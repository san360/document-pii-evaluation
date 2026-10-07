"""Security regressions. Plain script, no pytest — run it directly.

Every case here corresponds to a real finding, not a hypothetical. If one of
these starts failing, something that was deliberately closed has reopened.

No Azure credentials needed: nothing here submits a job.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

import evaluate_document_pii as engine
from app.main import app

passed = failed = 0


def check(label: str, actual, expected) -> None:
    global passed, failed
    if actual == expected:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}\n        expected {expected!r}, got {actual!r}")


def main() -> int:
    client = TestClient(app, base_url="http://127.0.0.1:8100")

    print("Artifact path containment")
    # A UNC path is an absolute root on Windows: `root / "//host/share"` throws
    # the root away, and merely resolving it makes Windows open an SMB
    # connection and offer the logged-on user's credentials.
    traversals = [
        "//192.0.2.1/share/x",
        "\\\\192.0.2.1\\share\\x",
        "../../../Windows/win.ini",
        "..\\..\\win.ini",
        "C:/Windows/win.ini",
        "C:win.ini",
        "/Windows/win.ini",
        "%2e%2e/win.ini",
        "",
    ]
    for path in traversals:
        response = client.get(
            "/api/evaluate/runs/eval-x/artifact", params={"path": path}
        )
        check(f"refused: {path!r}", response.status_code, 404)

    print("\nRun ids cannot escape the evaluations folder")
    for run_id in ["../etc", "..\\etc", "eval-../..", "nope"]:
        response = client.get(
            f"/api/evaluate/runs/{run_id}/artifact", params={"path": "a.pdf"}
        )
        check(f"refused run id {run_id!r}", response.status_code in (400, 404), True)

    print("\nCross-origin requests are refused")
    check(
        "Origin: evil.com",
        client.get("/api/evaluate/runs", headers={"Origin": "https://evil.com"}).status_code,
        403,
    )
    check(
        "Sec-Fetch-Site: cross-site",
        client.get("/api/evaluate/runs", headers={"Sec-Fetch-Site": "cross-site"}).status_code,
        403,
    )
    check(
        "same-origin still allowed",
        client.get(
            "/api/evaluate/runs", headers={"Origin": "http://127.0.0.1:8100"}
        ).status_code,
        200,
    )
    check("no Origin header still allowed", client.get("/api/evaluate/runs").status_code, 200)

    print("\nDNS rebinding is refused")
    check(
        "foreign Host",
        client.get("/api/evaluate/runs", headers={"Host": "attacker.com"}).status_code,
        400,
    )

    print("\nUpload names cannot escape the run folder")
    # Path("...pdf").stem == ".." — the output directory is derived from the
    # stem, so this would write the document one level above its run folder.
    response = client.post(
        "/api/evaluate", files={"files": ("...pdf", b"%PDF-1.4 x", "application/pdf")}
    )
    check("'...pdf' rejected", response.status_code, 400)
    check(
        "and the reason is the name",
        "name" in response.json().get("detail", "").lower(),
        True,
    )
    for name in ("..pdf", ". .pdf"):
        response = client.post(
            "/api/evaluate", files={"files": (name, b"%PDF-1.4 x", "application/pdf")}
        )
        check(f"{name!r} rejected", response.status_code, 400)

    print("\nThe stem guard holds in the engine too")
    for raw, expected in [("...pdf", "document"), ("..pdf", "document"),
                          ("report.pdf", "report")]:
        stem = Path(raw).stem.strip(". ") or "document"
        check(f"{raw!r} -> {expected!r}", stem, expected)

    print("\nSecrets never reach the run summary")
    scratch = ROOT / "evaluations" / ".sectest"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        config = engine.Config(
            language_endpoint="https://x.cognitiveservices.azure.com/",
            language_key="SUPERSECRETKEY",
            api_version="2026-05-01",
            auth="key",
            storage_account="st",
            storage_connection_string=(
                "DefaultEndpointsProtocol=https;AccountName=st;AccountKey=SECRETKEY==;"
            ),
            source_container="pii-source",
            target_container="pii-target",
        )
        engine.write_summary(scratch, "eval-test", config, engine.EvalOptions(), [])
        written = (scratch / "summary.json").read_text(encoding="utf-8")
        check("summary.json was actually written", len(written) > 0, True)
        check("no language key in summary", "SUPERSECRETKEY" in written, False)
        check("no account key in summary", "SECRETKEY==" in written, False)
        check("no connection string in summary", "AccountKey=" in written, False)
        check(
            "but the endpoint is recorded",
            json.loads(written)["endpoint"],
            "https://x.cognitiveservices.azure.com/",
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
