"""Build the 'static' slide of a Static+Video carousel post.

Pipeline:
  1. Pull a frame from the video (ffmpeg).
  2. Apply cinematic color grade (contrast, desat, vignette, film grain).
  3. Overlay hook text in a transitional serif (Playfair Display).

Output is 1080x1920 JPEG, ready to upload alongside the video.
"""
from __future__ import annotations

import logging
import math
import random
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from .config import PROJECT_ROOT
from .post_styles import StaticVideoStyle

log = logging.getLogger(__name__)

CANVAS_W = 1080
CANVAS_H = 1350   # 4:5 — the aspect ratio Instagram uses for feed carousels


# ----------------------------- frame extraction -------------------------

def _probe_duration(video: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def extract_frame(video: Path, out_png: Path, fraction: float = 0.25) -> Path | None:
    """Save a single frame at `fraction` of video duration."""
    dur = _probe_duration(video)
    ts = max(0.0, dur * fraction)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{ts:.2f}",
        "-i", str(video),
        "-frames:v", "1",
        "-q:v", "2",
        str(out_png),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out_png.exists():
        log.error("extract_frame failed: %s", r.stderr.strip()[-300:])
        return None
    return out_png


# ----------------------------- cinematic grading -----------------------

def _vignette(img: Image.Image, strength: float) -> Image.Image:
    if strength <= 0:
        return img
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    # Elliptical gradient: bright center, dark edges.
    cx, cy = w / 2, h / 2
    max_r = math.hypot(cx, cy)
    # Draw a series of concentric ellipses with decreasing brightness outward.
    # Implementation: radial gradient via downsampling a gaussian-ish mask.
    # Easier approach: build a gradient image with numpy-free math.
    for y in range(h):
        dy = (y - cy) / cy
        for x in range(0, w, 4):  # stride 4 for speed, we'll blur after
            dx = (x - cx) / cx
            d = math.sqrt(dx * dx + dy * dy)
            v = max(0.0, min(1.0, 1.0 - d))  # 1 at center, 0 at corner
            mask.putpixel((x, y), int(v * 255))
    mask = mask.resize((w, h)).filter(ImageFilter.GaussianBlur(radius=60))
    # Blend: darken by (1 - mask_norm * strength)
    dark = Image.new("RGB", (w, h), (0, 0, 0))
    # mask value 255 = keep original; 0 = fully dark. Multiply strength.
    # Convert so mask actually represents "keep" amount.
    # Pillow composite(im1, im2, mask) uses mask=0 -> im1, 255 -> im2.
    # We want center = keep img, edges = dark. So mask at center should = 255 (img), edges = 0 (dark)... wait inverse.
    # Using Image.composite(fg, bg, mask): where mask=255, fg shows; where mask=0, bg shows.
    # So fg=img (center visible), bg=dark (edges), mask bright at center -> correct.
    # But we want only partial darkening. Blend mask with white first according to strength.
    white = Image.new("L", (w, h), 255)
    mask = Image.blend(white, mask, strength)
    return Image.composite(img, dark, mask)


def _grain(img: Image.Image, strength: float) -> Image.Image:
    if strength <= 0:
        return img
    w, h = img.size
    noise = Image.effect_noise((w, h), sigma=strength * 255)
    noise_rgb = Image.merge("RGB", (noise, noise, noise))
    # Blend with weight = strength * 0.25 so it stays subtle.
    return Image.blend(img, noise_rgb, min(0.25, strength * 5))


def cinematic_grade(
    img: Image.Image,
    contrast: float,
    saturation: float,
    vignette_strength: float,
    grain_strength: float,
) -> Image.Image:
    out = img.convert("RGB")
    out = ImageEnhance.Contrast(out).enhance(contrast)
    out = ImageEnhance.Color(out).enhance(saturation)
    out = _vignette(out, vignette_strength)
    out = _grain(out, grain_strength)
    return out


# ----------------------------- canvas shaping --------------------------

def _fit_portrait(img: Image.Image, w: int = CANVAS_W, h: int = CANVAS_H) -> Image.Image:
    """Scale & center-crop to 1080x1920 portrait."""
    sw, sh = img.size
    src_ratio = sw / sh
    dst_ratio = w / h
    if src_ratio > dst_ratio:
        # source is wider than target; scale to target h, crop sides
        new_h = h
        new_w = int(sw * (h / sh))
    else:
        new_w = w
        new_h = int(sh * (w / sw))
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))


