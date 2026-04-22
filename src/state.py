"""Persistent post history. Plain JSON, atomic writes."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import STATE_PATH

log = logging.getLogger(__name__)


def _load(path: Path = STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"posted": [], "source_urls_used": []}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        log.error("state.json is corrupt, starting fresh (backed up)")
        path.rename(path.with_suffix(".corrupt.json"))
        return {"posted": [], "source_urls_used": []}


def _save(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".state.", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def was_posted(file_or_url: str, path: Path = STATE_PATH) -> bool:
    s = _load(path)
    if file_or_url in s.get("source_urls_used", []):
        return True
    for entry in s.get("posted", []):
        if entry.get("file") == file_or_url or entry.get("source_url") == file_or_url:
            return True
    return False


def mark_posted(
    file: str,
    ig_media_id: str,
    source_url: str = "",
    sound_id: int | None = None,
    path: Path = STATE_PATH,
) -> None:
    s = _load(path)
    entry = {
        "file": file,
        "ig_media_id": ig_media_id,
        "posted_at": datetime.now(timezone.utc).isoformat(),
        "source_url": source_url,
    }
    if sound_id is not None:
        entry["sound_id"] = sound_id
    s["posted"].append(entry)
    if source_url and source_url not in s["source_urls_used"]:
        s["source_urls_used"].append(source_url)
    _save(s, path)
    log.info("state: marked posted %s (ig=%s, sound=%s)", file, ig_media_id, sound_id)


def recent_sound_ids(n: int = 15, path: Path = STATE_PATH) -> list[int]:
    """Return sound_ids from the most recent N posts, oldest-first."""
    s = _load(path)
    ids = [p["sound_id"] for p in s.get("posted", []) if "sound_id" in p]
    return ids[-n:]


def mark_source_used(source_url: str, path: Path = STATE_PATH) -> None:
    s = _load(path)
    if source_url and source_url not in s["source_urls_used"]:
        s["source_urls_used"].append(source_url)
        _save(s, path)


def get_history(n: int = 20, path: Path = STATE_PATH) -> list[dict[str, Any]]:
    s = _load(path)
    return s.get("posted", [])[-n:]


def used_source_ids(path: Path = STATE_PATH) -> set[str]:
    s = _load(path)
    return set(s.get("source_urls_used", []))


# --- smoke-test asserts (spec allows a few) ---
def _self_test() -> None:
    import pathlib
    tmp = pathlib.Path(tempfile.mkstemp(suffix=".json")[1])
    tmp.unlink(missing_ok=True)
    assert was_posted("nope", path=tmp) is False
    mark_posted("a.mp4", "ig_1", "https://example.com/x", path=tmp)
    assert was_posted("a.mp4", path=tmp)
    assert was_posted("https://example.com/x", path=tmp)
    assert len(get_history(path=tmp)) == 1
    tmp.unlink(missing_ok=True)
    print("state.py self-test: OK")


if __name__ == "__main__":
    _self_test()
