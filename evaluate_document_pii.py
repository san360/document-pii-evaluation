#!/usr/bin/env python3
"""Evaluate the Azure AI Language **Document PII** API against real PDFs.

Scope, deliberately narrow:

* The only Azure call made here is ``analyze-documents/jobs`` (plus the blob
  upload it requires and the artifact download it produces).
* **No local document processing.** No OCR, no text extraction, no PyMuPDF
  redaction, no Text PII. Whatever the service returns is the answer.

The question this answers: does Document PII handle a searchable (digital) PDF
and a scanned image-only PDF, and how do the results differ?

Two submission modes, because they test different things:

* ``per-document`` (default) — one job per file. Isolates failures, so one bad
  document cannot mask the others. This is what you want for evaluation.
* ``batch`` — every file in a single job, the way the API is designed to be
  used and the way most sample code does it. Use this to find out whether one
  unprocessable document takes the whole batch down with it.

Usage
-----
    python evaluate_document_pii.py --input docs
    python evaluate_document_pii.py --input docs --mode batch
    python evaluate_document_pii.py --input docs --api-version 2026-05-01
    python evaluate_document_pii.py --input docs --policy entityMask --auth entra

Configuration is read from .env (the same file the web app uses) or the
environment; every value can be overridden on the command line.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import requests
from azure.storage.blob import (
    BlobSasPermissions,
    BlobServiceClient,
    ContainerSasPermissions,
    generate_blob_sas,
    generate_container_sas,
)

ROOT = Path(__file__).resolve().parent
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"
CATEGORY_DATA = ROOT / "data" / "entity_categories.json"

# A Windows console defaults to cp1252, which cannot encode the typography used
# in this script's output and raises UnicodeEncodeError mid-run. Reconfiguring
# is better than stripping the characters: it keeps the output readable where
# the terminal supports it, and degrades to "?" where it does not.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# The service rejects a job whose SAS expires inside 24 hours, with a generic
# "Invalid analysisInput in request / The request body is invalid or malformed"
# that names neither the SAS nor the expiry. An analyze-documents job carries
# its own 24-hour expirationDateTime, so the credentials have to outlive it.
# Measured: 23h rejected, 24h accepted. Two days leaves room to spare.
SAS_LIFETIME = timedelta(days=2)

POLL_INTERVAL_SECONDS = 5.0
MAX_POLL_SECONDS = 600.0
TERMINAL_STATES = {"succeeded", "failed", "cancelled", "canceled", "partiallycompleted"}

# Redaction policies. The first three are accepted by every supported
# api-version; syntheticReplacement exists only in the preview. Sending a
# preview-only policy to the GA version fails the whole request, so the gate
# lives here rather than in the UI — the CLI has to get the same answer.
POLICY_KINDS = ["characterMask", "entityMask", "noMask", "syntheticReplacement"]
PREVIEW_ONLY_POLICIES = frozenset({"syntheticReplacement"})
PREVIEW_API_VERSIONS = frozenset({"2026-05-15-preview"})

# Entity types the service accepts in entitySynonyms. Narrower than the 177
# detection categories — synonyms are context vocabulary, and the service only
# applies them to these.
SYNONYM_ENTITY_TYPES = [
    "Address", "Age", "CreditCardNumber", "Date", "DateOfBirth", "Email",
    "Organization", "Person", "PersonType", "PhoneNumber", "URL", "USSocialSecurityNumber",
]


# ---------------------------------------------------------------- config ----
def load_dotenv(path: Path) -> None:
    """Minimal .env reader — avoids a dependency and avoids surprises."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


@dataclass
class EvalOptions:
    """Everything that shapes a submission.

    The CLI builds one of these from argparse; the web router builds one from a
    form post. Both then call `run_evaluation`, so there is exactly one
    implementation of the request and no way for the two surfaces to drift —
    which is what previously made an identical document appear to behave
    differently depending on how it was submitted.
    """

    mode: str = "per-document"  # "per-document" | "batch"
    policy: str = "characterMask"
    redaction_character: str = "*"
    language: str = "en-US"
    domain: str = "none"
    categories: list[str] | None = None
    keep_uploads: bool = False

    # Preview-only (2026-05-15-preview). preserve_data_format applies to the
    # syntheticReplacement policy; entity_synonyms is held in a friendly shape
    # — [{"entityType": "Person", "synonyms": ["Insured", "Applicant"]}] — and
    # expanded to the wire format in task_parameters().
    preserve_data_format: bool = False
    entity_synonyms: list[dict] | None = None


