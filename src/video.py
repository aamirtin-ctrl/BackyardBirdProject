"""Turn a raw clip into an IG-ready 9:16 Reel between 60-90s."""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import QUEUE_DIR

log = logging.getLogger(__name__)

TARGET_W = 1080
TARGET_H = 1350   # 4:5 — matches the static slide so the carousel displays cleanly
# (9:16 is Reels-only; carousels require all items to share aspect ratio)
# Short-form: aim for ~10s, start near the beginning to grab the strongest moment.
TARGET_START = 0.0
TARGET_END = 15.0
MIN_OUT_SECS = 7.0
MAX_OUT_SECS = 15.0


def _probe(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {r.stderr.strip()[-300:]}")
    return json.loads(r.stdout)


def _slugify(s: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return (s or "clip")[:maxlen]


def process(raw_path: Path) -> Path | None:
    """Crop to 9:16 center, trim to 60-90s from offset 30s, re-encode for IG.

    Returns queued output path, or None on failure.
    """
    if not raw_path.exists():
        log.error("video.process: missing input %s", raw_path)
        return None

    sidecar = raw_path.with_suffix(".json")
    meta = {}
    if sidecar.exists():
        try:
            meta = json.loads(sidecar.read_text())
        except json.JSONDecodeError:
            log.warning("bad sidecar %s", sidecar)

    try:
        probe = _probe(raw_path)
    except Exception as e:
        log.error("probe failed on %s: %s", raw_path, e)
        return None

    vstream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None)
    if not vstream:
        log.error("no video stream in %s", raw_path)
        return None

    try:
        duration = float(probe["format"]["duration"])
    except (KeyError, ValueError):
        duration = 0.0

    start = TARGET_START if duration > TARGET_START + MIN_OUT_SECS else 0.0
    end = min(TARGET_END, duration if duration else TARGET_END)
    avail = end - start
    if avail < MIN_OUT_SECS:
        log.error("clip too short (source=%.1fs, available=%.1fs, need>=%.1fs)",
                  duration, avail, MIN_OUT_SECS)
        return None
    seg = min(MAX_OUT_SECS, avail)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug = _slugify(meta.get("title") or raw_path.stem)
    out_path = QUEUE_DIR / f"{ts}_{slug}.mp4"

    # 9:16 center crop filter: scale to cover, then crop to 1080x1920.
    vf = (
        f"scale=w='if(gt(a,{TARGET_W}/{TARGET_H}),-2,{TARGET_W})':"
        f"h='if(gt(a,{TARGET_W}/{TARGET_H}),{TARGET_H},-2)',"
        f"crop={TARGET_W}:{TARGET_H},"
        f"fps=30"
    )

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.2f}", "-t", f"{seg:.2f}",
        "-i", str(raw_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-maxrate", "5M", "-bufsize", "10M",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart",
        str(out_path),
    ]
    log.info("ffmpeg processing %s -> %s (start=%.1fs dur=%.1fs)",
             raw_path.name, out_path.name, start, seg)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log.error("ffmpeg failed: %s", r.stderr.strip()[-500:])
        out_path.unlink(missing_ok=True)
        return None

    # copy sidecar forward so caption.py can read source metadata
    out_sidecar = out_path.with_suffix(".json")
    if sidecar.exists():
        shutil.copyfile(sidecar, out_sidecar)
    else:
        out_sidecar.write_text(json.dumps({"source_url": "", "title": raw_path.stem}, indent=2))

    log.info("queued: %s (%.1f MB)", out_path.name, out_path.stat().st_size / 1e6)
    return out_path
