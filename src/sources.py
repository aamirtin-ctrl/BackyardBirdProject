"""Pull wildlife/conservation clips from licensed stock APIs.

Priority order (main.py iterates this):
  1. Pexels (free commercial license, no attribution required)
  2. Pixabay (free commercial license, no attribution required)
  3. archive.org — fallback, PD-only items

Wildlife-only: a tag-based filter strips clips showing people.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

import requests

from .config import Config, RAW_DIR

log = logging.getLogger(__name__)

# Birds-first seed queries. Prioritize: endangered species, action, calls.
# No humans.
WILDLIFE_QUERIES: list[str] = [
    # endangered / threatened species
    "condor bird",
    "harpy eagle",
    "shoebill",
    "hyacinth macaw",
    "whooping crane",
    "philippine eagle",
    "african grey parrot",
    "kakapo",
    "bali myna",
    "imperial amazon",
    "snowy owl",
    "spoon billed sandpiper",
    "atlantic puffin",
    "kiwi bird",
    # action
    "eagle catching fish",
    "hawk hunting",
    "owl in flight",
    "kingfisher diving",
    "hummingbird feeding flower",
    "woodpecker drumming tree",
    "peacock displaying feathers",
    "heron fishing",
    "pelican diving water",
    "falcon flight",
    "osprey catching fish",
    # calling / vocalizing
    "macaw calling",
    "loon call lake",
    "kookaburra laughing",
    "owl hooting night",
    "crow cawing",
    "rooster crowing",
    "songbird singing branch",
    "parrot squawking",
    # general bird backfill
    "flamingo flock",
    "toucan rainforest",
    "swan gliding water",
    "albatross flight ocean",
    "starling murmuration",
    "crane dancing",
    # non-bird wildlife fallback
    "coral reef",
    "sea turtle",
    "elephant herd",
    "monarch butterfly",
]

# archive.org fallback — collections skewed toward PD nature.
ARCHIVE_FALLBACK_QUERIES: list[str] = [
    "nasa earth",
    "prelinger nature",
    "usfws wildlife",
]

# Tags/words that indicate the clip is of a person. Applied to tags + title.
HUMAN_MARKERS = {
    "man", "men", "woman", "women", "person", "people", "human", "humans",
    "portrait", "child", "children", "kid", "boy", "girl", "face", "crowd",
    "tourist", "hiker", "diver", "swimmer", "runner", "worker", "farmer",
    "family", "couple", "group of people", "selfie", "influencer",
    "businesswoman", "businessman", "model", "skateboarder", "cyclist",
    "athlete", "dancer", "musician", "speaker", "presenter",
}

# Non-wildlife subject blacklist (urban, industrial, man-made). Applied to tags+title.
URBAN_MARKERS = {
    "city", "cityscape", "street", "road", "highway", "traffic", "car",
    "vehicle", "truck", "bus", "motorcycle", "stadium", "building",
    "skyscraper", "skyline", "downtown", "pier", "bridge", "tower",
    "construction", "factory", "office", "airport", "airplane", "train",
    "cruise ship", "yacht", "marina", "parking", "drone cityscape",
    "aerial city", "coastline", "cliffs", "beach resort",
    "boat", "boats", "ship", "ships", "dock", "harbor", "harbour",
}

# Captivity + human-structure blacklist. Birds must be in the wild.
CAPTIVITY_MARKERS = {
    "zoo", "aviary", "cage", "caged", "enclosure", "captive", "captivity",
    "pet", "pets", "domestic", "domesticated", "shoulder", "hand", "owner",
    "handler", "feeder", "feeding hand", "perched on finger",
    "fence", "fenced", "wire", "bars", "chain link", "chainlink",
    "rooftop", "roof", "indoor", "indoors", "window", "ceiling",
    "porch", "balcony", "birdhouse", "farm", "poultry", "chicken coop",
}

# Bird-related keywords. When a query is a bird species, at least one of these
# must appear in the clip's tags or title, else we reject it.
BIRD_TERMS = {
    "bird", "birds", "avian", "feather", "feathers", "wing", "wings", "beak",
    "flying", "flight", "perch", "perched", "nest", "nesting", "eagle",
    "hawk", "falcon", "owl", "parrot", "macaw", "cockatoo", "finch",
    "sparrow", "robin", "cardinal", "jay", "crow", "raven", "magpie",
    "hummingbird", "kingfisher", "heron", "egret", "stork", "crane",
    "flamingo", "pelican", "cormorant", "albatross", "gull", "tern",
    "puffin", "penguin", "duck", "goose", "swan", "loon", "chicken",
    "rooster", "peacock", "toucan", "woodpecker", "kookaburra", "kakapo",
    "condor", "vulture", "osprey", "kestrel", "shoebill", "ibis", "kiwi",
    "myna", "starling", "songbird", "wader", "shorebird", "raptor",
}

# Queries that are bird subjects (vs non-bird). Used to decide whether to
# apply BIRD_TERMS requirement. Match if any bird term appears in the query.
def _query_is_bird(query: str) -> bool:
    ql = query.lower()
    return any(term in ql for term in BIRD_TERMS) or any(
        word in ql for word in ("songbird", "shorebird", "raptor", "bird", "avian")
    )


def _has_urban(tags: list[str], text: str = "") -> bool:
    t = " ".join(tags + [text]).lower()
    return any(re.search(rf"\b{re.escape(m)}\b", t) for m in URBAN_MARKERS)


def _has_captivity(tags: list[str], text: str = "") -> bool:
    t = " ".join(tags + [text]).lower()
    return any(re.search(rf"\b{re.escape(m)}\b", t) for m in CAPTIVITY_MARKERS)


def _has_bird(tags: list[str], text: str = "") -> bool:
    t = " ".join(tags + [text]).lower()
    return any(re.search(rf"\b{re.escape(m)}\b", t) for m in BIRD_TERMS)

MIN_WIDTH = 1080  # want tall-capable or HD+ footage
MIN_HEIGHT = 1080
MIN_DURATION = 8   # targeting ~10s Reels, so 8s+ source is fine


# ----------------------------- helpers ---------------------------------

def _has_human(tags: list[str], text: str = "") -> bool:
    t = " ".join(tags + [text]).lower()
    return any(re.search(rf"\b{re.escape(m)}\b", t) for m in HUMAN_MARKERS)


def _download(url: str, out_path: Path) -> Path | None:
    """Stream-download a file with requests. Returns path or None."""
    try:
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if chunk:
                        f.write(chunk)
        return out_path
    except Exception as e:
        log.error("download failed %s: %s", url, e)
        out_path.unlink(missing_ok=True)
        return None


def _write_sidecar(path: Path, meta: dict[str, Any]) -> None:
    path.with_suffix(".json").write_text(json.dumps(meta, indent=2))


# ----------------------------- Pexels ----------------------------------

def pull_from_pexels(
    query: str, count: int, cfg: Config, skip_ids: set[str] | None = None
) -> list[Path]:
    if not cfg.pexels_api_key:
        log.warning("Pexels: no PEXELS_API_KEY set, skipping")
        return []
    skip_ids = skip_ids or set()
    headers = {"Authorization": cfg.pexels_api_key}
    params = {"query": query, "per_page": max(count * 4, 15), "orientation": "portrait"}
    try:
        r = requests.get("https://api.pexels.com/videos/search",
                         headers=headers, params=params, timeout=30)
        r.raise_for_status()
    except Exception as e:
        log.error("Pexels search failed: %s", e)
        return []

    results = r.json().get("videos", [])
    log.info("Pexels: %d candidates for %r", len(results), query)
    out: list[Path] = []
    require_bird = _query_is_bird(query)
    skipped = {"human": 0, "urban": 0, "captive": 0, "no_bird_term": 0, "too_short": 0, "low_res": 0}
    for v in results:
        if len(out) >= count:
            break
        vid_id = str(v.get("id"))
        page_url = v.get("url", "")
        if vid_id in skip_ids or page_url in skip_ids:
            continue
        tags = v.get("tags", []) or []
        # Pexels tags are often empty; page_url slug is our best text signal.
        text_blob = page_url
        if _has_human(tags, text=text_blob):
            skipped["human"] += 1
            continue
        if _has_urban(tags, text=text_blob):
            skipped["urban"] += 1
            continue
        if _has_captivity(tags, text=text_blob):
            skipped["captive"] += 1
            continue
        if require_bird and not _has_bird(tags, text=text_blob):
            skipped["no_bird_term"] += 1
            continue
        if (v.get("duration") or 0) < MIN_DURATION:
            skipped["too_short"] += 1
            continue

        # pick best portrait-ish file >= 1080 wide
        files = sorted(
            v.get("video_files", []),
            key=lambda f: (f.get("width") or 0) * (f.get("height") or 0),
            reverse=True,
        )
        picked = None
        for f in files:
            w, h = f.get("width") or 0, f.get("height") or 0
            if w >= MIN_WIDTH and h >= MIN_HEIGHT and f.get("link"):
                picked = f
                break
        if not picked:
            skipped["low_res"] += 1
            continue

        ext = "mp4"
        out_path = RAW_DIR / f"pexels_{vid_id}.{ext}"
        log.info("Pexels: downloading id=%s %dx%d %ss",
                 vid_id, picked["width"], picked["height"], v.get("duration"))
        if _download(picked["link"], out_path):
            _write_sidecar(out_path, {
                "source": "pexels",
                "source_url": page_url,
                "identifier": vid_id,
                "title": f"{query} (Pexels {vid_id})",
                "description": f"Pexels video by {v.get('user', {}).get('name', '')}",
                "subject": query,
                "tags": tags,
            })
            out.append(out_path)
    log.info("Pexels: pulled %d/%d for %r (skipped: %s)", len(out), count, query, skipped)
    return out


# ----------------------------- Pixabay ---------------------------------

def pull_from_pixabay(
    query: str, count: int, cfg: Config, skip_ids: set[str] | None = None
) -> list[Path]:
    if not cfg.pixabay_api_key:
        log.warning("Pixabay: no PIXABAY_API_KEY set, skipping")
        return []
    skip_ids = skip_ids or set()
    params = {
        "key": cfg.pixabay_api_key,
        "q": query,
        "per_page": max(count * 4, 20),
        "video_type": "film",
        "safesearch": "true",
        "category": "animals,nature,backgrounds",
    }
    try:
        r = requests.get("https://pixabay.com/api/videos/", params=params, timeout=30)
        r.raise_for_status()
    except Exception as e:
        log.error("Pixabay search failed: %s", e)
        return []

    hits = r.json().get("hits", [])
    log.info("Pixabay: %d candidates for %r", len(hits), query)
    out: list[Path] = []
    require_bird = _query_is_bird(query)
    skipped = {"human": 0, "urban": 0, "captive": 0, "no_bird_term": 0, "too_short": 0, "low_res": 0}
    for h in hits:
        if len(out) >= count:
            break
        vid_id = str(h.get("id"))
        page_url = h.get("pageURL", "")
        tags_raw = h.get("tags", "") or ""
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        if vid_id in skip_ids or page_url in skip_ids:
            continue
        text_blob = f"{page_url} {tags_raw}"
        if _has_human(tags, text=text_blob):
            skipped["human"] += 1
            continue
        if _has_urban(tags, text=text_blob):
            skipped["urban"] += 1
            continue
        if _has_captivity(tags, text=text_blob):
            skipped["captive"] += 1
            continue
        if require_bird and not _has_bird(tags, text=text_blob):
            skipped["no_bird_term"] += 1
            continue
        if (h.get("duration") or 0) < MIN_DURATION:
            skipped["too_short"] += 1
            continue

        videos = h.get("videos", {}) or {}
        # Pixabay sizes: large > medium > small > tiny. Pick largest >= 1080.
        picked = None
        for size in ("large", "medium", "small", "tiny"):
            v = videos.get(size) or {}
            if v.get("url") and v.get("width", 0) >= MIN_WIDTH and v.get("height", 0) >= MIN_HEIGHT:
                picked = v
                break
        if not picked:
            skipped["low_res"] += 1
            continue

        out_path = RAW_DIR / f"pixabay_{vid_id}.mp4"
        log.info("Pixabay: downloading id=%s %dx%d %ss",
                 vid_id, picked["width"], picked["height"], h.get("duration"))
        if _download(picked["url"], out_path):
            _write_sidecar(out_path, {
                "source": "pixabay",
                "source_url": page_url,
                "identifier": vid_id,
                "title": f"{query} (Pixabay {vid_id})",
                "description": tags_raw,
                "subject": query,
                "tags": tags,
            })
            out.append(out_path)
    log.info("Pixabay: pulled %d/%d for %r (skipped: %s)", len(out), count, query, skipped)
    return out


# ----------------------------- archive.org fallback --------------------

def _ydl_download(url: str, outtmpl: str) -> Path | None:
    cmd = [
        "yt-dlp",
        "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/best[height<=1080]",
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--no-warnings",
        "--print", "after_move:filepath",
        "-o", outtmpl,
        url,
    ]
    log.info("yt-dlp fetching: %s", url)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        log.error("yt-dlp timeout on %s", url)
        return None
    if r.returncode != 0:
        log.error("yt-dlp failed: %s", r.stderr.strip()[-500:])
        return None
    path = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    if not path or not Path(path).exists():
        return None
    return Path(path)


def pull_from_archive(
    query: str, count: int, skip_ids: set[str] | None = None
) -> list[Path]:
    """archive.org fallback. Filters to likely-PD collections."""
    skip_ids = skip_ids or set()
    params = {
        "q": f'({query}) AND mediatype:(movies) AND (collection:(nasa) OR collection:(prelinger) OR licenseurl:(*creativecommons*))',
        "fl[]": ["identifier", "title", "description", "downloads"],
        "rows": max(count * 4, 20),
        "sort[]": "downloads desc",
        "output": "json",
    }
    try:
        r = requests.get("https://archive.org/advancedsearch.php", params=params, timeout=30)
        r.raise_for_status()
        hits = r.json().get("response", {}).get("docs", [])
    except Exception as e:
        log.error("archive.org search failed: %s", e)
        return []

    out: list[Path] = []
    for hit in hits:
        if len(out) >= count:
            break
        ident = hit.get("identifier")
        title = hit.get("title") or ""
        if not ident or ident in skip_ids:
            continue
        if _has_human([], text=title):
            continue
        url = f"https://archive.org/details/{ident}"
        outtmpl = str(RAW_DIR / f"archive_{ident}.%(ext)s")
        path = _ydl_download(url, outtmpl)
        if path:
            _write_sidecar(path, {
                "source": "archive.org",
                "source_url": url,
                "identifier": ident,
                "title": title,
                "description": (hit.get("description") or "")[:1000],
                "subject": query,
            })
            out.append(path)
    log.info("archive: pulled %d/%d for %r", len(out), count, query)
    return out
