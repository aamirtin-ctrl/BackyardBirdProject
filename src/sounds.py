"""Sound-track selection for Instagram posts.

We don't embed copyrighted audio in the video (IG's audio fingerprinting
would mute or block the post). Instead the pipeline SELECTS a track per
post based on the caption's tone and logs a manual-add instruction. The
user adds the track via the Instagram app's native music picker, which
is covered by IG's music-catalog licensing deal for creator posts.

Track library: data/sounds.json (generated from the user-provided
emotional_sounds_conservation.xlsx).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from .config import PROJECT_ROOT

log = logging.getLogger(__name__)

SOUNDS_PATH = PROJECT_ROOT / "data" / "sounds.json"
ToneBucket = Literal["sad", "ambient", "hopeful"]


def load_library() -> dict:
    return json.loads(SOUNDS_PATH.read_text())


def pick_sound(tone_bucket: ToneBucket, recent_ids: list[int]) -> dict:
    """Pick a track from the bucket, preferring least-recently-used.

    `recent_ids` is the list of sound_ids used in recent posts, ordered
    oldest-first (so the LAST item is the most recent). We pick the track
    in the bucket that hasn't appeared for the longest stretch — or a
    random one if none of them have been used.
    """
    lib = load_library()
    if tone_bucket not in lib["buckets"]:
        log.warning("unknown tone_bucket %r, defaulting to 'ambient'", tone_bucket)
        tone_bucket = "ambient"
    bucket_ids = lib["buckets"][tone_bucket]["ids"]
    if not bucket_ids:
        raise RuntimeError(f"empty bucket: {tone_bucket}")

    # Score each candidate by how recently it was used (lower = better).
    # Unused tracks get score = -1 so they're picked first.
    def score(sid: int) -> int:
        try:
            return recent_ids[::-1].index(sid)  # 0 = most recent, larger = older
        except ValueError:
            return -1  # unused

    # Sort: unused first, then by oldest use.
    ranked = sorted(bucket_ids, key=lambda sid: (score(sid) == -1, -score(sid) if score(sid) >= 0 else 0))
    # Actually the right sort is: unused (score=-1) first, then oldest (highest score).
    unused = [sid for sid in bucket_ids if score(sid) == -1]
    if unused:
        chosen_id = unused[0]
    else:
        # All used; pick the one with the LARGEST score (oldest use).
        chosen_id = max(bucket_ids, key=score)

    track = next(s for s in lib["sounds"] if s["id"] == chosen_id)
    return track


def format_manual_add_instructions(track: dict) -> str:
    """Human-readable post-publish instruction string."""
    return (
        "\n=== MANUAL AUDIO STEP ===\n"
        f"Add music to the post in the Instagram app:\n"
        f"  1. Open the post on your phone\n"
        f"  2. Tap \u22ef (three dots) \u2192 Edit\n"
        f"  3. Tap 'Add music'\n"
        f"  4. Search for: {track['track']} \u2014 {track['artist']}\n"
        f"     Tone: {track['tone']}\n"
        f"     Why: {track.get('why_it_works', '')[:100]}...\n"
        f"  5. Trim to align with the video, tap Done\n"
        "========================\n"
    )
