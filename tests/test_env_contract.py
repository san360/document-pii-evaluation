"""Contract test: the .env that scripts/write-env.ps1 generates must be exactly
what evaluate_document_pii.py reads.

These two drift silently. A renamed setting leaves the generator writing a key
nothing consumes, and the tool reports itself unconfigured right after a
successful deploy — with no error anywhere to explain why.

Needs no Azure credentials.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from shutil import which

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evaluate_document_pii as engine

ROOT = Path(__file__).resolve().parents[1]

# Every AZURE_* key the engine actually reads, by inspection of build_config().
CONSUMED = {
    "AZURE_LANGUAGE_ENDPOINT",
    "AZURE_LANGUAGE_KEY",
    "AZURE_DOCUMENT_PII_API_VERSION",
    "AZURE_STORAGE_CONNECTION_STRING",
    "AZURE_STORAGE_ACCOUNT_URL",
    "AZURE_STORAGE_ACCOUNT_NAME",
    "AZURE_STORAGE_SOURCE_CONTAINER",
    "AZURE_STORAGE_TARGET_CONTAINER",
    "AZURE_USE_ENTRA_ID",
}

passed = failed = 0


def check(label: str, actual, expected) -> None:
    global passed, failed
    if actual == expected:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}\n        expected {expected!r}\n        actual   {actual!r}")


def check_true(label: str, value) -> None:
    check(label, bool(value), True)


def env_keys(text: str) -> set[str]:
    return {
        match.group(1)
        for line in text.splitlines()
        if (match := re.match(r"^([A-Z][A-Z0-9_]*)=", line.strip()))
    }


def generate_env() -> str:
    """Run the real generator against a resource group that does not exist."""
    shell = "pwsh" if which("pwsh") else "powershell"
    with tempfile.TemporaryDirectory() as directory:
        out = Path(directory) / "generated.env"
        result = subprocess.run(
            [
                shell, "-NoProfile", "-File",
                str(ROOT / "scripts" / "write-env.ps1"),
                "-ResourceGroup", "rg-contract-test-does-not-exist",
                "-OutputPath", str(out),
            ], check=False,
            capture_output=True, text=True, timeout=300, cwd=str(ROOT),
        )
        if not out.exists():
            raise RuntimeError(
                f"write-env.ps1 produced no file.\nstdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        return out.read_text(encoding="utf-8")


def with_env(values: dict[str, str]):
    """Run build_config against a controlled environment, ignoring the real .env."""
    saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("AZURE_")}
    os.environ.update(values)
    try:
        return engine.build_config(
            auth="auto", env_file=Path("does-not-exist-on-purpose.env")
        )
    finally:
        for key in list(os.environ):
            if key.startswith("AZURE_"):
                del os.environ[key]
        os.environ.update(saved)


def main() -> int:
    print("\n.env.example contract")
    example_keys = env_keys((ROOT / ".env.example").read_text(encoding="utf-8"))
    # ACCOUNT_NAME is derived from the connection string, so it is optional.
    required = CONSUMED - {"AZURE_STORAGE_ACCOUNT_NAME"}
    check(".env.example has no keys the tool ignores", sorted(example_keys - CONSUMED), [])
    check(".env.example covers every consumed setting", sorted(required - example_keys), [])

    print("\nGenerated .env contract")
    generated = generate_env()
    generated_keys = env_keys(generated)
    check("generated .env has no keys the tool ignores", sorted(generated_keys - CONSUMED), [])
    check("generated .env covers every consumed setting", sorted(required - generated_keys), [])
    check("generated and example agree", sorted(generated_keys ^ example_keys), [])

    print("\nGenerated .env is well formed")
    check_true("no stray carriage returns", "\r" not in generated.replace("\r\n", "\n"))
    check_true("ends with a newline", generated.endswith("\n"))
    check_true("records where it came from", "write-env.ps1" in generated)

    print("\nbuild_config reads a populated environment")
    config = with_env({
        "AZURE_LANGUAGE_ENDPOINT": "https://lang-test.cognitiveservices.azure.com/",
        "AZURE_LANGUAGE_KEY": "test-key",
        "AZURE_DOCUMENT_PII_API_VERSION": "2026-05-01",
        "AZURE_STORAGE_CONNECTION_STRING":
            "DefaultEndpointsProtocol=https;AccountName=sttest;AccountKey=abc==;"
            "EndpointSuffix=core.windows.net",
        "AZURE_STORAGE_SOURCE_CONTAINER": "pii-source",
        "AZURE_STORAGE_TARGET_CONTAINER": "pii-target",
    })
    check("endpoint parsed", config.language_endpoint, "https://lang-test.cognitiveservices.azure.com/")
    check("api-version honoured", config.api_version, "2026-05-01")
    check("account derived from the connection string", config.storage_account, "sttest")
    check("auth resolves to key when one is present", config.auth, "key")
    check_true("jobs url is the Document PII endpoint",
               config.jobs_url.endswith(
                   "/language/analyze-documents/jobs?api-version=2026-05-01"))

    print("\nAn unusable environment fails loudly")
    try:
        with_env({"AZURE_STORAGE_CONNECTION_STRING": "x"})
        check("missing endpoint raises ConfigError", "no error", "ConfigError")
    except engine.ConfigError as exc:
        check_true("missing endpoint raises ConfigError", "AZURE_LANGUAGE_ENDPOINT" in str(exc))

    print("\nCategory support matrix")
    categories = engine.entity_categories()
    check_true("taxonomy is loaded", len(categories) > 100)
    accepted, rejected = engine.filter_categories(
        "2026-05-01", ["Person", "VIN", "Email"])
    check("2026-05-01 rejects VIN", rejected, ["VIN"])
    check("and keeps the rest", accepted, ["Person", "Email"])
    accepted, rejected = engine.filter_categories(
        "2026-05-01", ["Person", "DateOfBirth", "Email"])
    check("2026-05-01 accepts DateOfBirth", rejected, [])
    accepted, rejected = engine.filter_categories(
        "2026-05-15-preview", ["Person", "KRSocialSecurityNumber"])
    check("2026-05-15-preview rejects KRSocialSecurityNumber",
          rejected, ["KRSocialSecurityNumber"])
    accepted, rejected = engine.filter_categories(
        "9999-01-01", ["Person", "DateOfBirth"])
    check("an unprobed version is passed through", rejected, [])

    print("\nPreview-only features are gated in the engine")
    plain = engine.EvalOptions(policy="characterMask")
    synthetic = engine.EvalOptions(policy="syntheticReplacement",
                                   preserve_data_format=True)
    with_synonyms = engine.EvalOptions(
        entity_synonyms=[{"entityType": "Person", "synonyms": ["Insured"]}])

    engine.check_preview_features(plain, "2026-05-01")
    check_true("a GA policy passes on GA", True)
    engine.check_preview_features(synthetic, "2026-05-15-preview")
    check_true("syntheticReplacement passes on preview", True)

    for label, options in (("syntheticReplacement", synthetic),
                           ("entitySynonyms", with_synonyms)):
        try:
            engine.check_preview_features(options, "2026-05-01")
            check(f"{label} is refused on GA", "allowed", "refused")
        except engine.ConfigError as exc:
            check_true(f"{label} is refused on GA, naming the fix",
                       "2026-05-15-preview" in str(exc))

    body = engine.task_parameters(synthetic, "2026-05-15-preview")
    policy = body["redactionPolicies"][0]
    check("syntheticReplacement sends its policy kind",
          policy["policyKind"], "syntheticReplacement")
    check("and preserveDataFormat", policy["preserveDataFormat"], True)
    check_true("and no redactionCharacter", "redactionCharacter" not in policy)

    body = engine.task_parameters(
        engine.EvalOptions(language="it",
                           entity_synonyms=[{"entityType": "Person",
                                             "synonyms": ["Assicurato", " "]}]),
        "2026-05-15-preview")
    group = body["entitySynonyms"][0]
    check("synonyms carry the entity type", group["entityType"], "Person")
    check("blank synonyms are dropped", len(group["synonyms"]), 1)
    check("language is the 2-letter code", group["synonyms"][0]["language"], "it")

    check("empty synonym groups are omitted entirely",
          "entitySynonyms" in engine.task_parameters(plain, "2026-05-15-preview"),
          False)
    check_true("every synonym entity type is a real category",
               set(engine.SYNONYM_ENTITY_TYPES).issubset(
                   {c.get("tag") for c in categories}))

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
