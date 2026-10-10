"""Deterministic local image cleanup before PP-OCRv6 recognition."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class PreprocessConfig:
    algorithm: str = "light-gray-red-v2-preserve-dark-edge"
    enabled: bool = True
    gray_threshold: int = 120
    red_min: int = 140
    red_delta: int = 60

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def preprocess_ocr_image(
    image: np.ndarray, config: PreprocessConfig,
) -> tuple[np.ndarray, dict[str, int]]:
    """Whiten light gray background/watermarks and red stamp pixels.

    Dark foreground is left unchanged.  The operation is intentionally simple,
    deterministic, and independently testable; OCR remains local.
    """
    if image.ndim != 3 or image.shape[2] < 3:
        raise ValueError("OCR preprocessing requires an RGB image")
    if not config.enabled:
        return image.copy(), {"gray_pixels_whitened": 0,
                              "gray_pixels_preserved_near_dark": 0,
                              "red_pixels_whitened": 0}
    if not 0 <= config.gray_threshold <= 255:
        raise ValueError("gray_threshold must be in [0,255]")
    rgb = image[:, :, :3].astype(np.int16, copy=False)
    red = (
        (rgb[:, :, 0] >= config.red_min)
        & ((rgb[:, :, 0] - rgb[:, :, 1]) >= config.red_delta)
        & ((rgb[:, :, 0] - rgb[:, :, 2]) >= config.red_delta)
    )
    intensity = np.mean(rgb, axis=2)
    gray_like = (
        (np.max(rgb, axis=2) - np.min(rgb, axis=2) <= 12)
        & (intensity > config.gray_threshold)
        & (intensity < 250)
    )
    # Keep anti-aliased edge pixels that touch genuine dark foreground.  This
    # prevents thresholding from eroding printed strokes while still removing
    # isolated light-gray watermark/background components.
    dark = intensity <= config.gray_threshold
    near_dark = dark.copy()
    height, width = dark.shape
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if not (dx or dy):
                continue
            src_y = slice(max(0, -dy), min(height, height - dy))
            src_x = slice(max(0, -dx), min(width, width - dx))
            dst_y = slice(max(0, dy), min(height, height + dy))
            dst_x = slice(max(0, dx), min(width, width + dx))
            near_dark[dst_y, dst_x] |= dark[src_y, src_x]
    preserved_gray = gray_like & near_dark
    gray_like &= ~near_dark
    cleaned = image.copy()
    cleaned[gray_like | red, :3] = 255
    return cleaned, {
        "gray_pixels_whitened": int(np.count_nonzero(gray_like & ~red)),
        "gray_pixels_preserved_near_dark": int(np.count_nonzero(preserved_gray)),
        "red_pixels_whitened": int(np.count_nonzero(red)),
    }
