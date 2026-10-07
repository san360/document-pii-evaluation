"""FastAPI application: the Document PII evaluation console.

One tab, one job — submit documents to ``analyze-documents/jobs`` and show what
comes back. All the actual work lives in ``evaluate_document_pii.py``, which the
CLI also calls.
"""

from __future__ import annotations

import logging
import re

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import BASE_DIR, get_settings
from app.routers import evaluate

logger = logging.getLogger(__name__)

_ALLOWED_ORIGINS: set[str] = set()


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    )

    # Origins the browser is allowed to call this console from. Anything else is
    # a cross-site request to an unauthenticated local server, which is how a
    # random web page would reach it.
    global _ALLOWED_ORIGINS
    _ALLOWED_ORIGINS = {
        f"http://{host}:{settings.port}"
        for host in ("127.0.0.1", "localhost", "[::1]")
    }

    application = FastAPI(
        title=settings.app_name,
        description="Evaluate the Azure AI Language Document PII API against real PDFs.",
        version="2.0.0",
    )

    application.include_router(evaluate.router)

    # Defeats DNS rebinding: without a Host check, an attacker domain that
    # re-resolves to 127.0.0.1 becomes same-origin and can then read the run
    # artifacts, which include every detected PII value verbatim.
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[
            "127.0.0.1", "localhost", "[::1]",
            f"127.0.0.1:{settings.port}", f"localhost:{settings.port}",
            f"[::1]:{settings.port}",
        ],
    )

    static_dir = BASE_DIR / "static"
    application.mount("/static", StaticFiles(directory=static_dir), name="static")

    # Stamp every /static reference with its file's mtime. Without this the
    # browser happily serves a cached script against a newer page — which looks
    # exactly like a feature silently not working, and costs an afternoon to
    # recognise. No build step, no manual version bumping.
    static_ref = re.compile(r'(src|href)="(/static/[^"?]+)"')

    def version_static(html: str) -> str:
        def replace(match: re.Match[str]) -> str:
            attribute, path = match.group(1), match.group(2)
            asset = static_dir / path[len("/static/") :]
            try:
                stamp = int(asset.stat().st_mtime)
            except OSError:
                return match.group(0)
            return f'{attribute}="{path}?v={stamp}"'

        return static_ref.sub(replace, html)

    @application.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(version_static(html), headers={"Cache-Control": "no-cache"})

    @application.get("/api/health")
    async def health() -> dict:
        """Always answers, even with nothing configured."""
        return {"status": "ok", "app": settings.app_name}

    @application.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @application.middleware("http")
    async def security_headers(request: Request, call_next):
        # Cross-origin guard. The console has no authentication, so without this
        # any page the user visits while it is running can POST a document to it
        # (multipart is a CORS-"simple" request and needs no preflight) or pull
        # an artifact with a plain GET. Sec-Fetch-Site is sent by every current
        # browser; Origin covers the rest. Both are absent for same-origin
        # navigations and for curl, which keeps the CLI and the tab working.
        site = request.headers.get("sec-fetch-site")
        if site and site not in {"same-origin", "none"}:
            return JSONResponse(status_code=403, content={"detail": "Cross-origin request refused."})
        origin = request.headers.get("origin")
        if origin and origin not in _ALLOWED_ORIGINS:
            return JSONResponse(status_code=403, content={"detail": "Cross-origin request refused."})

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # SAMEORIGIN, not DENY: the app embeds its own PDF artifacts so you can
        # look at what the service returned. Framing by anyone else is still
        # refused — this app renders attacker-supplied PDFs, so clickjacking it
        # is a realistic vector.
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; object-src 'self'; frame-src 'self' blob:; "
            "frame-ancestors 'self'; base-uri 'none'; form-action 'none'",
        )
        return response

    logger.info("%s ready — open http://%s:%s", settings.app_name, settings.host, settings.port)
    return application


app = create_app()
