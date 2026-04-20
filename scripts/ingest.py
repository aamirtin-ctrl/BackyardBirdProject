"""Manual: pull N fresh wildlife clips into the queue."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import sources, state, video  # noqa: E402
from src.config import Config, setup_logging  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--count", type=int, default=3)
    ap.add_argument("-q", "--query", help="single search query; omit to use seed list")
    ap.add_argument(
        "--source",
        choices=["pexels", "pixabay", "archive", "auto"],
        default="auto",
        help="which source to pull from (default: auto = pexels -> pixabay -> archive)",
    )
    args = ap.parse_args()

    cfg = Config.load(require_live=False)
    setup_logging(cfg.log_level)
    used = state.used_source_ids()
    raw: list[Path] = []

    queries = [args.query] if args.query else sources.WILDLIFE_QUERIES
    need = args.count
    # Spread requests across queries: ask each for 1-2 then rotate.
    per_query = 1 if need > 4 else 2

    def pull(fn, *fn_args) -> None:
        nonlocal raw
        for q in queries:
            if len(raw) >= need:
                return
            remaining = need - len(raw)
            raw += fn(q, min(per_query, remaining), *fn_args)

    if args.source in ("pexels", "auto"):
        pull(sources.pull_from_pexels, cfg, used)
    if len(raw) < need and args.source in ("pixabay", "auto"):
        pull(sources.pull_from_pixabay, cfg, used)
    if len(raw) < need and args.source in ("archive", "auto"):
        fallback_queries = [args.query] if args.query else sources.ARCHIVE_FALLBACK_QUERIES
        for q in fallback_queries:
            if len(raw) >= need:
                break
            raw += sources.pull_from_archive(q, need - len(raw), used)

    # Dedupe by path (Pexels can return same id for different queries).
    seen_paths: set[str] = set()
    deduped: list[Path] = []
    for r in raw:
        if str(r) in seen_paths:
            continue
        seen_paths.add(str(r))
        deduped.append(r)
    raw = deduped

    for r in raw:
        side = r.with_suffix(".json")
        if side.exists():
            try:
                meta = json.loads(side.read_text())
                if meta.get("source_url"):
                    state.mark_source_used(meta["source_url"])
            except json.JSONDecodeError:
                pass
        out = video.process(r)
        if out:
            r.unlink(missing_ok=True)
            side.unlink(missing_ok=True)

    return 0 if raw else 1


if __name__ == "__main__":
    sys.exit(main())