@dataclass
class Config:
    language_endpoint: str
    language_key: str
    storage_account: str
    storage_connection_string: str
    source_container: str
    target_container: str
    api_version: str
    auth: str  # "key" | "entra"

    @property
    def jobs_url(self) -> str:
        root = self.language_endpoint.rstrip("/")
        return f"{root}/language/analyze-documents/jobs?api-version={self.api_version}"


def build_config(
    args: argparse.Namespace | None = None,
    *,
    endpoint: str = "",
    storage_account: str = "",
    source_container: str = "",
    target_container: str = "",
    api_version: str = "",
    auth: str = "auto",
    env_file: Path | None = None,
) -> Config:
    """Resolve configuration from .env, the environment and explicit overrides.

    ``env_file`` defaults to ``.env`` beside this script. Pass an explicit path
    — or a path that does not exist — to read the environment alone, which is
    what the contract test needs to exercise the unconfigured case.
    """
    if args is not None:
        endpoint = endpoint or getattr(args, "endpoint", "")
        storage_account = storage_account or getattr(args, "storage_account", "")
        source_container = source_container or getattr(args, "source_container", "")
        target_container = target_container or getattr(args, "target_container", "")
        api_version = api_version or getattr(args, "api_version", "")
        auth = getattr(args, "auth", auth)

    load_dotenv(ROOT / ".env" if env_file is None else env_file)

    endpoint = endpoint or os.environ.get("AZURE_LANGUAGE_ENDPOINT", "")
    key = os.environ.get("AZURE_LANGUAGE_KEY", "")
    connection = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
    account = storage_account or os.environ.get("AZURE_STORAGE_ACCOUNT_NAME", "")

    if not account and connection:
        for part in connection.split(";"):
            if part.startswith("AccountName="):
                account = part.split("=", 1)[1]

    if auth == "auto":
        # Honour the documented flag. Without this, a user who sets
        # AZURE_USE_ENTRA_ID=true but still has a key in .env — exactly what
        # write-env.ps1 produces on a re-run — silently keeps using the key
        # while believing they have moved off it.
        prefer_entra = os.environ.get("AZURE_USE_ENTRA_ID", "").strip().lower() in {
            "true", "1", "yes",
        }
        auth = "entra" if prefer_entra or not key else "key"

    if not endpoint:
        fail("AZURE_LANGUAGE_ENDPOINT is not set. Put it in .env or pass --endpoint.")
    if auth == "key" and not key:
        fail("AZURE_LANGUAGE_KEY is not set. Use --auth entra to sign in with az login.")
    if not account:
        fail("No storage account. Set AZURE_STORAGE_CONNECTION_STRING or pass --storage-account.")

    return Config(
        language_endpoint=endpoint,
        language_key=key,
        storage_account=account,
        storage_connection_string=connection,
        source_container=source_container
        or os.environ.get("AZURE_STORAGE_SOURCE_CONTAINER", "pii-source"),
        target_container=target_container
        or os.environ.get("AZURE_STORAGE_TARGET_CONTAINER", "pii-target"),
        api_version=api_version
        or os.environ.get("AZURE_DOCUMENT_PII_API_VERSION", "2026-05-01"),
        auth=auth,
    )


class ConfigError(RuntimeError):
    """Configuration is missing or unusable."""


def fail(message: str) -> None:
    """Abort. Raises in-process so the web app can report it; exits in the CLI."""
    if __name__ == "__main__":
        print(f"\nERROR: {message}", file=sys.stderr)
        sys.exit(1)
    raise ConfigError(message)


# ------------------------------------------------------------ categories ----
@lru_cache
def entity_categories() -> list[dict]:
    """The PII taxonomy, with the api-versions each category is accepted by.

    Measured against a live resource by tests/discover_categories.py — the
    support lists are not from documentation.
    """
    if not CATEGORY_DATA.exists():
        return []
    return json.loads(CATEGORY_DATA.read_text(encoding="utf-8"))


