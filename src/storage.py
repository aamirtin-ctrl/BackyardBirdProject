"""Upload a processed clip to Cloudflare R2 and return a public URL."""
from __future__ import annotations

import logging
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig

from .config import Config

log = logging.getLogger(__name__)


def _client(cfg: Config):
    endpoint = f"https://{cfg.r2_account_id}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=cfg.r2_access_key_id,
        aws_secret_access_key=cfg.r2_secret_access_key,
        region_name="auto",
        config=BotoConfig(signature_version="s3v4"),
    )


def upload(local_path: Path, cfg: Config, key: str | None = None) -> str:
    """Upload local_path to R2, return public URL."""
    key = key or local_path.name
    client = _client(cfg)
    log.info("R2: uploading %s -> s3://%s/%s", local_path.name, cfg.r2_bucket, key)
    client.upload_file(
        str(local_path),
        cfg.r2_bucket,
        key,
        ExtraArgs={"ContentType": "video/mp4"},
    )
    base = cfg.r2_public_base_url.rstrip("/")
    url = f"{base}/{key}"
    log.info("R2: public URL: %s", url)
    return url


def delete(key: str, cfg: Config) -> None:
    client = _client(cfg)
    try:
        client.delete_object(Bucket=cfg.r2_bucket, Key=key)
        log.info("R2: deleted %s", key)
    except Exception as e:
        log.warning("R2: delete failed for %s: %s", key, e)
