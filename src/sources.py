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

# Birds-first seed queries — leaning into Pexels' actual strengths:
# flight silhouettes against sunset skies, golden-hour atmospherics, and
# macro close-ups. These are the categories where Pexels has deep high-
# quality inventory. Dramatic predator-hunting footage mostly lives on
# paid platforms; we don't fight the stock-site content gap.
WILDLIFE_QUERIES: list[str] = [
    # flight silhouettes against sky / sunset
    "bird silhouette sunset sky",
    "eagle silhouette sunset",
    "hawk silhouette sunset",
    "heron silhouette sunset water",
    "flamingo silhouette sunset",
    "pelican silhouette sunset",
    "crane silhouette sunset",
    "flock birds silhouette sunset",
    "seabirds silhouette golden hour",
    "bird silhouette dawn sky",
    # golden hour / atmospheric
    "heron golden hour water",
    "great blue heron sunset",
    "egret sunset water",
    "swan sunrise lake",
    "flamingo sunset wading",
    "eagle golden hour flying",
    "owl sunset flying",
    # macro / close-up with motion or personality
    "hummingbird close up flower macro",
    "hummingbird wings slow motion",
    "owl close up eyes blinking",
    "eagle head close up",
    "parrot eye close up",
    "kingfisher feathers close up",
    "bird feather macro wind",
    "pelican head close up",
    # water + splash atmospheres
    "duck takeoff water splash slow motion",
    "swan wings spreading water sunset",
    "egret wings flapping water",
    "flamingo flock wading",
    "heron wading sunset close up",
    # flocks / murmurations
    "starling murmuration sunset",
    "flock geese flying sunset",
    "flock cranes flying sunset",
    "seagulls flying cliff sunset",
    # slow-motion flight + landing
    "owl flying slow motion",
    "eagle landing slow motion",
    "snowy owl flying snow",
    "bald eagle flying slow motion",
    "macaw flying rainforest slow motion",
    "puffin flying cliff sea",
    # non-bird wildlife fallback (cinematic)
    "sea turtle swimming ocean sunset",
    "monarch butterfly macro slow motion",
    "coral reef fish slow motion",
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
    "house", "houses", "home", "homes", "residential", "neighborhood",
    "suburban", "backyard", "garden bench", "power line", "powerline",
    "telephone pole", "fence post",
}

