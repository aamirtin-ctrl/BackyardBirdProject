"""Post style registry. Each style defines the publish flow + visual params.

Default: "static_video" — carousel with a cinematic graded still (w/ hook text)
as slide 1 and the original video as slide 2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Placement = Literal["top", "center", "bottom"]


@dataclass(frozen=True)
class StaticVideoStyle:
    name: str = "static_video"
    # which frame of the video to grab for the static slide (seconds from start).
    # 0.25 = 25% into the clip, usually past any fade-in.
    frame_fraction: float = 0.25

    # cinematic grading
    contrast: float = 1.12        # 1.0 = no change
    saturation: float = 0.88      # <1 desaturates
    vignette_strength: float = 0.35  # 0 none, 1 heavy
    grain_strength: float = 0.02  # 0 none, 1 very heavy

    # text
    font_path: str = "fonts/PlayfairDisplay-Bold.ttf"
    text_placement: Placement = "top"   # "top" matches the owl/Second Nature look
    text_color: tuple[int, int, int] = (255, 255, 255)
    text_max_chars_per_line: int = 28
    text_size_ratio: float = 0.055   # font size as fraction of canvas height
    text_shadow: bool = True         # subtle shadow for legibility
    bottom_gradient: bool = False    # True = Washington-Post style dark bottom band


STATIC_VIDEO = StaticVideoStyle()

REGISTRY = {STATIC_VIDEO.name: STATIC_VIDEO}


def get(name: str) -> StaticVideoStyle:
    if name not in REGISTRY:
        raise KeyError(f"unknown post style: {name!r}. known: {list(REGISTRY)}")
    return REGISTRY[name]