@lru_cache
def _probed_versions() -> frozenset[str]:
    versions: set[str] = set()
    for category in entity_categories():
        versions.update(category.get("document_pii_api_versions", []))
    return frozenset(versions)


def filter_categories(api_version: str, selected: list[str]) -> tuple[list[str], list[str]]:
    """Split ``selected`` into (accepted, rejected) for one api-version.

    Document PII rejects the **entire request** over one unsupported category.
    Of the 177 in the taxonomy both supported versions refuse the same 3:
    CASocialIdentificationNumber, KRSocialSecurityNumber and VIN. A request
    that fails for this reason returns a 400 naming the offender, which is
    recoverable — but only if you notice before blaming the document.

    An api-version that was never probed is passed through untouched rather
    than guessed at, and so is a category absent from the taxonomy.
    """
    if not selected:
        return [], []
    if api_version not in _probed_versions():
        return list(selected), []

    known = {
        category["tag"]: category.get("document_pii_api_versions", [])
        for category in entity_categories()
    }
    accepted: list[str] = []
    rejected: list[str] = []
    for tag in selected:
        versions = known.get(tag)
        if versions is None or api_version in versions:
            accepted.append(tag)
        else:
            rejected.append(tag)
    return accepted, rejected


# ------------------------------------------------------------------ auth ----
def language_headers(config: Config) -> dict[str, str]:
    if config.auth == "key":
        return {
            "Ocp-Apim-Subscription-Key": config.language_key,
            "Content-Type": "application/json",
        }
    from azure.identity import DefaultAzureCredential

    token = DefaultAzureCredential().get_token(COGNITIVE_SCOPE).token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def blob_service(config: Config) -> BlobServiceClient:
    if config.storage_connection_string:
        return BlobServiceClient.from_connection_string(config.storage_connection_string)
    from azure.identity import DefaultAzureCredential

    return BlobServiceClient(
        account_url=f"https://{config.storage_account}.blob.core.windows.net",
        credential=DefaultAzureCredential(),
    )


def sas_credentials(service: BlobServiceClient) -> dict:
    """Account key when one is available, user delegation key under Entra ID."""
    account_key = getattr(service.credential, "account_key", None)
    if account_key:
        return {"account_key": account_key}
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    return {
        "user_delegation_key": service.get_user_delegation_key(now, now + SAS_LIFETIME)
    }


# --------------------------------------------------------------- corpus -----
@dataclass
class Document:
    path: Path
    blob_name: str
    size_bytes: int
    kind: str = "unknown"  # "searchable" | "scanned" | "unknown" | "n/a"


def classify_pdf(path: Path) -> str:
    """Label the INPUT as searchable or scanned.

    This is corpus description, not processing: it is reported alongside the
    result so "it failed" can be read as "it failed on a scan". It plays no
    part in the API call, and the evaluation still runs without it.
    """
    if path.suffix.lower() != ".pdf":
        return "n/a"
    try:
        import pymupdf
    except ImportError:
        return "unknown"
    try:
        with pymupdf.open(path) as doc:
            for index in range(min(5, doc.page_count)):
                if doc[index].get_text("text").strip():
                    return "searchable"
        return "scanned"
    except Exception:  # noqa: BLE001 - an unparseable PDF is simply "unknown"
        return "unknown"


def collect_documents(input_dir: Path, run_id: str) -> list[Document]:
    if not input_dir.is_dir():
        fail(f"{input_dir} is not a directory.")
    files = sorted(
        f
        for f in input_dir.iterdir()
        if f.suffix.lower() in SUPPORTED_EXTENSIONS and not f.name.startswith("~$")
    )
    if not files:
        fail(f"No {'/'.join(sorted(SUPPORTED_EXTENSIONS))} files found in {input_dir}/.")
    return [
        Document(
            path=f,
            blob_name=f"{run_id}/{f.name}",
            size_bytes=f.stat().st_size,
            kind=classify_pdf(f),
        )
        for f in files
    ]


