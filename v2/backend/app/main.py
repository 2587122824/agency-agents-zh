from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.router import router
from .core.config import FRONTEND_DIST, settings
from .orchestration.project_transitions import ProjectStateConflictError


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ProjectStateConflictError)
async def project_state_conflict(_: Request, exc: ProjectStateConflictError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"detail": str(exc)},
        headers={"X-Error-Code": exc.code},
    )


app.include_router(router, prefix=settings.api_prefix)


if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    target = FRONTEND_DIST / path
    if path and target.is_file() and target.resolve().is_relative_to(FRONTEND_DIST.resolve()):
        return FileResponse(target)
    index = FRONTEND_DIST / "index.html"
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-store"})
    return {"message": "Frontend is not built. Run npm.cmd run build in v2/frontend."}
