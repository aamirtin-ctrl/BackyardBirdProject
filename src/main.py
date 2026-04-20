"""Pipeline entry point. Run once per day via GH Actions cron."""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

from . import caption, instagram, post_styles, sources, state, static_image, storage, video
from .config import (
    Config, DATA_DIR, POSTED_DIR, QUEUE_DIR, RAW_DIR, setup_logging,
)

log = logging.getLogger(__name__)

MIN_QUEUE = 3
STATIC_DIR = DATA_DIR / "static"


def _queue_clips() -> list[Path]:
    return sorted(QUEUE_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime)


def _topup_queue(cfg: Config) -> None:
    existing = _queue_clips()
    if len(existing) >= MIN_QUEUE:
        log.info("queue has %d clips, skipping ingest", len(existing))
        return
    need = MIN_QUEUE - len(existing) + 1
    log.info("queue low (%d), pulling %d new clips", len(existing), need)
    used = state.used_source_ids()

    raw_paths: list[Path] = []
    per_query = 1 if need > 4 else 2

    for query in sources.WILDLIFE_QUERIES:
        if len(raw_paths) >= need:
            break
        raw_paths += sources.pull_from_pexels(
            query, min(per_query, need - len(raw_paths)), cfg, used
        )

    if len(raw_paths) < need:
        for query in sources.WILDLIFE_QUERIES:
            if len(raw_paths) >= need:
                break
            raw_paths += sources.pull_from_pixabay(
                query, min(per_query, need - len(raw_paths)), cfg, used
            )

    if len(raw_paths) < need:
        for query in sources.ARCHIVE_FALLBACK_QUERIES:
            if len(raw_paths) >= need:
                break
            raw_paths += sources.pull_from_archive(query, need - len(raw_paths), used)

    # Dedupe by path
    seen_paths: set[str] = set()
    raw_paths = [p for p in raw_paths if not (str(p) in seen_paths or seen_paths.add(str(p)))]

    for raw in raw_paths:
        sidecar = raw.with_suffix(".json")
        if sidecar.exists():
            try:
                meta = json.loads(sidecar.read_text())
                if meta.get("source_url"):
                    state.mark_source_used(meta["source_url"])
            except json.JSONDecodeError:
                pass
        out = video.process(raw)
        if out:
            raw.unlink(missing_ok=True)
            sidecar.unlink(missing_ok=True)


def _pick_next(cfg: Config) -> Path | None:
    for clip in _queue_clips():
        if state.was_posted(clip.name):
            continue
        return clip
    return None


def _load_meta(clip: Path) -> dict:
    sidecar = clip.with_suffix(".json")
    if not sidecar.exists():
        return {}
    try:
        return json.loads(sidecar.read_text())
    except json.JSONDecodeError:
        log.warning("bad sidecar %s", sidecar)
        return {}


def _post_static_video(
    clip: Path, meta: dict, cfg: Config, dry: bool
) -> tuple[str, dict]:
    """Run the Static+Video carousel flow. Returns (media_id, info)."""
    style = post_styles.get("static_video")
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Hook text for the static slide (sidecar first, API fallback)
    hook = caption.generate_hook(meta, cfg.anthropic_api_key, clip_path=clip)
    log.info("hook: %s", hook)

    # 2. Build static image from the video + hook
    static_path = STATIC_DIR / f"{clip.stem}.jpg"
    result = static_image.build_static(clip, hook, style, static_path)
    if not result:
        raise RuntimeError("static image build failed")

    # 3. Caption for the whole carousel (long-form + hashtags)
    cap_result = caption.generate_caption(meta, cfg.anthropic_api_key, clip_path=clip)
    final_text = caption.compose_final(cap_result["caption"], cap_result["hashtags"])

    # 4. Upload both assets
    if dry:
        image_url = f"file://{static_path.resolve()}"
        video_url = f"file://{clip.resolve()}"
        log.info("DRY-RUN: skipping R2 upload")
    else:
        image_url = storage.upload(static_path, cfg, key=f"static/{static_path.name}")
        video_url = storage.upload(clip, cfg, key=f"video/{clip.name}")

    # 5. Publish carousel
    media_id = instagram.post_carousel_static_video(
        image_url, video_url, final_text, cfg, dry_run=dry
    )
    return media_id, {
        "hook": hook,
        "caption_warnings": cap_result["warnings"],
        "image_url": image_url,
        "video_url": video_url,
    }


def _post_reels(clip: Path, meta: dict, cfg: Config, dry: bool) -> tuple[str, dict]:
    """Single REELS post (legacy v1 style)."""
    cap_result = caption.generate_caption(meta, cfg.anthropic_api_key, clip_path=clip)
    final_text = caption.compose_final(cap_result["caption"], cap_result["hashtags"])

    if dry:
        video_url = f"file://{clip.resolve()}"
        log.info("DRY-RUN: skipping R2 upload")
    else:
        video_url = storage.upload(clip, cfg, key=f"video/{clip.name}")

    media_id = instagram.post_reel(video_url, final_text, cfg, dry_run=dry)
    return media_id, {
        "caption_warnings": cap_result["warnings"],
        "video_url": video_url,
    }


def run(dry_run: bool = False) -> int:
    cfg = Config.load(require_live=not dry_run)
    setup_logging(cfg.log_level)
    dry = dry_run or cfg.dry_run
    log.info("=== pipeline start (dry_run=%s, style=%s) ===", dry, cfg.post_style)

    try:
        _topup_queue(cfg)
    except Exception as e:
        log.exception("ingest failed: %s", e)
        return 1

    clip = _pick_next(cfg)
    if not clip:
        log.error("no postable clip available")
        return 1

    meta = _load_meta(clip)

    try:
        if cfg.post_style == "static_video":
            media_id, info = _post_static_video(clip, meta, cfg, dry)
        elif cfg.post_style == "reels":
            media_id, info = _post_reels(clip, meta, cfg, dry)
        else:
            log.error("unknown POST_STYLE: %s", cfg.post_style)
            return 2
    except Exception as e:
        log.exception("publish failed: %s", e)
        return 1

    if dry:
        log.info("DRY-RUN: skipping state update and file archival")
    else:
        state.mark_posted(clip.name, media_id, source_url=meta.get("source_url", ""))
        dest = POSTED_DIR / clip.name
        shutil.move(str(clip), str(dest))
        for suf in (".json", ".llm.json"):
            src = clip.with_suffix(suf)
            if src.exists():
                shutil.move(str(src), str(POSTED_DIR / src.name))
    log.info("=== pipeline complete: media_id=%s info=%s ===", media_id, info)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="skip R2 upload and IG publish; keep local artifacts")
    args = ap.parse_args(argv)
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
