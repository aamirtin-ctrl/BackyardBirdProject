"""Load env vars and validate required ones. Fail loudly at startup."""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
QUEUE_DIR = DATA_DIR / "queue"
POSTED_DIR = DATA_DIR / "posted"
STATE_PATH = DATA_DIR / "state.json"

load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str
    pexels_api_key: str
    pixabay_api_key: str
    r2_account_id: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket: str
    r2_public_base_url: str
    ig_user_id: str
    ig_access_token: str
    meta_app_id: str
    meta_app_secret: str
    log_level: str
    dry_run: bool
    post_style: str

    @classmethod
    def load(cls, require_live: bool = False) -> "Config":
        def g(k: str, default: str = "") -> str:
            return os.environ.get(k, default).strip()

        cfg = cls(
            anthropic_api_key=g("ANTHROPIC_API_KEY"),
            pexels_api_key=g("PEXELS_API_KEY"),
            pixabay_api_key=g("PIXABAY_API_KEY"),
            r2_account_id=g("R2_ACCOUNT_ID"),
            r2_access_key_id=g("R2_ACCESS_KEY_ID"),
            r2_secret_access_key=g("R2_SECRET_ACCESS_KEY"),
            r2_bucket=g("R2_BUCKET", "ig-poster"),
            r2_public_base_url=g("R2_PUBLIC_BASE_URL"),
            ig_user_id=g("IG_USER_ID"),
            ig_access_token=g("IG_ACCESS_TOKEN"),
            meta_app_id=g("META_APP_ID"),
            meta_app_secret=g("META_APP_SECRET"),
            log_level=g("LOG_LEVEL", "INFO") or "INFO",
            dry_run=g("DRY_RUN", "false").lower() in {"1", "true", "yes"},
            post_style=g("POST_STYLE", "static_video") or "static_video",
        )
        if require_live:
            missing = [
                name for name, val in {
                    # ANTHROPIC_API_KEY is intentionally optional: Option A path
                    # pre-generates hook+caption via a scheduled Claude Code
                    # trigger, so the runtime doesn't need API access.
                    "R2_ACCOUNT_ID": cfg.r2_account_id,
                    "R2_ACCESS_KEY_ID": cfg.r2_access_key_id,
                    "R2_SECRET_ACCESS_KEY": cfg.r2_secret_access_key,
                    "R2_PUBLIC_BASE_URL": cfg.r2_public_base_url,
                    "IG_USER_ID": cfg.ig_user_id,
                    "IG_ACCESS_TOKEN": cfg.ig_access_token,
                }.items() if not val
            ]
            if missing:
                print(f"FATAL: missing required env vars: {', '.join(missing)}", file=sys.stderr)
                sys.exit(2)
        return cfg


def setup_logging(level: str = "INFO") -> None:
    log_file = PROJECT_ROOT / "run.log"
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, mode="a", encoding="utf-8"),
    ]
    logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)
    # silence yt-dlp noise
    logging.getLogger("yt_dlp").setLevel(logging.WARNING)


for d in (RAW_DIR, QUEUE_DIR, POSTED_DIR):
    d.mkdir(parents=True, exist_ok=True)
