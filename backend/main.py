"""
ClipGenerator — FastAPI application entry-point.

Responsibilities
----------------
* Create and configure the FastAPI application instance.
* Register CORS middleware so the browser-based frontend can talk to the API
  regardless of which port it is served from during development.
* Mount the static-files directory (frontend/) so the SPA is served at /.
* Include every API router under the /api prefix.
* Run `init_db()` on startup to create all SQLite tables when they are absent.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.database import init_db
from backend.routes import clips, jobs, upload

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Project root is two levels up from this file: backend/main.py → backend/ → root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Async context-manager executed once at startup and once at shutdown."""
    logger.info("ClipGenerator starting up …")

    # Ensure upload / output directories exist so the rest of the app never
    # has to worry about missing parent directories.
    for folder in ("uploads", "outputs"):
        (PROJECT_ROOT / folder).mkdir(parents=True, exist_ok=True)
        logger.info("Ensured directory exists: %s", PROJECT_ROOT / folder)

    # Create all SQLAlchemy-mapped tables that do not already exist in the
    # SQLite database file.  This is intentionally a no-op when the schema is
    # already up-to-date so it is safe to run on every startup.
    await init_db()
    logger.info("Database initialised.")

    yield  # ← application runs here

    logger.info("ClipGenerator shutting down.")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """
    Build and return the configured FastAPI application.

    Separating construction into a factory makes the app easy to import in
    tests or by other tooling without immediately binding to a port.
    """
    app = FastAPI(
        title="ClipGenerator",
        description=(
            "AI-powered video clip generator.  Upload a long-form video and "
            "receive short, subtitle-burned clips ready for social media."
        ),
        version="1.0.0",
        lifespan=lifespan,
        # Disable the automatic /docs and /redoc redirects if you want a
        # completely clean root; they remain enabled here for convenience.
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------
    # During development the frontend may be opened as a plain file (file://)
    # or served by a different dev-server port, so we allow all origins.
    # Restrict `allow_origins` to your actual domain in production.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # API Routers
    # ------------------------------------------------------------------
    app.include_router(upload.router, prefix="/api", tags=["upload"])
    app.include_router(jobs.router, prefix="/api", tags=["jobs"])
    app.include_router(clips.router, prefix="/api", tags=["clips"])

    # ------------------------------------------------------------------
    # Static files — frontend SPA
    # ------------------------------------------------------------------
    # The /static mount exposes JS, CSS and any other assets that index.html
    # references via relative paths like "/static/app.js".
    if FRONTEND_DIR.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(FRONTEND_DIR)),
            name="static",
        )
        logger.info("Mounted frontend static files from: %s", FRONTEND_DIR)
    else:
        logger.warning(
            "Frontend directory not found at %s — static files will not be served.",
            FRONTEND_DIR,
        )

    # ------------------------------------------------------------------
    # Root route — serve index.html
    # ------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    async def serve_index():
        """Serve the SPA shell for the root path."""
        index = FRONTEND_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return {"detail": "Frontend not found. Please build the frontend first."}

    # Catch-all for any deep path the SPA may try to load directly in the
    # browser (e.g. if the user refreshes on a client-side route).
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        """
        Forward unknown paths to the SPA so client-side routing works on hard
        refresh.  API paths are matched before this catch-all because routers
        are registered first.
        """
        # If the path resolves to a real static file, serve it directly.
        candidate = FRONTEND_DIR / full_path
        if candidate.exists() and candidate.is_file():
            return FileResponse(str(candidate))
        # Otherwise fall back to the SPA shell.
        index = FRONTEND_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return {"detail": "Not found."}

    return app


# ---------------------------------------------------------------------------
# Module-level app instance
# ---------------------------------------------------------------------------
# `uvicorn backend.main:app` picks this up directly.

app = create_app()
