from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = BACKEND_ROOT.parent
RUNTIME_ROOT = V2_ROOT / "runtime"
FRONTEND_DIST = V2_ROOT / "frontend" / "dist"


@dataclass(frozen=True)
class Settings:
    app_name: str = "Agency Studio V2"
    app_version: str = "0.1.0"
    api_prefix: str = "/api/v1"
    database_url: str = os.getenv(
        "V2_DATABASE_URL",
        f"sqlite:///{(RUNTIME_ROOT / 'studio.db').as_posix()}",
    )
    cors_origins: tuple[str, ...] = (
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    )


settings = Settings()
