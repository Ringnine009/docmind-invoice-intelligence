"""FastAPI application factory and entry point."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.routes import router as api_router
from app.core.config import REPO_ROOT, Settings, get_settings
from app.services.batch.store import BatchStore
from app.services.extraction.base import Extractor
from app.services.extraction.dashscope_extractor import DashScopeExtractor


def create_app(extractor: Extractor | None = None) -> FastAPI:
    settings: Settings = get_settings()
    app = FastAPI(
        title="DocMind API",
        description=(
            "Multimodal invoice intelligence & audit platform — extraction "
            "with per-field confidence, rule-based auditing and knowledge "
            "graphs. Docs: /api/docs"
        ),
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.state.settings = settings
    app.state.extractor = extractor or DashScopeExtractor(settings)
    app.state.store = BatchStore(settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)
    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built React dashboard from / when available."""
    dist = REPO_ROOT / "frontend" / "dist"
    if (dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
