"""Instagram Graph API publishing.

Supports two post styles:
  - REELS (single video)
  - CAROUSEL (IMAGE + VIDEO children) — the "Static + Video" style

Uses Instagram Login / Business Login API (no Facebook Page required).
Endpoint base: https://graph.instagram.com/v21.0
"""
from __future__ import annotations

import logging
import time

import requests

from .config import Config

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.instagram.com/v21.0"
POLL_INTERVAL_S = 10
POLL_MAX_S = 300


class IGError(RuntimeError):
    pass


def _post(url: str, data: dict, timeout: int = 60) -> dict:
    r = requests.post(url, data=data, timeout=timeout)
    if not r.ok:
        raise IGError(f"POST {url} -> {r.status_code}: {r.text[:500]}")
    return r.json()


def _get(url: str, params: dict, timeout: int = 30) -> dict:
    r = requests.get(url, params=params, timeout=timeout)
    if not r.ok:
        raise IGError(f"GET {url} -> {r.status_code}: {r.text[:500]}")
    return r.json()


# ------------------------------- containers ---------------------------

def create_reels_container(video_url: str, caption: str, cfg: Config) -> str:
    url = f"{GRAPH_BASE}/{cfg.ig_user_id}/media"
    log.info("IG: creating Reels container")
    resp = _post(url, {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "access_token": cfg.ig_access_token,
    })
    cid = resp.get("id")
    if not cid:
        raise IGError(f"no container id: {resp}")
    log.info("IG: reels container id=%s", cid)
    return cid


def create_image_child(image_url: str, cfg: Config) -> str:
    """IMAGE carousel child. is_carousel_item=true is required."""
    url = f"{GRAPH_BASE}/{cfg.ig_user_id}/media"
    log.info("IG: creating image child (%s)", image_url)
    resp = _post(url, {
        "image_url": image_url,
        "is_carousel_item": "true",
        "access_token": cfg.ig_access_token,
    })
    cid = resp.get("id")
    if not cid:
        raise IGError(f"no image child id: {resp}")
    log.info("IG: image child id=%s", cid)
    return cid


def create_video_child(video_url: str, cfg: Config) -> str:
    """VIDEO carousel child. Note: media_type=VIDEO, NOT REELS, for carousels."""
    url = f"{GRAPH_BASE}/{cfg.ig_user_id}/media"
    log.info("IG: creating video child (%s)", video_url)
    resp = _post(url, {
        "media_type": "VIDEO",
        "video_url": video_url,
        "is_carousel_item": "true",
        "access_token": cfg.ig_access_token,
    })
    cid = resp.get("id")
    if not cid:
        raise IGError(f"no video child id: {resp}")
    log.info("IG: video child id=%s", cid)
    return cid


def create_carousel_container(child_ids: list[str], caption: str, cfg: Config) -> str:
    url = f"{GRAPH_BASE}/{cfg.ig_user_id}/media"
    log.info("IG: creating carousel container with %d children", len(child_ids))
    resp = _post(url, {
        "media_type": "CAROUSEL",
        "children": ",".join(child_ids),
        "caption": caption,
        "access_token": cfg.ig_access_token,
    })
    cid = resp.get("id")
    if not cid:
        raise IGError(f"no carousel container id: {resp}")
    log.info("IG: carousel container id=%s", cid)
    return cid


# ------------------------------- polling + publish --------------------

def wait_for_finished(container_id: str, cfg: Config) -> None:
    url = f"{GRAPH_BASE}/{container_id}"
    elapsed = 0
    while elapsed < POLL_MAX_S:
        data = _get(url, {"fields": "status_code", "access_token": cfg.ig_access_token})
        status = data.get("status_code")
        log.info("IG: container %s status=%s (%ds)", container_id, status, elapsed)
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise IGError(f"container errored: {data}")
        if status == "EXPIRED":
            raise IGError(f"container expired: {data}")
        time.sleep(POLL_INTERVAL_S)
        elapsed += POLL_INTERVAL_S
    raise IGError(f"container {container_id} not FINISHED after {POLL_MAX_S}s")


def publish(container_id: str, cfg: Config) -> str:
    url = f"{GRAPH_BASE}/{cfg.ig_user_id}/media_publish"
    log.info("IG: publishing container %s", container_id)
    resp = _post(url, {
        "creation_id": container_id,
        "access_token": cfg.ig_access_token,
    })
    mid = resp.get("id")
    if not mid:
        raise IGError(f"no media id in publish response: {resp}")
    log.info("IG: published media id=%s", mid)
    return mid


# ------------------------------- high-level -----------------------------

def post_reel(video_url: str, caption: str, cfg: Config, dry_run: bool = False) -> str:
    if dry_run:
        log.info("DRY-RUN: would post REEL video=%s caption=%r",
                 video_url, caption[:80])
        return "DRY-RUN"
    cid = create_reels_container(video_url, caption, cfg)
    wait_for_finished(cid, cfg)
    return publish(cid, cfg)


def post_carousel_static_video(
    image_url: str,
    video_url: str,
    caption: str,
    cfg: Config,
    dry_run: bool = False,
) -> str:
    """Publish the Static+Video carousel (image first, video second)."""
    if dry_run:
        log.info(
            "DRY-RUN: would post CAROUSEL image=%s video=%s caption=%r",
            image_url, video_url, caption[:80],
        )
        return "DRY-RUN"
    img_child = create_image_child(image_url, cfg)
    vid_child = create_video_child(video_url, cfg)
    # Poll both children until ready
    for cid in (img_child, vid_child):
        wait_for_finished(cid, cfg)
    parent = create_carousel_container([img_child, vid_child], caption, cfg)
    wait_for_finished(parent, cfg)
    return publish(parent, cfg)
