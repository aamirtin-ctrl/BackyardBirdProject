"""Caption generation via Claude. Enforces the voice rules from SPEC.md."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from anthropic import Anthropic

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-5"

SYSTEM_PROMPT = """You write Instagram captions for a documentary clips account. The voice is informed, a little confrontational, and built to make people stop scrolling.

Structure - every caption follows this:
1. HOOK (line 1, standalone): Accusatory or provocative. Calls out a comfortable assumption, a quiet hypocrisy, or something the viewer probably hasn't thought about. Should make the reader feel slightly indicted or unsettled. Examples of the right energy:
   "You've been lied to about how forests grow back."
   "The fish on your plate spent its life screaming. You just couldn't hear it."
   "Every plastic bag you've ever used still exists. All of them."
   "We pretend extinction is slow. It isn't."
2. BODY (3-6 sentences): Drop the accusation, shift into storytelling and information. Explain what's actually going on. Use specifics, numbers, names, places. The reader should walk away knowing something concrete they didn't know before. Narrative tone, not lecture tone.
3. CLOSE (1-2 sentences): Land it. Don't moralize or call to action. Let the fact do the work.

Voice rules - absolute:
- No em dashes. Ever. Use periods or commas.
- Short sentences used sparingly. The hook is short. Most body sentences are medium length, varied rhythm.
- No hashtags in the caption body. Return them separately.
- No emojis.
- 90-160 words total.
- Never use: "dive in," "let's explore," "did you know," "stunning," "amazing," "mind-blowing," "you won't believe," "fun fact," "imagine if."
- Don't moralize at the end. No "we need to do better." No "the choice is ours." Trust the reader.
- Don't soften the hook with qualifiers. "Maybe you've been lied to" is dead. "You've been lied to" lives.

Return JSON: {"caption": "...", "hashtags": ["#tag1", "#tag2", ...]}
15-20 hashtags, mix of broad (#documentary) and niche (#deepseaecology)."""

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

    user_parts = [
        f"Source: {clip_metadata.get('source', 'unknown')}",
        f"Source URL: {clip_metadata.get('source_url', '')}",
        f"Title: {clip_metadata.get('title', '')}",
    ]
    desc = (clip_metadata.get("description") or "").strip()
    if desc:
        user_parts.append(f"Description: {desc[:800]}")
    subject = clip_metadata.get("subject") or clip_metadata.get("title") or ""
    user_parts.append(f"Subject matter: {subject}")
    user_parts.append(
        "\nWrite the caption now. Return only the JSON object, no preamble."
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
    subject = (
        clip_metadata.get("subject")
        or clip_metadata.get("title")
        or "a bird in the wild"
    )
    desc = (clip_metadata.get("description") or "").strip()
    user = f"Subject: {subject}"
    if desc:
        user += f"\nNotes from source: {desc[:400]}"
    user += "\n\nReturn JSON only."

    log.info("generating hook for: %s", subject[:60])
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