# ------------------------------------------------------------- the API ------
def upload(
    service: BlobServiceClient, config: Config, documents: list[Document], say=None
) -> None:
    say = say or (lambda message: None)
    container = service.get_container_client(config.source_container)
    try:
        container.create_container()
    except Exception as exc:  # noqa: BLE001 - SDK raises varied types; the message is the stable signal
        if "ContainerAlreadyExists" not in str(exc):
            fail(f"Cannot reach container '{config.source_container}': {exc}")
    try:
        service.get_container_client(config.target_container).create_container()
    except Exception as exc:  # noqa: BLE001 - as above
        if "ContainerAlreadyExists" not in str(exc):
            fail(f"Cannot reach container '{config.target_container}': {exc}")

    for document in documents:
        say(f"    uploading {document.path.name} ({document.size_bytes/1024:.0f} KB)")
        with document.path.open("rb") as handle:
            container.upload_blob(document.blob_name, handle, overwrite=True)


def source_sas_url(service, config: Config, blob_name: str, credentials: dict) -> str:
    token = generate_blob_sas(
        account_name=config.storage_account,
        container_name=config.source_container,
        blob_name=blob_name,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + SAS_LIFETIME,
        **credentials,
    )
    encoded = quote(blob_name, safe="/")
    return (
        f"https://{config.storage_account}.blob.core.windows.net/"
        f"{config.source_container}/{encoded}?{token}"
    )


def target_sas_url(config: Config, credentials: dict) -> str:
    token = generate_container_sas(
        account_name=config.storage_account,
        container_name=config.target_container,
        permission=ContainerSasPermissions(read=True, write=True, list=True, create=True),
        expiry=datetime.now(timezone.utc) + SAS_LIFETIME,
        **credentials,
    )
    return (
        f"https://{config.storage_account}.blob.core.windows.net/"
        f"{config.target_container}?{token}"
    )


def synonym_language(language: str) -> str:
    """The 2-letter ISO 639-1 code entitySynonyms wants, from 'en-US' etc."""
    code = (language or "en").replace("_", "-").split("-")[0].strip().lower()
    return code[:2] or "en"


def check_preview_features(options: EvalOptions, api_version: str) -> None:
    """Refuse preview-only options on a GA api-version, before submitting.

    These are not dropped with a warning the way unsupported categories are. A
    silently downgraded policy would redact the document differently from what
    was asked without saying so, and silently dropped synonyms would look like
    a feature that simply does not work. Both are better as a refusal that
    names the fix.
    """
    if api_version in PREVIEW_API_VERSIONS:
        return
    preview = min(PREVIEW_API_VERSIONS)
    if options.policy in PREVIEW_ONLY_POLICIES:
        fail(
            f"The '{options.policy}' redaction policy requires api-version "
            f"{preview}; {api_version} accepts only "
            f"{', '.join(k for k in POLICY_KINDS if k not in PREVIEW_ONLY_POLICIES)}."
        )
    if options.entity_synonyms:
        fail(
            f"entitySynonyms requires api-version {preview}; {api_version} "
            "does not accept it."
        )


def task_parameters(options: EvalOptions, api_version: str) -> dict:
    """Build PiiTaskParameters.

    Only the 2026 api-versions are supported, which take a ``redactionPolicies``
    array. The retired 2024-11-15-preview took a singular ``redactionPolicy``
    object and detected markedly less — 3 entities against 10 on the same
    scanned page.
    """
    policy: dict = {"policyKind": options.policy}
    if options.policy == "characterMask":
        policy["redactionCharacter"] = options.redaction_character
    elif options.policy == "syntheticReplacement":
        policy["preserveDataFormat"] = bool(options.preserve_data_format)

    parameters: dict = {"redactionPolicies": [{**policy, "isDefault": True}]}
    if options.categories:
        parameters["piiCategories"] = options.categories
    if options.domain != "none":
        parameters["domain"] = options.domain

    groups = []
    for group in options.entity_synonyms or []:
        words = [w.strip() for w in group.get("synonyms", []) if str(w).strip()]
        if not group.get("entityType") or not words:
            continue
        language = synonym_language(options.language)
        groups.append({
            "entityType": group["entityType"],
            "synonyms": [{"synonym": w, "language": language} for w in words],
        })
    if groups:
        parameters["entitySynonyms"] = groups
    return parameters


