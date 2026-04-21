"""Caption generation via Claude. Enforces the voice rules from SPEC.md."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from anthropic import Anthropic

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-5"

SYSTEM_PROMPT = """You write Instagram captions for a wildlife/conservation account focused on birds. The voice is informed, a little confrontational, and built to make people stop scrolling. The caption is ONE hook line followed by cohesive narrative prose. It is not a list of punchy one-liners.

SPECIES ACCURACY — ABSOLUTE PRIORITY.

The clip metadata contains two species-related fields:
  - `query_used`: the search term used to FIND the clip (may be wrong)
  - `species_verified`: the species actually shown in the clip, extracted from the source platform's own title/slug

You MUST write the caption about `species_verified` if it is present. Ignore `query_used` for species identification. They are frequently different — e.g. a search for "bald eagle" may return a "white-bellied sea eagle" clip. Writing about the wrong species is a catastrophic error.

If `species_verified` is empty or "unknown", write the caption in generic terms ("the heron," "these birds," "a raptor at dusk") — do NOT name a species you cannot confirm. Generic but correct beats specific but wrong.

When writing conservation specifics (population numbers, threat history, legal protections, geographic range), they must apply to `species_verified` — NOT to a different species the reader might expect. A white-bellied sea eagle is NOT a bald eagle: different continent, different listing status, different threats. If you are unsure of the exact conservation facts for the verified species, write about a threat category that clearly applies to the broader genus or habitat, and keep claims conservative.


Shape:

1. HOOK (line 1, standalone): Short. Accusatory, provocative, or quietly devastating. One fact or claim that indicts a comfortable assumption. Should make the reader stop scrolling.

2. BODY: 85-140 words of flowing, cohesive prose. Most important rule: SENTENCES MUST CHAIN. Each sentence responds to, extends, or complicates the one before it. Thread the argument with connective words: because, yet, but, since, meanwhile, and by then, when, while, even so, after, until. Sentence length must vary: most sentences are medium to long; short sentences are RARE and used only for rhythm. The body must NOT read as a sequence of separate hooks. It reads as a paragraph, a story, an argument unfolding. Standard prose, not punchy fragments.

Content focus for the body: the body spends most of its ink on the DANGERS the bird is currently facing. Name the exact threat (a specific pesticide, lead ammunition, wind turbines, a specific crop or industry, a named disease, a specific development pattern), cite real numbers or years, and place it in historical context. Weave in one or two facts about what makes the bird remarkable as the set-up before the threat lands. Never moralize. Never call to action. Let the facts land on their own.

Here is a reference caption that has the exact voice, flow, and structure expected. Match this rhythm:

---
Four hundred seventeen pairs. That was all.

That was the entire bald eagle population of the contiguous United States in 1963, after DDT had spent two decades thinning their eggshells until mothers crushed their own clutches simply by sitting on them. The pesticide was banned in 1972 and the population has since climbed past seventy thousand pairs, yet the species is not out of danger. Lead poisoning from spent hunting ammunition is now killing bald eagles faster than anything since DDT itself, because when an eagle scavenges the gut pile a hunter leaves behind, a single lead fragment smaller than a pencil tip is enough to trigger seizures and death within days. Thousands of eagles die this way every year, and the ammunition that kills them remains legal across most of the country.
---

Voice rules - absolute:
- No em dashes. Ever. Use periods, commas, or semicolons.
- No hashtags in the caption body. Return them separately.
- No emojis.
- 90-160 words total (hook + body).
- Never use: "dive in," "let's explore," "did you know," "stunning," "amazing," "incredible," "mind-blowing," "you won't believe," "fun fact," "imagine if," "the beauty of," "majestic creature," "nature's wonder."
- Praise by fact, not by adjective.
- Don't soften the hook with qualifiers. "Maybe you've been lied to" is dead. "You've been lied to" lives.