# ----------------------------- text overlay ---------------------------

def _wrap(text: str, max_chars: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= max_chars:
            cur = f"{cur} {word}"
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _measure(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Path,
    initial_size: int,
    safe_width: int,
    max_lines: int = 3,
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Pick a font size + line wrap so every line fits within safe_width.

    Tries the initial size first, wrapping greedily word-by-word; if any line
    still exceeds safe_width (e.g. one huge word), shrinks the font in 6%
    steps until it fits. Returns (font, wrapped_lines).
    """
    words = text.split()
    size = initial_size
    while size >= initial_size * 0.5:
        try:
            font = ImageFont.truetype(str(font_path), size)
        except OSError:
            font = ImageFont.load_default()
            return font, [text]

        # Greedy wrap by pixel width
        lines: list[str] = []
        cur = ""
        overflow = False
        for word in words:
            trial = f"{cur} {word}".strip()
            w, _ = _measure(draw, trial, font)
            if w <= safe_width:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                # single word might itself overflow
                ww, _ = _measure(draw, word, font)
                if ww > safe_width:
                    overflow = True
                    break
                cur = word
        if cur:
            lines.append(cur)

        if not overflow and len(lines) <= max_lines:
            return font, lines

        # shrink and retry
        size = int(size * 0.94)

    # fallback: use last attempted size even if imperfect
    font = ImageFont.truetype(str(font_path), max(size, int(initial_size * 0.5)))
    lines = [text]
    return font, lines


def overlay_text(
    img: Image.Image,
    text: str,
    style: StaticVideoStyle,
) -> Image.Image:
    out = img.convert("RGBA")
    w, h = out.size

    font_path = PROJECT_ROOT / style.font_path
    initial_font_size = int(h * style.text_size_ratio)
    safe_width = int(w * 0.86)

    draw_probe = ImageDraw.Draw(out)
    font, lines = _fit_text(
        draw_probe, text, font_path, initial_font_size, safe_width
    )
    font_size = font.size

    line_heights = []
    line_widths = []
    for line in lines:
        lw, lh = _measure(draw_probe, line, font)
        line_widths.append(lw)
        line_heights.append(lh)
    line_gap = int(font_size * 0.25)
    block_h = sum(line_heights) + line_gap * (len(lines) - 1)

    # position
    x_pad = int(w * 0.06)
    if style.text_placement == "top":
        y_start = int(h * 0.12)
    elif style.text_placement == "center":
        y_start = (h - block_h) // 2
    else:  # bottom
        y_start = h - block_h - int(h * 0.09)

    # optional bottom gradient (Washington-Post look)
    if style.bottom_gradient and style.text_placement == "bottom":
        grad = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(grad)
        grad_top = int(h * 0.55)
        for y in range(grad_top, h):
            a = int(((y - grad_top) / (h - grad_top)) ** 1.6 * 180)
            gdraw.line([(0, y), (w, y)], fill=(0, 0, 0, a))
        out = Image.alpha_composite(out, grad)

    draw = ImageDraw.Draw(out)
    y = y_start
    r, g, b = style.text_color
    for line, lw, lh in zip(lines, line_widths, line_heights):
        x = (w - lw) // 2
        if style.text_shadow:
            # soft shadow: two offsets, semi-transparent black.
            for dx, dy, alpha in [(0, 3, 140), (2, 5, 90)]:
                draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, alpha))
        draw.text((x, y), line, font=font, fill=(r, g, b, 255))
        y += lh + line_gap

    return out.convert("RGB")


# ----------------------------- top-level --------------------------------

def build_static(
    video_path: Path,
    hook_text: str,
    style: StaticVideoStyle,
    out_jpg: Path,
) -> Path | None:
    """Create the static slide. Returns output path or None."""
    frame_png = out_jpg.with_suffix(".frame.png")
    try:
        if not extract_frame(video_path, frame_png, style.frame_fraction):
            return None
        img = Image.open(frame_png).convert("RGB")
        img = _fit_portrait(img)
        img = cinematic_grade(
            img,
            contrast=style.contrast,
            saturation=style.saturation,
            vignette_strength=style.vignette_strength,
            grain_strength=style.grain_strength,
        )
        img = overlay_text(img, hook_text, style)
        out_jpg.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_jpg, "JPEG", quality=92, optimize=True)
        log.info("static: %s (%d bytes)", out_jpg.name, out_jpg.stat().st_size)
        return out_jpg
    finally:
        frame_png.unlink(missing_ok=True)