def submit(
    config: Config,
    options: EvalOptions,
    documents: list[Document],
    service,
    credentials: dict,
) -> tuple[str, dict]:
    target = target_sas_url(config, credentials)
    payload = {
        "displayName": f"Document PII evaluation ({options.mode})",
        "analysisInput": {
            "documents": [
                {
                    "id": f"doc-{index}",
                    "language": options.language,
                    "source": {"location": source_sas_url(service, config, d.blob_name, credentials)},
                    "target": {"location": target},
                }
                for index, d in enumerate(documents)
            ]
        },
        "tasks": [
            {
                "kind": "PiiEntityRecognition",
                "taskName": "Redact-PII",
                "parameters": task_parameters(options, config.api_version),
            }
        ],
    }

    response = requests.post(
        config.jobs_url, headers=language_headers(config), json=payload, timeout=90
    )
    if response.status_code not in (200, 202):
        raise RuntimeError(f"submit failed: HTTP {response.status_code} — {response.text[:600]}")

    operation = response.headers.get("operation-location")
    if not operation:
        raise RuntimeError("submit returned no operation-location header")
    return operation, payload


def poll(config: Config, operation_url: str, on_status=None) -> dict:
    headers = language_headers(config)
    headers.pop("Content-Type", None)
    started = time.time()
    while True:
        elapsed = time.time() - started
        if elapsed > MAX_POLL_SECONDS:
            raise RuntimeError(f"job did not finish within {MAX_POLL_SECONDS:.0f}s")
        response = requests.get(operation_url, headers=headers, timeout=60)
        if response.status_code >= 400:
            raise RuntimeError(f"poll failed: HTTP {response.status_code} — {response.text[:400]}")
        body = response.json()
        status = str(body.get("status", "")).lower()
        if on_status:
            on_status(status, elapsed)
        if status in TERMINAL_STATES:
            return body
        time.sleep(POLL_INTERVAL_SECONDS)


def run_evaluation(
    config: Config,
    options: EvalOptions,
    documents: list[Document],
    out_root: Path,
    progress=None,
) -> list[DocumentOutcome]:
    """Submit, poll and collect. The one implementation both surfaces call.

    ``progress(message)`` is called with human-readable lines as it goes; the
    CLI prints them, the web app streams them to the browser.
    """
    say = progress or (lambda message: None)

    check_preview_features(options, config.api_version)

    if options.categories:
        accepted, rejected = filter_categories(config.api_version, options.categories)
        if rejected:
            say(
                f"{len(rejected)} category/categories are not accepted by "
                f"{config.api_version} and were dropped: {', '.join(rejected)}"
            )
            options = replace(options, categories=accepted or None)

    service = blob_service(config)
    credentials = sas_credentials(service)

    say("Uploading to blob storage")
    upload(service, config, documents, say)

    outcomes: list[DocumentOutcome] = []
    groups = [[d] for d in documents] if options.mode == "per-document" else [documents]

    for index, group in enumerate(groups, start=1):
        label = group[0].path.name if len(group) == 1 else f"{len(group)} documents"
        say(f"[{index}/{len(groups)}] submitting {label}")
        started = time.time()
        try:
            operation, _ = submit(config, options, group, service, credentials)
            job = poll(
                config,
                operation,
                on_status=lambda status, elapsed: say(f"    {status} ({elapsed:.0f}s)"),
            )
        except RuntimeError as exc:
            elapsed = time.time() - started
            say(f"    FAILED — {exc}")
            outcomes.extend(
                DocumentOutcome(
                    filename=d.path.name,
                    kind=d.kind,
                    size_bytes=d.size_bytes,
                    status="failed",
                    error=str(exc),
                    duration_seconds=round(elapsed, 1),
                )
                for d in group
            )
            continue

        elapsed = time.time() - started
        (out_root / f"job-{index:02d}-status.json").write_text(
            json.dumps(job, indent=2), encoding="utf-8"
        )
        group_outcomes = harvest(job, group, service, config, out_root)
        for outcome in group_outcomes:
            outcome.duration_seconds = round(elapsed, 1)
            say(
                f"    {outcome.filename}: {outcome.status}"
                + (f" — {outcome.entity_count} entities" if outcome.entity_count else "")
            )
        outcomes.extend(group_outcomes)

    if not options.keep_uploads:
        container = service.get_container_client(config.source_container)
        stranded = []
        for document in documents:
            try:
                container.delete_blob(document.blob_name)
            except Exception as exc:  # noqa: BLE001 - report every failure, whatever the SDK raises
                stranded.append(f"{document.blob_name} ({exc.__class__.__name__})")
        if stranded:
            # Staying silent here would leave a copy of someone's document in
            # blob storage with nothing in the run to say so.
            say(
                f"WARNING: {len(stranded)} staged source blob(s) could not be "
                f"deleted and are still in '{config.source_container}': "
                + ", ".join(stranded)
            )

    return outcomes