Return JSON: {"caption": "...", "hashtags": ["#tag1", "#tag2", ...]}
15-20 hashtags, mix of broad (#wildlife, #conservation, #birding) and niche (species + threat specific)."""

BANNED_PHRASES = [
    "dive in", "let's explore", "did you know", "stunning", "amazing",
    "mind-blowing", "you won't believe", "fun fact", "imagine if",
]


def _strip_em_dashes(s: str) -> str:
    # Replace em/en dashes with a period+space or comma where appropriate.
    # Conservative: swap for a period if capital letter follows, else comma.
    def repl(m: re.Match) -> str:
        tail = s[m.end():m.end() + 2].lstrip()
        return ". " if tail and tail[0].isupper() else ", "

    return re.sub(r"\s*[\u2014\u2013]\s*", repl, s)


def _extract_json(text: str) -> dict[str, Any]:
    # Try plain parse, then fenced block, then first {...} span.
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        return json.loads(brace.group(0))
    raise ValueError("no JSON found in model output")


def _validate(caption: str, hashtags: list[str]) -> list[str]:
    warnings: list[str] = []
    lower = caption.lower()
    if "\u2014" in caption or "\u2013" in caption or "--" in caption:
        warnings.append("contains em/en dashes (will be stripped)")
    for bad in BANNED_PHRASES:
        if bad in lower:
            warnings.append(f"banned phrase: {bad!r}")
    wc = len(caption.split())
    if wc < 80 or wc > 180:
        warnings.append(f"word count out of band: {wc}")
    if not (12 <= len(hashtags) <= 22):
        warnings.append(f"hashtag count off: {len(hashtags)}")
    return warnings


def _load_llm_sidecar(clip_path) -> dict | None:
    """Return contents of <clip>.llm.json if present, else None."""
    if clip_path is None:
        return None
    from pathlib import Path
    p = Path(clip_path).with_suffix(".llm.json")
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        log.warning("llm sidecar unreadable: %s", p)
        return None


def generate_caption(clip_metadata: dict, api_key: str, clip_path=None) -> dict[str, Any]:
    """Return {caption, hashtags, warnings}.

    If a `<clip>.llm.json` sidecar exists with caption+hashtags already set
    (the Option A path: generated ahead-of-time by a scheduled Claude Code
    trigger), use that and skip the API call entirely.
    """
    side = _load_llm_sidecar(clip_path)
    if side and side.get("caption") and side.get("hashtags"):
        log.info("using pre-generated caption from llm sidecar")
        caption = _strip_em_dashes(str(side["caption"]).strip())
        hashtags = [
            h if str(h).startswith("#") else f"#{h}"
            for h in side["hashtags"]
            if str(h).strip()
        ]
        warnings = _validate(caption, hashtags)
        for w in warnings:
            log.warning("caption (sidecar) validation: %s", w)
        return {"caption": caption, "hashtags": hashtags, "warnings": warnings}

    if not api_key:
        raise RuntimeError(
            "no LLM sidecar found and ANTHROPIC_API_KEY is not set. "
            "Either run the scheduled trigger to pre-generate copy, or set the API key."
        )

    client = Anthropic(api_key=api_key)

    species_verified = clip_metadata.get("species_verified") or ""
    query_used = clip_metadata.get("query_used") or ""
    user_parts = [
        f"Source: {clip_metadata.get('source', 'unknown')}",
        f"Source URL: {clip_metadata.get('source_url', '')}",
        f"species_verified: {species_verified or '(none — write generically, do not name a species)'}",
        f"query_used (may differ from actual subject, DO NOT trust for species ID): {query_used}",
        f"Title: {clip_metadata.get('title', '')}",
    ]
    desc = (clip_metadata.get("description") or "").strip()
    if desc:
        user_parts.append(f"Description: {desc[:800]}")
    user_parts.append(
        "\nWrite the caption now. Return only the JSON object, no preamble. "
        "Remember: species_verified is the truth. If it is empty, stay generic."
    )

    log.info("generating caption for: %s", clip_metadata.get("title", "?")[:60])
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": "\n".join(user_parts)}],
    )
    text = "".join(
        b.text for b in resp.content if getattr(b, "type", "") == "text"
    )
    data = _extract_json(text)
    caption = str(data.get("caption", "")).strip()
    hashtags = [str(h).strip() for h in data.get("hashtags", []) if str(h).strip()]
    hashtags = [h if h.startswith("#") else f"#{h}" for h in hashtags]

    warnings = _validate(caption, hashtags)
    caption = _strip_em_dashes(caption)

    for w in warnings:
        log.warning("caption validation: %s", w)

    return {"caption": caption, "hashtags": hashtags, "warnings": warnings}


