"""Document PII evaluation, exposed over HTTP.

This router does **not** reimplement anything. It uploads what the browser sent
to a temporary folder and hands it to ``evaluate_document_pii.run_evaluation``
— the same function the CLI calls, with the same ``EvalOptions``.

That is deliberate. The two surfaces previously built their own request bodies,
and when an identical document behaved differently depending on how it was
submitted, the difference was assumed to be in the request. It was not — but
finding that out cost a day. One implementation removes the question.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

import evaluate_document_pii as engine
from app.config import get_settings
from app.services.jobs import registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/evaluate", tags=["evaluate"])

PDF_MAGIC = b"%PDF-"
EVAL_ROOT = Path(engine.ROOT) / "evaluations"
RUN_ID_PREFIX = "eval-"


def _run_dir(run_id: str) -> Path:
    if not run_id.startswith(RUN_ID_PREFIX) or "/" in run_id or "\\" in run_id or ".." in run_id:
        raise HTTPException(400, f"Not a valid evaluation id: {run_id}")
    return EVAL_ROOT / run_id


@router.get("/config")
async def evaluation_config() -> dict:
    """What the Evaluate tab needs to render itself.

    Readiness is derived by actually building the engine config, so the tab
    cannot claim ready while the CLI would refuse — they share the check.
    """
    engine.load_dotenv(engine.ROOT / ".env")
    ready = True
    missing: list[str] = []
    endpoint = ""
    default_version = os.environ.get("AZURE_DOCUMENT_PII_API_VERSION", "2026-05-01")
    try:
        config = engine.build_config(auth="auto")
        endpoint = config.language_endpoint
        default_version = config.api_version
    except engine.ConfigError as exc:
        ready = False
        missing.append(str(exc))

    versions = ["2026-05-01", "2026-05-15-preview"]
    if default_version not in versions:
        versions.insert(0, default_version)

    return {
        "ready": ready,
        "missing": missing,
        "endpoint": endpoint,
        "default_api_version": default_version,
        "api_versions": versions,
        "policies": engine.POLICY_KINDS,
        "preview_only_policies": sorted(engine.PREVIEW_ONLY_POLICIES),
        "preview_api_versions": sorted(engine.PREVIEW_API_VERSIONS),
        "synonym_entity_types": engine.SYNONYM_ENTITY_TYPES,
        "modes": [
            {
                "value": "per-document",
                "label": "One job per file",
                "hint": "Isolates failures so one bad file cannot mask the others.",
            },
            {
                "value": "batch",
                "label": "All files in one job",
                "hint": "How the API is designed to be used. Faster; shared fate.",
            },
        ],
    }


@router.get("/categories")
async def categories() -> dict:
    """The PII taxonomy and which api-versions accept each category."""
    rows = engine.entity_categories()
    versions: set[str] = set()
    for row in rows:
        versions.update(row.get("document_pii_api_versions", []))
    return {
        "api_versions": sorted(versions),
        "categories": [
            {
                "tag": row.get("tag"),
                "name": row.get("name"),
                "group": row.get("group"),
                "document_pii_api_versions": row.get("document_pii_api_versions", []),
            }
            for row in rows
        ],
    }


@router.post("")
async def start_evaluation(
    files: list[UploadFile] = File(...),
    mode: str = Form("per-document"),
    api_version: str = Form(""),
    policy: str = Form("characterMask"),
    redaction_character: str = Form("*"),
    language: str = Form("en-US"),
    domain: str = Form("none"),
    categories: str = Form(""),
    keep_uploads: bool = Form(False),
    preserve_data_format: bool = Form(False),
    entity_synonyms: str = Form(""),
) -> dict:
    """Stage the uploads and run the same evaluation the CLI runs."""
    if not files:
        raise HTTPException(400, "No files were uploaded.")

    run_id = datetime.now(timezone.utc).strftime(f"{RUN_ID_PREFIX}%Y-%m-%d_%H-%M-%S")
    out_root = EVAL_ROOT / run_id
    out_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f"{run_id}-"))

    documents: list[engine.Document] = []
    budget = get_settings().max_upload_mb * 1024 * 1024
    total = 0
    for upload in files:
        data = await upload.read()
        name = Path(upload.filename or "document.pdf").name
        if not data:
            raise HTTPException(400, f"{name} is empty.")
        total += len(data)
        if total > budget:
            shutil.rmtree(staging, ignore_errors=True)
            raise HTTPException(
                413,
                f"Upload exceeds the {get_settings().max_upload_mb} MB limit.",
            )
        suffix = Path(name).suffix.lower()
        if suffix not in engine.SUPPORTED_EXTENSIONS:
            raise HTTPException(400, f"{name}: only PDF, DOCX and TXT are supported.")
        if suffix == ".pdf" and not data.lstrip()[:5].startswith(PDF_MAGIC):
            raise HTTPException(400, f"{name} has no %PDF- header.")
        # Path(...).name already removed separators, but a name like "...pdf"
        # still has ".." as its stem, which the output folder is derived from.
        if not Path(name).stem.strip(". "):
            raise HTTPException(400, f"{name}: unusable file name.")

        path = staging / name
        path.write_bytes(data)
        documents.append(
            engine.Document(
                path=path,
                blob_name=f"{run_id}/{name}",
                size_bytes=len(data),
                kind=engine.classify_pdf(path),
            )
        )

    documents.sort(key=lambda d: d.path.name)

    try:
        config = engine.build_config(api_version=api_version, auth="auto")
    except engine.ConfigError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise HTTPException(400, str(exc)) from exc

    synonyms: list[dict] = []
    if entity_synonyms.strip():
        try:
            parsed = json.loads(entity_synonyms)
        except json.JSONDecodeError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise HTTPException(400, f"entity_synonyms is not valid JSON: {exc}") from exc
        known = set(engine.SYNONYM_ENTITY_TYPES)
        for group in parsed if isinstance(parsed, list) else []:
            entity_type = str(group.get("entityType", "")).strip()
            words = [str(w).strip() for w in group.get("synonyms", []) if str(w).strip()]
            if not entity_type or not words:
                continue
            if entity_type not in known:
                shutil.rmtree(staging, ignore_errors=True)
                raise HTTPException(
                    400,
                    f"{entity_type} does not accept synonyms. Choose from: "
                    f"{', '.join(engine.SYNONYM_ENTITY_TYPES)}",
                )
            synonyms.append({"entityType": entity_type, "synonyms": words})

    options = engine.EvalOptions(
        mode=mode if mode in {"per-document", "batch"} else "per-document",
        policy=policy,
        redaction_character=redaction_character or "*",
        language=language or "en-US",
        domain=domain or "none",
        categories=[c for c in (categories or "").split(",") if c.strip()] or None,
        keep_uploads=bool(keep_uploads),
        preserve_data_format=bool(preserve_data_format),
        entity_synonyms=synonyms or None,
    )

    # Refuse a preview-only option on the GA version here, while it can still be
    # a 400 the browser can show, rather than inside the background task where
    # it would surface only as a failed run.
    try:
        engine.check_preview_features(options, config.api_version)
    except engine.ConfigError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise HTTPException(400, str(exc)) from exc

    job = registry.create(run_id, f"{len(documents)} document(s)")
    job.record("start", f"{len(documents)} document(s) · {options.mode} · {config.api_version}")

    async def execute() -> None:
        try:
            outcomes = await asyncio.to_thread(
                engine.run_evaluation,
                config,
                options,
                documents,
                out_root,
                lambda message: job.record("evaluate", message),
            )
            summary = engine.write_summary(out_root, run_id, config, options, outcomes)
            job.result = summary
            succeeded = sum(1 for o in outcomes if o.status == "succeeded")
            job.status = (
                "succeeded" if succeeded == len(outcomes)
                else "failed" if succeeded == 0
                else "partial"
            )
            job.record("done", f"{succeeded}/{len(outcomes)} succeeded")
        except Exception as exc:
            logger.exception("evaluation %s failed", run_id)
            job.status = "failed"
            job.error = str(exc)
            job.record("error", str(exc), {"level": "error"})
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    job.task = asyncio.create_task(execute())
    return {"run_id": run_id, "status": "running", "document_count": len(documents)}


@router.get("/jobs/{run_id}")
async def job_status(run_id: str, since: int = 0) -> dict:
    """Progress for an in-flight run.

    Falls back to the saved summary when the registry does not know the id —
    the registry is in memory, so a completed run survives a restart only on
    disk, and the browser should still be able to show it.
    """
    job = registry.get(run_id)
    if job is not None:
        return job.as_dict(since=since)

    manifest = _run_dir(run_id) / "summary.json"
    if not manifest.exists():
        raise HTTPException(404, f"No run called {run_id}")
    return {
        "run_id": run_id,
        "status": "succeeded",
        "events": [],
        "event_count": 0,
        "result": json.loads(manifest.read_text(encoding="utf-8")),
        "restored": True,
    }


@router.get("/runs")
async def list_evaluations(limit: int = 100) -> dict:
    rows: list[dict] = []
    if EVAL_ROOT.exists():
        for directory in sorted(EVAL_ROOT.iterdir(), reverse=True):
            if len(rows) >= limit:
                break
            if directory.name.startswith("."):
                continue
            manifest = directory / "summary.json"
            if not directory.is_dir() or not manifest.exists():
                continue
            try:
                summary = json.loads(manifest.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            documents = summary.get("documents", [])
            rows.append(
                {
                    "run_id": summary.get("run_id", directory.name),
                    "created_at": summary.get("created_at", ""),
                    "api_version": summary.get("api_version", ""),
                    "mode": summary.get("mode", ""),
                    "policy": summary.get("policy", ""),
                    "document_count": len(documents),
                    "succeeded": sum(1 for d in documents if d.get("status") == "succeeded"),
                    "total_entities": sum(d.get("entity_count", 0) for d in documents),
                }
            )
    return {"runs": rows, "evaluations_dir": str(EVAL_ROOT)}


@router.get("/runs/{run_id}")
async def get_evaluation(run_id: str) -> dict:
    manifest = _run_dir(run_id) / "summary.json"
    if not manifest.exists():
        raise HTTPException(404, f"No evaluation called {run_id}")
    return json.loads(manifest.read_text(encoding="utf-8"))


@router.delete("/runs/{run_id}")
async def delete_evaluation(run_id: str) -> dict:
    """Soft delete: move the run aside rather than destroying it.

    These folders hold redacted copies of real documents and a verbatim list of
    every PII value the service found — evidence that may have taken minutes of
    billable API time and cannot be reconstructed from anything else on disk.
    An accidental click should not be able to end that, so the run is moved to
    ``evaluations/.trash/`` and can be moved back by hand.
    """
    directory = _run_dir(run_id)
    if not directory.exists():
        return {"deleted": run_id, "note": "already gone"}

    trash = EVAL_ROOT / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    destination = trash / f"{run_id}--{stamp}"
    shutil.move(str(directory), str(destination))
    logger.info("moved evaluation %s to %s", run_id, destination)
    return {"deleted": run_id, "recoverable_at": str(destination)}


@router.get("/runs/{run_id}/artifact")
async def get_artifact(run_id: str, path: str, download: bool = False) -> Response:
    """Serve one artifact. ``path`` is relative to the evaluation folder.

    The path is validated **before** it touches the filesystem. On Windows a
    UNC path such as ``//host/share/x`` is an absolute root, so ``root / path``
    silently discards ``root`` — and merely resolving it makes Windows open an
    SMB connection and offer the logged-on user's credentials. Checking
    containment after the join would be far too late.
    """
    root = _run_dir(run_id).resolve()

    candidate = PurePosixPath(path)
    if (
        not path
        or candidate.is_absolute()
        or "\\" in path
        or ":" in path
        or "\x00" in path
        or ".." in candidate.parts
    ):
        raise HTTPException(404, f"{path} is not in {run_id}")

    target = (root / Path(*candidate.parts)).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, f"{path} is not in {run_id}")

    suffix = target.suffix.lower()
    media = {
        ".json": "application/json",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(suffix, "application/octet-stream")

    headers = {"Cache-Control": "private, max-age=600"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{target.name}"'
    return Response(content=target.read_bytes(), media_type=media, headers=headers)