# Captivity + human-structure blacklist. Birds must be in the wild.
CAPTIVITY_MARKERS = {
    "zoo", "aviary", "cage", "caged", "enclosure", "captive", "captivity",
    "pet", "pets", "domestic", "domesticated", "shoulder", "hand", "owner",
    "handler", "feeder", "feeding hand", "perched on finger",
    "fence", "fenced", "wire", "bars", "chain link", "chainlink",
    "rooftop", "roof", "indoor", "indoors", "window", "ceiling",
    "porch", "balcony", "birdhouse", "farm", "poultry", "chicken coop",
    "aquarium", "tank", "net", "fishing net", "trap", "trapped",
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

# Bird ACTION words. Bird clips must contain at least one of these to pass
# (i.e. no static perched/resting shots). This encodes the rule: "I don't
# just want shots of birds sitting not doing anything."
BIRD_ACTION_TERMS = {
    # flight
    "flying", "flight", "soaring", "gliding", "hovering", "takeoff",
    "landing", "wingspan", "in-flight", "airborne", "flapping", "swooping",
    # hunting / feeding
    "hunting", "catching", "diving", "fishing", "feeding", "preying",
    "pouncing", "striking", "plunging", "snatching",
    # interaction w/ nature
    "nectar", "flower", "pollinating", "drinking", "bathing", "splashing",
    "swimming", "wading",
    # display / social / vocal
    "displaying", "courtship", "dancing", "mating", "calling", "singing",
    "squawking", "hooting", "cawing",
    "flock", "murmuration", "swarm",
    # nest
    "building-nest", "building nest", "chicks", "hatchling", "fledgling",
    # misc subtle motion
    "running", "spreading", "stretching", "preening", "shaking",
    "blinking", "looking", "watching", "tilting", "turning",
}

# Cinematic-quality cues that also let a bird clip pass the "action" gate.
# The user wants flight silhouettes, sunset/golden-hour scenes, and macro
# close-ups — these are legit even when the clip doesn't show overt action.
BIRD_CINEMATIC_TERMS = {
    "silhouette", "silhouetted",
    "sunset", "sunrise", "golden hour", "dusk", "dawn",
    "close up", "closeup", "macro",
    "slow motion", "slow-motion", "slowmo",
    "cinematic",
}

# Words indicating a static, motionless shot. If these are present AND no
# action word is, the clip is rejected. Pure "perched" or "resting" shots
# should not make it through unless the bird is doing something.
STATIC_MARKERS = {
    "resting", "still", "stationary", "sleeping", "asleep", "immobile",
    "motionless", "posing", "portrait",
}

# Queries that are bird subjects (vs non-bird). Used to decide whether to
# apply BIRD_TERMS requirement. Match if any bird term appears in the query.
def _query_is_bird(query: str) -> bool:
    ql = query.lower()
    return any(term in ql for term in BIRD_TERMS) or any(
        word in ql for word in ("songbird", "shorebird", "raptor", "bird", "avian")
    )


def _has_urban(tags: list[str], text: str = "") -> bool:
    t = _normalize(tags, text)
    return any(re.search(rf"\b{re.escape(m)}\b", t) for m in URBAN_MARKERS)


def _has_captivity(tags: list[str], text: str = "") -> bool:
    t = _normalize(tags, text)
    return any(re.search(rf"\b{re.escape(m)}\b", t) for m in CAPTIVITY_MARKERS)


def _has_bird(tags: list[str], text: str = "") -> bool:
    t = _normalize(tags, text)
    return any(re.search(rf"\b{re.escape(m)}\b", t) for m in BIRD_TERMS)


def _has_action(tags: list[str], text: str = "") -> bool:
    """True if the clip's tags/slug contain any bird-action word."""
    t = _normalize(tags, text)
    return any(re.search(rf"\b{re.escape(m)}\b", t) for m in BIRD_ACTION_TERMS)


def _has_cinematic(tags: list[str], text: str = "") -> bool:
    """True if tags/slug suggest a cinematic shot (silhouette, sunset, macro).
    These pass the action gate even without overt motion."""
    t = _normalize(tags, text)
    return any(re.search(rf"\b{re.escape(m)}\b", t) for m in BIRD_CINEMATIC_TERMS)


def _has_static_marker(tags: list[str], text: str = "") -> bool:
    t = _normalize(tags, text)
    return any(re.search(rf"\b{re.escape(m)}\b", t) for m in STATIC_MARKERS)

MIN_WIDTH = 1920   # Full HD minimum; prefer 4K
MIN_HEIGHT = 1920
MIN_DURATION = 8   # targeting ~10s Reels, so 8s+ source is fine


# ----------------------------- helpers ---------------------------------

def _normalize(tags: list[str], text: str) -> str:
    """Lowercase, then treat hyphens/underscores/slashes as word separators.

    Pexels/Pixabay URL slugs use hyphens ("telephone-pole"), but our markers
    are declared with spaces. Normalize both sides to the same form.
    """
    raw = " ".join(tags + [text]).lower()
    return re.sub(r"[-_/]+", " ", raw)


def _has_human(tags: list[str], text: str = "") -> bool:
    t = _normalize(tags, text)
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

    # Prefer 4K+ ("size=large"). If that yields too few, retry without the
    # size filter — getting *something* beats getting nothing.
    def _search(size: str | None) -> list:
        params = {
            "query": query, "per_page": max(count * 6, 20),
            "orientation": "portrait",
        }
        if size:
            params["size"] = size
        try:
            r = requests.get("https://api.pexels.com/videos/search",
                             headers=headers, params=params, timeout=30)
            r.raise_for_status()
            return r.json().get("videos", [])
        except Exception as e:
            log.error("Pexels search failed (%s): %s", size, e)
            return []

    results = _search("large")
    if len(results) < count * 2:
        fallback = _search(None)
        # merge, preferring large first
        seen = {str(v.get("id")) for v in results}
        results += [v for v in fallback if str(v.get("id")) not in seen]

    # Sort by resolution descending so the highest-quality candidates are
    # tried first.
    results.sort(
        key=lambda v: (v.get("width") or 0) * (v.get("height") or 0),
        reverse=True,
    )
    log.info("Pexels: %d candidates for %r (size=large preferred)",
             len(results), query)
    out: list[Path] = []
    require_bird = _query_is_bird(query)
    skipped = {"human": 0, "urban": 0, "captive": 0, "no_bird_term": 0,
               "no_action": 0, "too_short": 0, "low_res": 0}
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
        # Action rule: bird clips must show the bird doing something. Either
        # the tags/slug explicitly say so (flying/hunting/feeding/displaying)
        # or, if the clip carries a STATIC marker (resting/still/posing), it
        # must ALSO have an action marker. Otherwise skip.
        if require_bird:
            has_action = _has_action(tags, text=text_blob)
            has_cinematic = _has_cinematic(tags, text=text_blob)
            has_static = _has_static_marker(tags, text=text_blob)
            # Pass if either action OR cinematic marker present. A bare
            # "static" clip (resting/posing) without any action or
            # cinematic cue is rejected.
            if not (has_action or has_cinematic):
                skipped["no_action"] += 1
                continue
            # If the ONLY signal is a static marker with no action or
            # cinematic quality, reject.
            if has_static and not has_action and not has_cinematic:
                skipped["no_action"] += 1
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

    def _search(editors_choice: bool) -> list:
        params = {
            "key": cfg.pixabay_api_key,
            "q": query,
            "per_page": max(count * 6, 20),
            "video_type": "film",
            "safesearch": "true",
            "category": "animals,nature,backgrounds",
            "min_width": MIN_WIDTH,
            "min_height": MIN_HEIGHT,
            "order": "popular",
            "editors_choice": "true" if editors_choice else "false",
        }
        try:
            r = requests.get("https://pixabay.com/api/videos/", params=params, timeout=30)
            r.raise_for_status()
            return r.json().get("hits", [])
        except Exception as e:
            log.error("Pixabay search failed (editors=%s): %s", editors_choice, e)
            return []

    # Try editor-curated first; fall back to any if thin.
    hits = _search(editors_choice=True)
    if len(hits) < count * 2:
        more = _search(editors_choice=False)
        seen = {str(h.get("id")) for h in hits}
        hits += [h for h in more if str(h.get("id")) not in seen]

    # Sort by the largest available rendition's area, descending.
    def _area(h: dict) -> int:
        videos = h.get("videos", {}) or {}
        large = videos.get("large") or videos.get("medium") or {}
        return (large.get("width") or 0) * (large.get("height") or 0)

    hits.sort(key=_area, reverse=True)
    log.info("Pixabay: %d candidates for %r (editors_choice preferred)",
             len(hits), query)
    out: list[Path] = []
    require_bird = _query_is_bird(query)
    skipped = {"human": 0, "urban": 0, "captive": 0, "no_bird_term": 0,
               "no_action": 0, "too_short": 0, "low_res": 0}
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
        if require_bird and not (_has_action(tags, text=text_blob)
                                  or _has_cinematic(tags, text=text_blob)):
            skipped["no_action"] += 1
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
