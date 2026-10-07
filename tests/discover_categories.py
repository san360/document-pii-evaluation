"""Discover which PII categories each api-version accepts, per API.

The service rejects an unsupported category with a 400 naming exactly one
offender. Rather than probing 176 categories individually, send them all, drop
whichever the service names, and repeat — so the request count is proportional
to the number of *invalid* categories rather than the total.

The two APIs do not agree: Text PII accepts the full taxonomy on every
api-version, while Document PII rejects preview-only categories on older ones.
That disagreement is the whole reason this script exists, and why the result is
stored per API rather than as one list.

Writes back into app/data/entity_categories.json. Re-run when adding an
api-version to the UI. Needs live credentials.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from app.services.blob import BlobStager

from app.config import BASE_DIR, Settings, get_settings

TEXT_API_VERSIONS: list[str] = []  # Text PII is out of scope
DOCUMENT_API_VERSIONS = ["2026-05-01", "2026-05-15-preview"]
SAMPLE = "Contact Jordan Avery on 415-555-2671 or jordan@contoso.com."

INVALID_PATTERN = re.compile(r"Invalid value for parameter 'piiCategories': '([^']+)'")
# Document PII reports several at once, in a different sentence:
#   'VIN,CASocialIdentificationNumber' is not a valid value to be used in 'piiCategories'
INVALID_LIST_PATTERN = re.compile(
    r"'([^']+)' is not a valid value to be used in 'piiCategories'"
)


def _named_invalid(message: str) -> list[str]:
    """Every category the service named as invalid, across both error formats."""
    match = INVALID_PATTERN.search(message)
    if match:
        return [match.group(1)]
    match = INVALID_LIST_PATTERN.search(message)
    if match:
        return [part.strip() for part in match.group(1).split(",") if part.strip()]
    return []


def _error_text(response: httpx.Response) -> str:
    try:
        error = response.json().get("error", {})
        inner = (error.get("innererror") or {}).get("message", "")
        return f"{error.get('message', '')} {inner}"
    except ValueError:
        return response.text


async def _narrow(
    client: httpx.AsyncClient, url: str, headers: dict, build_body, categories: list[str], label: str
) -> tuple[set[str], int]:
    remaining = list(categories)
    rejected: set[str] = set()
    requests = 0

    while remaining:
        response = await client.post(url, headers=headers, json=build_body(remaining))
        requests += 1
        if response.status_code < 400:
            return rejected, requests

        message = _error_text(response)
        named = _named_invalid(message)
        if not named:
            print(f"    [{label}] unexpected error, stopping: {message[:150]}")
            return rejected, requests

        progressed = False
        for bad in named:
            if bad in remaining:
                rejected.add(bad)
                remaining.remove(bad)
                progressed = True
        if not progressed:
            print(f"    [{label}] named {named}, none in the list; stopping")
            return rejected, requests

    return rejected, requests


async def probe_text(client, settings: Settings, version: str, tags: list[str]) -> set[str]:
    url = settings.language_url("language/:analyze-text", version)
    headers = {
        "Ocp-Apim-Subscription-Key": settings.language_key,
        "Content-Type": "application/json",
    }

    def build(categories: list[str]) -> dict:
        return {
            "kind": "PiiEntityRecognition",
            "parameters": {
                "modelVersion": "latest",
                "stringIndexType": "UnicodeCodePoint",
                "piiCategories": categories,
            },
            "analysisInput": {"documents": [{"id": "1", "language": "en", "text": SAMPLE}]},
        }

    rejected, requests = await _narrow(client, url, headers, build, tags, version)
    print(f"    {len(tags) - len(rejected)} accepted, {len(rejected)} rejected, {requests} requests")
    if rejected:
        print(f"    rejected: {', '.join(sorted(rejected))}")
    return rejected


async def probe_document(
    client, settings: Settings, version: str, tags: list[str], source_url: str, target_url: str
) -> set[str]:
    url = settings.language_url("language/analyze-documents/jobs", version)
    headers = {
        "Ocp-Apim-Subscription-Key": settings.language_key,
        "Content-Type": "application/json",
    }

    def build(categories: list[str]) -> dict:
        parameters: dict = {
            "modelVersion": "latest",
            "stringIndexType": "UnicodeCodePoint",
            "piiCategories": categories,
        }
        if version >= "2026":
            parameters["redactionPolicies"] = [{"policyKind": "characterMask", "isDefault": True}]
        else:
            parameters["redactionPolicy"] = {"policyKind": "characterMask"}
        return {
            "displayName": "category probe",
            "analysisInput": {
                "documents": [
                    {
                        "id": "1",
                        "language": "en",
                        "source": {"kind": "AzureBlob", "location": source_url},
                        "target": {"kind": "AzureContainer", "location": target_url},
                    }
                ]
            },
            "tasks": [{"kind": "PiiEntityRecognition", "taskName": "probe", "parameters": parameters}],
        }

    rejected, requests = await _narrow(client, url, headers, build, tags, version)
    print(f"    {len(tags) - len(rejected)} accepted, {len(rejected)} rejected, {requests} requests")
    if rejected:
        print(f"    rejected: {', '.join(sorted(rejected))}")
    return rejected


async def main() -> None:
    settings = get_settings()
    path = BASE_DIR / "data" / "entity_categories.json"
    categories = json.loads(path.read_text(encoding="utf-8"))
    tags = [c["tag"] for c in categories]
    print(f"probing {len(tags)} categories\n")

    text_supported: dict[str, list[str]] = {tag: [] for tag in tags}
    document_supported: dict[str, list[str]] = {tag: [] for tag in tags}

    stager = BlobStager(settings)
    stager.ensure_containers()
    staged = stager.upload("category-probe.txt", b"Jordan Avery, 415-555-2671\n", "text/plain")

    async with httpx.AsyncClient(timeout=120.0) as client:
        for version in TEXT_API_VERSIONS:
            print(f"=== TEXT PII {version} ===")
            rejected = await probe_text(client, settings, version, tags)
            for tag in tags:
                if tag not in rejected:
                    text_supported[tag].append(version)
            print()

        for version in DOCUMENT_API_VERSIONS:
            print(f"=== DOCUMENT PII {version} ===")
            rejected = await probe_document(
                client, settings, version, tags, staged.source_sas_url, staged.target_sas_url
            )
            for tag in tags:
                if tag not in rejected:
                    document_supported[tag].append(version)
            print()

    stager.cleanup(["category-probe.txt"])

    for category in categories:
        tag = category["tag"]
        category["text_pii_api_versions"] = text_supported[tag]
        category["document_pii_api_versions"] = document_supported[tag]
        category.pop("api_versions", None)

    path.write_text(json.dumps(categories, indent=2), encoding="utf-8")
    print(f"wrote {path}\n")

    print("accepted counts:")
    for version in TEXT_API_VERSIONS:
        count = sum(1 for c in categories if version in c["text_pii_api_versions"])
        print(f"  text     {version}: {count}/{len(categories)}")
    for version in DOCUMENT_API_VERSIONS:
        count = sum(1 for c in categories if version in c["document_pii_api_versions"])
        print(f"  document {version}: {count}/{len(categories)}")


if __name__ == "__main__":
    asyncio.run(main())