def write_summary(
    out_root: Path,
    run_id: str,
    config: Config,
    options: EvalOptions,
    outcomes: list[DocumentOutcome],
) -> dict:
    summary = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": config.language_endpoint,
        "api_version": config.api_version,
        "auth": config.auth,
        "mode": options.mode,
        "policy": options.policy,
        "preserve_data_format": options.preserve_data_format if options.policy == "syntheticReplacement" else None,
        "entity_synonyms": options.entity_synonyms or None,
        "language": options.language,
        "domain": options.domain,
        "categories": options.categories,
        "documents": [asdict(o) for o in outcomes],
    }
    (out_root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


# ----------------------------------------------------------- the result -----
@dataclass
class DocumentOutcome:
    filename: str
    kind: str
    size_bytes: int
    status: str = "unknown"
    error: str = ""
    job_id: str = ""
    duration_seconds: float = 0.0
    entity_count: int = 0
    categories: dict[str, int] = field(default_factory=dict)
    redacted_artifact: str = ""
    result_artifact: str = ""
    source_artifact: str = ""
    warnings: list[str] = field(default_factory=list)


def download_artifacts(
    service, config: Config, locations: list[str], out_dir: Path
) -> tuple[str, str, dict | None]:
    """Fetch whatever the service wrote. Returns (redacted name, json name, parsed)."""
    container = service.get_container_client(config.target_container)
    prefix = f"https://{config.storage_account}.blob.core.windows.net/{config.target_container}/"
    redacted_name = result_name = ""
    parsed: dict | None = None

    for location in locations:
        path = location.split("?", 1)[0]
        if not path.startswith(prefix):
            continue
        blob_name = path[len(prefix) :]
        data = container.download_blob(blob_name).readall()
        local = out_dir / Path(blob_name).name
        local.write_bytes(data)
        if blob_name.lower().endswith(".json"):
            result_name = local.name
            try:
                parsed = json.loads(data.decode("utf-8-sig"))
            except ValueError:
                parsed = None
        else:
            redacted_name = local.name
    return redacted_name, result_name, parsed


def summarise_entities(result_json: dict | None) -> tuple[int, dict[str, int]]:
    """Count entities by type.

    The result artifact has no published schema. Observed shape (live,
    confirmed on 2026-05-01): entities[] with `text`, `type`, `entityId`,
    `mask`, `length`, `confidenceScore`, `tags[]` — note `type`, not
    `category`, and no offset at all.
    """
    if not result_json:
        return 0, {}
    entities = result_json.get("entities")
    if not isinstance(entities, list):
        return 0, {}
    counts: dict[str, int] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        tags = [t.get("name") for t in entity.get("tags", []) or [] if isinstance(t, dict)]
        category = entity.get("type") or entity.get("category") or (tags[0] if tags else "Unknown")
        counts[category] = counts.get(category, 0) + 1
    return len(entities), dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def harvest(
    job: dict, documents: list[Document], service, config: Config, out_root: Path
) -> list[DocumentOutcome]:
    """Turn a finished job into one outcome per submitted document."""
    outcomes = {
        f"doc-{index}": DocumentOutcome(
            filename=d.path.name, kind=d.kind, size_bytes=d.size_bytes
        )
        for index, d in enumerate(documents)
    }
    job_id = job.get("jobId", "")
    job_status = str(job.get("status", "")).lower()

    task_error = ""
    for item in (job.get("tasks") or {}).get("items", []) or []:
        if str(item.get("status", "")).lower() in {"failed", "cancelled", "canceled"}:
            task_error = json.dumps(job.get("errors") or item.get("errors") or [])[:400]

        results = item.get("results") or {}
        for error in results.get("errors", []) or []:
            doc_id = error.get("id") if isinstance(error, dict) else None
            message = json.dumps(error)[:300]
            if doc_id in outcomes:
                outcomes[doc_id].status = "failed"
                outcomes[doc_id].error = message
            else:
                task_error = task_error or message

        for document in results.get("documents", []) or []:
            doc_id = document.get("id", "")
            outcome = outcomes.get(doc_id)
            if outcome is None:
                continue
            outcome.job_id = job_id
            for warning in document.get("warnings", []) or []:
                text = warning.get("message") if isinstance(warning, dict) else str(warning)
                if text:
                    outcome.warnings.append(text)

            targets = document.get("targets") or document.get("target") or []
            if isinstance(targets, dict):
                targets = [targets]
            locations = [t.get("location", "") for t in targets if t.get("location")]

            # Path(...).name upstream strips separators, but the *stem* can
            # still be "..": Path("...pdf").stem == "..", which would walk the
            # output directory up one level and write the document outside its
            # own run folder.
            stem = Path(outcome.filename).stem.strip(". ") or "document"
            out_dir = out_root / stem
            out_dir.mkdir(parents=True, exist_ok=True)

            # Keep the source beside the output. The evaluation is then
            # self-contained and can be reviewed side by side later — the
            # uploads the web app stages are in a temp folder that is deleted
            # as soon as the run finishes.
            source = next(
                (d.path for d in documents if d.path.name == outcome.filename), None
            )
            if source and source.exists():
                copy_name = f"source{source.suffix.lower()}"
                try:
                    shutil.copyfile(source, out_dir / copy_name)
                    outcome.source_artifact = copy_name
                except OSError as exc:
                    logging.getLogger(__name__).debug("could not copy source: %s", exc)

            try:
                redacted, result_name, parsed = download_artifacts(
                    service, config, locations, out_dir
                )
            except Exception as exc:  # noqa: BLE001 - one bad document must not sink the run
                outcome.status = "failed"
                outcome.error = f"could not download artifacts: {exc}"
                continue

            outcome.redacted_artifact = redacted
            outcome.result_artifact = result_name
            outcome.entity_count, outcome.categories = summarise_entities(parsed)
            outcome.status = "succeeded" if redacted else "no-output"

    for outcome in outcomes.values():
        if outcome.status == "unknown":
            outcome.status = "failed" if job_status != "succeeded" else "no-output"
            outcome.error = outcome.error or task_error or f"job {job_status}"
        outcome.job_id = outcome.job_id or job_id

    return list(outcomes.values())


# ------------------------------------------------------------- reporting ----
def print_table(outcomes: list[DocumentOutcome]) -> None:
    name_width = max([len(o.filename) for o in outcomes] + [8])
    kind_width = max([len(o.kind) for o in outcomes] + [10])
    print()
    print(
        f"  {'document'.ljust(name_width)}  {'input'.ljust(kind_width)}  "
        f"{'result':<11} {'time':>7} {'PII':>5}  top categories"
    )
    print(f"  {'-' * name_width}  {'-' * kind_width}  {'-'*11} {'-'*7} {'-'*5}  {'-'*34}")
    for outcome in outcomes:
        mark = {"succeeded": "OK", "no-output": "NO OUTPUT"}.get(outcome.status, "FAILED")
        top = ", ".join(f"{k}:{v}" for k, v in list(outcome.categories.items())[:3]) or "-"
        print(
            f"  {outcome.filename.ljust(name_width)}  {outcome.kind.ljust(kind_width)}  "
            f"{mark:<11} {outcome.duration_seconds:>6.1f}s {outcome.entity_count:>5}  {top}"
        )
    print()
    for outcome in outcomes:
        if outcome.error:
            print(f"  ! {outcome.filename}: {outcome.error}")
    print()


def verdict(outcomes: list[DocumentOutcome]) -> None:
    """State plainly whether the input format predicted the result."""
    by_kind: dict[str, list[DocumentOutcome]] = {}
    for outcome in outcomes:
        by_kind.setdefault(outcome.kind, []).append(outcome)

    print("  By input format")
    for kind, group in sorted(by_kind.items()):
        ok = sum(1 for o in group if o.status == "succeeded")
        print(f"    {kind:<12} {ok}/{len(group)} succeeded")
    print()


def parse_synonyms(raw: list[str] | None) -> list[dict] | None:
    """Parse --synonyms 'Person=Insured,Applicant' into the friendly shape.

    Unknown entity types are refused rather than passed through: the service
    rejects the whole request over one bad value, and a typo here would surface
    as an opaque 400 about the request body.
    """
    if not raw:
        return None
    known = {t.lower(): t for t in SYNONYM_ENTITY_TYPES}
    groups: list[dict] = []
    for item in raw:
        name, sep, words = str(item).partition("=")
        if not sep:
            fail(f"--synonyms expects TYPE=word1,word2 — got {item!r}")
        canonical = known.get(name.strip().lower())
        if not canonical:
            fail(f"--synonyms: {name.strip()!r} is not a supported entity type. "
                 f"Choose from: {', '.join(SYNONYM_ENTITY_TYPES)}")
        values = [w.strip() for w in words.split(",") if w.strip()]
        if not values:
            fail(f"--synonyms: no words given for {canonical}")
        groups.append({"entityType": canonical, "synonyms": values})
    return groups or None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--input", type=Path, default=ROOT / "docs",
                        help="folder of documents to evaluate (default: docs/)")
    parser.add_argument("--output", type=Path, default=ROOT / "evaluations",
                        help="where to write results (default: evaluations/)")
    parser.add_argument("--mode", choices=["per-document", "batch"], default="per-document",
                        help="one job per file (default), or all files in one job")
    parser.add_argument("--api-version", default="",
                        help="2026-05-01 (default) or 2026-05-15-preview")
    parser.add_argument("--policy", default="characterMask", choices=POLICY_KINDS,
                        help="syntheticReplacement requires 2026-05-15-preview")
    parser.add_argument("--redaction-character", default="*")
    parser.add_argument("--preserve-data-format", action="store_true",
                        help="syntheticReplacement: keep the shape of the original value")
    parser.add_argument("--synonyms", nargs="*", default=None, metavar="TYPE=W1,W2",
                        help="context vocabulary, e.g. Person=Insured,Applicant "
                             "(requires 2026-05-15-preview)")
    parser.add_argument("--language", default="en-US")
    parser.add_argument("--domain", default="none", choices=["none", "phi"])
    parser.add_argument("--categories", nargs="*", default=None,
                        help="restrict to these PII categories (default: all)")
    parser.add_argument("--auth", choices=["auto", "key", "entra"], default="auto")
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--storage-account", default="")
    parser.add_argument("--source-container", default="")
    parser.add_argument("--target-container", default="")
    parser.add_argument("--keep-uploads", action="store_true",
                        help="do not delete the staged source blobs afterwards")
    args = parser.parse_args()

    config = build_config(args)
    options = EvalOptions(
        mode=args.mode,
        policy=args.policy,
        redaction_character=args.redaction_character,
        language=args.language,
        domain=args.domain,
        categories=args.categories,
        keep_uploads=args.keep_uploads,
        preserve_data_format=args.preserve_data_format,
        entity_synonyms=parse_synonyms(args.synonyms),
    )

    run_id = datetime.now(timezone.utc).strftime("eval-%Y-%m-%d_%H-%M-%S")
    out_root = args.output / run_id
    out_root.mkdir(parents=True, exist_ok=True)

    print("=" * 74)
    print("  Azure AI Language — Document PII API evaluation")
    print("=" * 74)
    print(f"  endpoint     {config.language_endpoint}")
    print(f"  api-version  {config.api_version}")
    print(f"  auth         {config.auth}")
    print(f"  storage      {config.storage_account} ({config.source_container} -> {config.target_container})")
    print(f"  mode         {options.mode}")
    print(f"  policy       {options.policy}")
    print(f"  output       {out_root}")

    documents = collect_documents(args.input, run_id)
    print(f"\n  {len(documents)} document(s) in {args.input}/")
    for document in documents:
        print(f"    - {document.path.name}  [{document.kind}]  {document.size_bytes/1024:.0f} KB")
    print()

    outcomes = run_evaluation(config, options, documents, out_root, progress=lambda m: print(f"  {m}"))

    print("\n" + "=" * 74)
    print_table(outcomes)
    verdict(outcomes)

    write_summary(out_root, run_id, config, options, outcomes)
    print(f"  Artifacts and summary.json written to {out_root}")

    failed = sum(1 for o in outcomes if o.status != "succeeded")
    return 1 if failed == len(outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())