def compose_final(caption: str, hashtags: list[str]) -> str:
    """Format for IG: caption, blank line, space-separated hashtags."""
    tags = " ".join(hashtags)
    return f"{caption}\n\n{tags}"


# ----------------------- hook text for static slide --------------------

HOOK_SYSTEM_PROMPT = """You write the single-line hook that appears on the static slide of an Instagram carousel for a wildlife/conservation account.

The hook sits over a cinematic photograph of a bird in the wild. It is the only text on the image. The viewer reads it in under two seconds and then swipes to the video.

SPECIES ACCURACY: the clip metadata contains `species_verified` (the actual species shown, from the source platform's own title) and `query_used` (the search term that found it — may be wrong). If `species_verified` is present, the hook MUST be about that species. If it is empty, write a generic hook that does not name a specific species. Never name a species you cannot confirm from the metadata.

Write one line only. Rules:
- 4 to 10 words. Tight.
- Must feel specific to THIS clip's subject (species, behavior, environment). Not generic.
- Tone: tender, haunting, provocative, or quietly devastating. Never cute. Never corporate.
- No em dashes. Ever. Use periods, commas, or nothing.
- No emojis.
- No hashtags.
- No question marks unless the question is load-bearing.
- No banned phrases: "did you know", "stunning", "amazing", "mind-blowing", "you won't believe", "imagine if", "fun fact", "dive in", "let's explore", "the beauty of", "majestic creature".
- Don't describe the photo. Don't say what the bird is doing. Say something the viewer won't have thought.
- Don't moralize. Don't call to action.

Good examples (different subjects, different energies):
  The last one hatched in 1987.
  It remembers every face it has ever seen.
  Built for silence. Designed to kill.
  Older than language.
  The wingspan crosses oceans twice a year.
  No parent taught it this song.

Return JSON: {"hook": "your single line here"}"""


def _hook_user_message(clip_metadata: dict) -> str:
    species_verified = clip_metadata.get("species_verified") or ""
    query_used = clip_metadata.get("query_used") or ""
    subject = clip_metadata.get("subject") or clip_metadata.get("title") or ""
    parts = [
        f"species_verified: {species_verified or '(none — write a generic hook, do not name a species)'}",
        f"query_used (may be wrong, DO NOT trust for species ID): {query_used}",
        f"Subject: {subject}",
    ]
    desc = (clip_metadata.get("description") or "").strip()
    if desc:
        parts.append(f"Notes from source: {desc[:400]}")
    parts.append("\nReturn JSON only. Remember: species_verified is truth.")
    return "\n".join(parts)


def generate_hook(clip_metadata: dict, api_key: str, clip_path=None) -> str:
    """Return a single hook line for the static slide.

    Prefers pre-generated `hook` from <clip>.llm.json if present.
    """
    side = _load_llm_sidecar(clip_path)
    if side and side.get("hook"):
        hook = _strip_em_dashes(str(side["hook"]).strip())
        log.info("using pre-generated hook from llm sidecar")
        return hook

    if not api_key:
        raise RuntimeError(
            "no LLM sidecar found and ANTHROPIC_API_KEY is not set. "
            "Either run the scheduled trigger to pre-generate copy, or set the API key."
        )

    client = Anthropic(api_key=api_key)
    user = _hook_user_message(clip_metadata)
    log.info("generating hook for species=%r (query=%r)",
             clip_metadata.get("species_verified", ""),
             clip_metadata.get("query_used", ""))
    resp = client.messages.create(
        model=MODEL,
        max_tokens=200,
        system=HOOK_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    try:
        hook = str(_extract_json(text).get("hook", "")).strip()
    except (ValueError, json.JSONDecodeError):
        log.error("hook: model returned non-JSON: %r", text[:200])
        hook = text.strip().strip('"').splitlines()[0]

    hook = _strip_em_dashes(hook)
    # Extra safety: strip trailing period if the line is a fragment (<6 words).
    wc = len(hook.split())
    if wc < 6 and hook.endswith("."):
        hook = hook[:-1]
    return hook
