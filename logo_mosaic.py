#!/usr/bin/env python3
"""
Spoonflower logo mosaic: 54" x 36" @ 150 DPI, mid-gray RGBA alpha paste.

Hard-capped with LANCZOS thumbnail (hero 220 / medium 150 / small 100 / micro 30–60).
Vector bbox + exact alpha packing with 2px grout; process-pool composite.

Copyright (c) 2026 Hillwork LLC
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import os
import random
import sys
import time
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

# ---------------------------------------------------------------------------
# Configuration & Parameters (edit these to re-theme or re-size)
# ---------------------------------------------------------------------------

CANVAS_WIDTH = 8100   # 54 inches @ 150 DPI
CANVAS_HEIGHT = 5400  # 36 inches @ 150 DPI
BACKGROUND_COLOR = "#808080"  # Mid-gray target color
OUTPUT_FILE = "./spoonflower_logo_mosaic_54x36.png"

PROJECT_DIR = Path(__file__).resolve().parent
LOGOS_DIR = PROJECT_DIR / "logos"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff"}

HERO_FRACTION = 0.10
MEDIUM_FRACTION = 0.35
SMALL_FRACTION = 0.35
MICRO_FRACTION = 0.20

# Hard pixel caps: max(width, height) via Image.thumbnail(..., LANCZOS).
ABSOLUTE_MAX_PX = 220  # Hero cap (~1.47" @ 150 DPI)
MIN_LOGO_PX = 25       # Micro floor
TIER_MAX = {
    "hero": 220,
    "medium": 150,
    "small": 100,
    "micro": 75,
}
TIER_MIN = {
    "micro": 25,
}

STROKE_PX = 2
STROKE_COLOR = (0, 0, 0)
LIGHT_MARK_LUMA = 140  # Light/white marks get a 2px dark outer stroke

MIN_DUPLICATE_DISTANCE = 500
BOX_PAD = 1              # 1px grout for dense sticker-bomb packing
MICRO_PAD = 1
TARGET_OCCUPANCY = 0.90
LAYOUT_SEED = 2026
PRIMARY_QUEUE = 4500
PLACE_TRIES = 250        # random samples per item (150–300)
PLACE_TRIES_MAX = 300
ALPHA_INK_MIN = 32       # exact-mask collision ignores near-transparent fringe

CPU_COUNT = os.cpu_count() or 8
RENDER_WORKERS = max(1, CPU_COUNT)

OUTPUT_DPI = 150

WHITE_LUMA_MIN = 210
BLACK_LUMA_MAX = 48
TAN_LUMA_MIN = 165
PAD_CHROMA_MAX = 50
CORNER_MATCH_TOL = 28
BG_FLOOD_TOL = 40
FRINGE_DESPILL = 52
BORDER_MATCH_MIN = 0.72
BORDER_STD_MAX = 8.0

# Filename keys match flexibly (exact name, stem, or normalized aliases like snow_bunny).
LOGO_OVERRIDES: dict[str, dict] = {
    "snow_bunny.png": {"invert": False},
    "billie_eilish.png": {},
    "lelas_bistro.png": {"invert": False},
    "lela_s": {},
    "takara_sushi": {"invert": True},
    "kann": {"invert": True},
    "function_logo": {"invert": True},
    "karaoke_from_hell": {"invert": True},
    "hey_luigi": {"invert": True},
    "janken": {"invert": True},
    "ringside": {"invert": True},
    "ovation": {"invert": True},
    "can_font": {"invert": True},
    "the_star": {"invert": True},
}


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def luma(rgb: tuple[int, ...]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def is_compression_box_color(rgb: tuple[int, ...]) -> bool:
    """True for faint white, tan/cream, or dark JPEG bounding boxes."""
    y = luma(rgb)
    chroma = max(rgb[0], rgb[1], rgb[2]) - min(rgb[0], rgb[1], rgb[2])
    if chroma > PAD_CHROMA_MAX:
        return False
    if y >= WHITE_LUMA_MIN:
        return True
    if y >= TAN_LUMA_MIN and rgb[0] >= rgb[2] - 8:
        return True
    if y <= BLACK_LUMA_MAX:
        return True
    return False


def channel_dist(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))


def similar_color(a: tuple[int, ...], b: tuple[int, ...], tol: int) -> bool:
    return channel_dist(a, b) <= tol


def discover_logo_paths() -> list[Path]:
    output_name = Path(OUTPUT_FILE).name.lower()
    paths: list[Path] = []
    for path in sorted(LOGOS_DIR.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if path.name.lower() == output_name:
            continue
        if path.name.lower().startswith(("logo_mosaic", "spoonflower_")):
            continue
        if path.name.startswith("_debug"):
            continue
        paths.append(path)
    return paths


def iter_border_pixels(img: Image.Image, step: int = 1):
    width, height = img.size
    pixels = img.load()
    for x in range(0, width, step):
        top = pixels[x, 0]
        bottom = pixels[x, height - 1]
        if top[3] >= 16:
            yield top
        if bottom[3] >= 16:
            yield bottom
    for y in range(0, height, step):
        left = pixels[0, y]
        right = pixels[width - 1, y]
        if left[3] >= 16:
            yield left
        if right[3] >= 16:
            yield right


def border_stats(img: Image.Image, bg_rgb: tuple[int, int, int]) -> tuple[float, float]:
    samples = list(iter_border_pixels(img, step=max(1, max(img.size) // 80)))
    if not samples:
        return 0.0, 999.0
    match = sum(1 for pixel in samples if channel_dist(pixel, bg_rgb) <= BG_FLOOD_TOL)
    lumas = [luma(pixel) for pixel in samples]
    mean = sum(lumas) / len(lumas)
    variance = sum((value - mean) ** 2 for value in lumas) / len(lumas)
    return match / len(samples), variance ** 0.5


def has_useful_alpha(img: Image.Image) -> bool:
    if img.mode in ("RGBA", "LA"):
        alpha = img.getchannel("A")
        extrema = alpha.getextrema()
        return extrema[0] < 250
    if img.mode == "P" and "transparency" in img.info:
        alpha = img.convert("RGBA").getchannel("A")
        extrema = alpha.getextrema()
        return extrema[0] < 250
    return False


def crop_to_alpha(img: Image.Image, pad: int = 1) -> Image.Image:
    bbox = img.getchannel("A").getbbox()
    if not bbox:
        return img
    left, top, right, bottom = bbox
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(img.width, right + pad)
    bottom = min(img.height, bottom + pad)
    return img.crop((left, top, right, bottom))


def normalize_override_key(name: str) -> str:
    stem = Path(name).stem.lower()
    if stem.startswith("zach "):
        stem = stem[5:]
    elif stem.startswith("zach_"):
        stem = stem[5:]
    cleaned = []
    for char in stem:
        if char.isalnum():
            cleaned.append(char)
        elif char in {" ", "-", "_"}:
            cleaned.append("_")
    return "".join(cleaned).strip("_").replace("__", "_")


def lookup_logo_override(path: Path) -> dict:
    aliases = {
        path.name,
        path.name.lower(),
        path.stem,
        path.stem.lower(),
        normalize_override_key(path.name),
        f"{normalize_override_key(path.name)}{path.suffix.lower()}",
        normalize_override_key(path.name).replace("_", ""),
    }
    for key, settings in LOGO_OVERRIDES.items():
        key_norm = normalize_override_key(key)
        compact = key_norm.replace("_", "")
        if key in aliases or key.lower() in aliases or key_norm in aliases or compact in aliases:
            return dict(settings)
    return {}


def opaque_luma_stats(gray: Image.Image, alpha: Image.Image) -> tuple[float, float, float]:
    g = np.asarray(gray, dtype=np.float64)
    a = np.asarray(alpha, dtype=np.uint8)
    mask = a >= 16
    count = int(mask.sum())
    if count == 0:
        return 128.0, 0.0, 0.0
    vals = g[mask]
    mean = float(vals.mean())
    variance = float(vals.var())
    fill = count / float(g.size)
    return mean, variance ** 0.5, fill


def to_soft_grayscale(img: Image.Image, override: dict | None = None) -> tuple[Image.Image, str]:
    """Keep anti-aliased grayscale pixels and the original alpha. No threshold, tint, or clip."""
    override = override or {}
    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")
    gray = ImageOps.grayscale(rgba.convert("RGB"))
    if override.get("invert"):
        gray = ImageOps.invert(gray)
    rgb = Image.merge("RGB", (gray, gray, gray))
    out = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    out.paste(rgb, mask=alpha)
    out.putalpha(alpha)
    return out, "rgba grayscale"


def lanczos_thumbnail(img: Image.Image, max_px: int) -> Image.Image:
    work = img.convert("RGBA").copy()
    max_px = max(1, int(max_px))
    work.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    return work


def enforce_pixel_cap(img: Image.Image, max_px: int) -> Image.Image:
    """Belt-and-suspenders cap used at load. LANCZOS only."""
    capped = lanczos_thumbnail(img, min(max_px, ABSOLUTE_MAX_PX))
    if max(capped.size) > ABSOLUTE_MAX_PX:
        capped.thumbnail((ABSOLUTE_MAX_PX, ABSOLUTE_MAX_PX), Image.Resampling.LANCZOS)
    if max(capped.size) > max_px:
        capped.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    return capped


def _contrast_lut() -> list[int]:
    """Steep S-curve: darks toward #000, lights toward #FFF, edge ramps stay anti-aliased."""
    lut: list[int] = []
    for i in range(256):
        t = i / 255.0
        t = (t - 0.5) * 1.9 + 0.5
        t = min(1.0, max(0.0, t))
        # Soft crush of the extremes without a hard 1-bit snap
        if t < 0.06:
            t = 0.0
        elif t > 0.94:
            t = 1.0
        lut.append(int(round(t * 255)))
    return lut


CONTRAST_LUT = _contrast_lut()


def boost_contrast_keep_aa(img: Image.Image) -> Image.Image:
    """High-contrast grayscale with original anti-aliased alpha. No 1-bit interior snap."""
    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")
    gray = ImageOps.grayscale(rgba.convert("RGB"))
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = gray.point(CONTRAST_LUT)
    rgb = Image.merge("RGB", (gray, gray, gray))
    out = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    out.paste(rgb, mask=alpha)
    out.putalpha(alpha)
    return out


def apply_dark_stroke(img: Image.Image, px: int = STROKE_PX) -> Image.Image:
    """2px dark outer stroke so light/white marks read on #808080."""
    rgba = img.convert("RGBA")
    pad = max(0, int(px))
    if pad <= 0:
        return rgba
    canvas = Image.new("RGBA", (rgba.width + pad * 2, rgba.height + pad * 2), (0, 0, 0, 0))
    canvas.paste(rgba, (pad, pad), rgba)
    dilated = canvas.getchannel("A").filter(ImageFilter.MaxFilter(pad * 2 + 1))
    stroke = Image.new("RGBA", canvas.size, STROKE_COLOR + (255,))
    stroke.putalpha(dilated)
    out = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    out.paste(stroke, (0, 0), stroke)
    out.paste(canvas, (0, 0), canvas)
    return out


def flood_background_mask(
    img: Image.Image,
    is_background,
) -> bytearray:
    width, height = img.size
    pixels = img.load()
    mask = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    def idx(x: int, y: int) -> int:
        return y * width + x

    def try_enqueue(x: int, y: int) -> None:
        if x < 0 or y < 0 or x >= width or y >= height:
            return
        i = idx(x, y)
        if mask[i]:
            return
        if not is_background(pixels[x, y]):
            return
        mask[i] = 1
        queue.append((x, y))

    for x in range(width):
        try_enqueue(x, 0)
        try_enqueue(x, height - 1)
    for y in range(height):
        try_enqueue(0, y)
        try_enqueue(width - 1, y)

    while queue:
        x, y = queue.popleft()
        try_enqueue(x + 1, y)
        try_enqueue(x - 1, y)
        try_enqueue(x, y + 1)
        try_enqueue(x, y - 1)

    return mask


def punch_background(img: Image.Image, bg_rgb: tuple[int, int, int]) -> Image.Image | None:
    width, height = img.size
    work = img.copy()
    pixels = work.load()

    def is_background(pixel) -> bool:
        if pixel[3] < 16:
            return True
        return channel_dist(pixel, bg_rgb) <= BG_FLOOD_TOL

    mask = flood_background_mask(work, is_background)
    bg_pixels = sum(mask)
    if bg_pixels < (width * height) * 0.04:
        return None
    content_pixels = width * height - bg_pixels
    if content_pixels < (width * height) * 0.04:
        return None

    for y in range(height):
        row = y * width
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if mask[row + x]:
                pixels[x, y] = (0, 0, 0, 0)
                continue
            dist = channel_dist((r, g, b), bg_rgb)
            if dist < FRINGE_DESPILL and a:
                fade = dist / FRINGE_DESPILL
                pixels[x, y] = (r, g, b, max(0, int(a * fade)))

    cropped = crop_to_alpha(work)
    if cropped.width < 8 or cropped.height < 8:
        return None
    return cropped


def image_corners(img: Image.Image) -> list[tuple[int, int, int]]:
    width, height = img.size
    px = img.load()
    inset_x = min(2, max(0, width - 1))
    inset_y = min(2, max(0, height - 1))
    corners: list[tuple[int, int, int]] = []
    for x, y in (
        (inset_x, inset_y),
        (width - 1 - inset_x, inset_y),
        (inset_x, height - 1 - inset_y),
        (width - 1 - inset_x, height - 1 - inset_y),
    ):
        x0 = min(max(x, 0), width - 1)
        y0 = min(max(y, 0), height - 1)
        totals = [0, 0, 0]
        count = 0
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                sx = min(max(x0 + dx, 0), width - 1)
                sy = min(max(y0 + dy, 0), height - 1)
                pixel = px[sx, sy]
                if pixel[3] < 16:
                    continue
                totals[0] += pixel[0]
                totals[1] += pixel[1]
                totals[2] += pixel[2]
                count += 1
        if count:
            corners.append((totals[0] // count, totals[1] // count, totals[2] // count))
    return corners


def classify_background(img: Image.Image) -> tuple[str, tuple[int, int, int]] | None:
    corners = image_corners(img)
    if len(corners) < 3:
        return None
    whiteish = [c for c in corners if luma(c) >= WHITE_LUMA_MIN]
    tanish = [c for c in corners if is_compression_box_color(c) and luma(c) >= TAN_LUMA_MIN]
    blackish = [c for c in corners if luma(c) <= BLACK_LUMA_MAX and max(c) <= 70]
    kind: str | None = None
    bg_rgb: tuple[int, int, int] | None = None
    if len(whiteish) >= 3:
        kind, bg_rgb = "white", whiteish[0]
    elif len(tanish) >= 3:
        kind, bg_rgb = "tan", tanish[0]
    elif len(blackish) >= 3:
        kind, bg_rgb = "black", blackish[0]
    else:
        matches = 0
        for i in range(len(corners)):
            for j in range(i + 1, len(corners)):
                if similar_color(corners[i], corners[j], CORNER_MATCH_TOL):
                    matches += 1
        if matches >= 4 and is_compression_box_color(corners[0]):
            kind, bg_rgb = "uniform", corners[0]
        elif matches >= 5:
            kind, bg_rgb = "uniform", corners[0]
    if kind is None or bg_rgb is None:
        return None

    match_frac, luma_std = border_stats(img, bg_rgb)
    if match_frac < BORDER_MATCH_MIN:
        return None
    if kind == "uniform" and luma_std > BORDER_STD_MAX:
        return None
    if kind in {"white", "black", "tan"} and luma_std > 36:
        return None
    return kind, bg_rgb


def median_rgb(samples: list[tuple[int, ...]]) -> tuple[int, int, int]:
    rs = sorted(p[0] for p in samples)
    gs = sorted(p[1] for p in samples)
    bs = sorted(p[2] for p in samples)
    mid = len(samples) // 2
    return rs[mid], gs[mid], bs[mid]


def strip_border_box(img: Image.Image) -> Image.Image | None:
    """Punch a JPEG/PNG bounding box when most of the border is pad white/tan/black."""
    samples = list(iter_border_pixels(img, step=max(1, max(img.size) // 90)))
    if len(samples) < 8:
        return None
    pad_samples = [p for p in samples if is_compression_box_color(p)]
    if len(pad_samples) / len(samples) < 0.58:
        return None
    return punch_background(img, median_rgb(pad_samples))


def is_black_card_with_mark(img: Image.Image) -> bool:
    pixels = img.load()
    opaque = 0
    black = 0
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = pixels[x, y]
            if a < 16:
                continue
            opaque += 1
            if luma((r, g, b)) <= BLACK_LUMA_MAX and max(r, g, b) <= 70:
                black += 1
    if opaque == 0:
        return False
    black_frac = black / opaque
    return black_frac >= 0.45 and (1.0 - black_frac) >= 0.08


def normalize_logo(
    img: Image.Image,
    gray_rgb: tuple[int, int, int],
    path: Path | None = None,
) -> tuple[Image.Image, str]:
    override = lookup_logo_override(path) if path is not None else {}
    rgba = img.convert("RGBA")
    notes: list[str] = []
    first_kind: str | None = None
    if has_useful_alpha(rgba):
        work = crop_to_alpha(rgba)
        notes.append("alpha mask")
    else:
        work = rgba

    for pass_index in range(3):
        classified = classify_background(work)
        punched = None
        kind = ""
        if classified:
            kind, bg_rgb = classified
            if has_useful_alpha(work) and kind in {"white", "tan"}:
                mean, _std, _fill = opaque_luma_stats(work.convert("L"), work.getchannel("A"))
                if mean >= 150:
                    classified = None
                    kind = ""
            if classified and pass_index >= 1 and first_kind == "white" and kind == "black" and not is_black_card_with_mark(work):
                break
            if classified:
                punched = punch_background(work, bg_rgb)
        if punched is None:
            punched = strip_border_box(work)
            kind = kind or "box"
        if punched is None:
            if pass_index == 0 and not classified:
                break
            if classified:
                notes.append(f"{kind} skipped")
            break
        work = crop_to_alpha(punched)
        notes.append(f"{kind} flood-fill")
        if first_kind is None:
            first_kind = kind

    if not any(
        note.endswith("flood-fill") or note == "alpha mask" or note.endswith("skipped")
        for note in notes
    ):
        notes.append("full-bleed")
    work, mono_note = to_soft_grayscale(work, override)
    notes.append(mono_note)
    _ = gray_rgb
    return work, " + ".join(notes)


def _preprocess_job(path_str: str) -> tuple[str, str, int, int, bytes] | None:
    """Normalize, LANCZOS-cap, AA contrast, and 2px stroke once per source."""
    path = Path(path_str)
    gray_rgb = hex_to_rgb(BACKGROUND_COLOR)
    with Image.open(path) as raw:
        normalized, note = normalize_logo(raw, gray_rgb, path)
    if normalized.width < 1 or normalized.height < 1:
        return None
    before = normalized.size
    master = enforce_pixel_cap(normalized, ABSOLUTE_MAX_PX)
    master = boost_contrast_keep_aa(master)
    mean, _std, _fill = opaque_luma_stats(master.convert("L"), master.getchannel("A"))
    if mean >= LIGHT_MARK_LUMA:
        master = apply_dark_stroke(master, STROKE_PX)
        if max(master.size) > ABSOLUTE_MAX_PX:
            master.thumbnail((ABSOLUTE_MAX_PX, ABSOLUTE_MAX_PX), Image.Resampling.LANCZOS)
    note = (
        f"{note}; thumbnail≤{ABSOLUTE_MAX_PX}px "
        f"{before[0]}x{before[1]}→{master.width}x{master.height}"
    )
    buf = BytesIO()
    master.save(buf, format="PNG")
    return path_str, note, master.width, master.height, buf.getvalue()


def _map_parallel(fn, jobs):
    """Multi-core map. Threads for image work (ProcessPool spawn can stall on import)."""
    if not jobs:
        return []
    try:
        with ProcessPoolExecutor(max_workers=RENDER_WORKERS) as executor:
            return list(executor.map(fn, jobs, chunksize=1))
    except Exception as exc:
        print(f"  ProcessPool unavailable ({exc}); falling back to threads")
        with ThreadPoolExecutor(max_workers=RENDER_WORKERS) as executor:
            return list(executor.map(fn, jobs, chunksize=1))


def _add_transparent_hpad(img: Image.Image, side: str, px: int) -> Image.Image:
    """Widen the RGBA buffer with empty pixels on one side. No rescaling."""
    pad = max(0, int(px))
    if pad <= 0:
        return img
    work = img.convert("RGBA")
    out = Image.new("RGBA", (work.width + pad, work.height), (0, 0, 0, 0))
    out.paste(work, (0, 0) if side == "right" else (pad, 0), work)
    return out


def preprocess_assets(
    paths: list[Path],
) -> tuple[dict[str, Image.Image], dict[tuple[str, int], Image.Image]]:
    print("\nPhase 1: preprocess sources (LANCZOS + contrast + stroke)...")
    jobs = [str(path) for path in paths]
    with ThreadPoolExecutor(max_workers=RENDER_WORKERS) as executor:
        results = list(executor.map(_preprocess_job, jobs, chunksize=1))
    logos_dict: dict[str, Image.Image] = {}
    for path, result in zip(paths, results):
        if result is None:
            print(f"  Skipping empty image: {path.name}")
            continue
        _path_str, note, width, height, blob = result
        if max(width, height) > ABSOLUTE_MAX_PX:
            raise RuntimeError(
                f"{path.name} loaded at {width}x{height}, over the {ABSOLUTE_MAX_PX}px cap"
            )
        logos_dict[path.stem] = Image.open(BytesIO(blob)).convert("RGBA")
        print(f"  {path.name}: {note}")
    print(f"  Cached {len(logos_dict)} source bitmaps")

    rng = random.Random(LAYOUT_SEED)
    tier_buffers: dict[tuple[str, int], Image.Image] = {}
    n_pad = 0
    for lid, master in logos_dict.items():
        for cap in (TIER_MAX["medium"], TIER_MAX["small"]):
            img = _scale_logo(master, cap)
            if rng.random() < 0.40:
                img = _add_transparent_hpad(
                    img, rng.choice(("left", "right")), rng.randint(10, 30)
                )
                n_pad += 1
            tier_buffers[(lid, cap)] = img
    print(
        f"  Asymmetric pre-pad: {n_pad} of {len(logos_dict) * 2} medium/small buffers "
        f"(10–30px left or right)"
    )
    return logos_dict, tier_buffers



def _too_close_id(
    x: float,
    y: float,
    previous: list[tuple[float, float]],
    min_dist: float = MIN_DUPLICATE_DISTANCE,
) -> bool:
    if not previous:
        return False
    min_sq = min_dist * min_dist
    for px, py in previous:
        dx = x - px
        dy = y - py
        if dx * dx + dy * dy < min_sq:
            return True
    return False


def _scale_logo(src: Image.Image, max_px: int) -> Image.Image:
    return lanczos_thumbnail(src, max_px)


def _ink_mask(img: Image.Image, grout: int) -> np.ndarray:
    """Opaque ink (alpha ≥ 32) dilated by grout. Transparent corners stay False."""
    alpha = img.getchannel("A")
    if grout > 0:
        kernel = grout * 2 + 1
        if kernel % 2 == 0:
            kernel += 1
        alpha = alpha.filter(ImageFilter.MaxFilter(kernel))
    return np.asarray(alpha) >= ALPHA_INK_MIN


QUAD_LABELS = ("TL", "TR", "BL", "BR")


def quadrant_index(
    x: float,
    y: float,
    canvas_w: int = CANVAS_WIDTH,
    canvas_h: int = CANVAS_HEIGHT,
) -> int:
    return (0 if y < canvas_h * 0.5 else 2) + (0 if x < canvas_w * 0.5 else 1)


def quadrant_bbox_fracs(
    placed_items: list[dict],
    canvas_w: int = CANVAS_WIDTH,
    canvas_h: int = CANVAS_HEIGHT,
) -> tuple[float, float, float, float]:
    areas = [0.0, 0.0, 0.0, 0.0]
    qarea = canvas_w * canvas_h / 4.0
    if qarea <= 0:
        return (0.0, 0.0, 0.0, 0.0)
    for item in placed_items:
        x, y = item["pos"]
        w, h = item["img"].size
        qi = quadrant_index(x + w / 2, y + h / 2, canvas_w, canvas_h)
        areas[qi] += w * h
    return tuple(a / qarea for a in areas)


def quadrant_tier_counts(placed_items: list[dict]) -> list[tuple[int, int, int]]:
    quads = [(0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0)]
    for item in placed_items:
        x, y = item["pos"]
        iw, ih = item["img"].size
        qi = quadrant_index(x + iw / 2, y + ih / 2)
        h, m, s = quads[qi]
        t = item.get("tier", "small")
        if t == "hero":
            quads[qi] = (h + 1, m, s)
        elif t == "medium":
            quads[qi] = (h, m + 1, s)
        else:
            quads[qi] = (h, m, s + 1)
    return quads


def generate_mosaic_layout(
    logos_dict: dict[str, Image.Image],
    canvas_w: int = CANVAS_WIDTH,
    canvas_h: int = CANVAS_HEIGHT,
    rng: random.Random | None = None,
    tier_buffers: dict[tuple[str, int], Image.Image] | None = None,
) -> list[dict]:
    rng = rng or random.Random(LAYOUT_SEED)
    logo_keys = list(logos_dict.keys())
    if not logo_keys:
        return []
    rng.shuffle(logo_keys)

    scaled: dict[tuple[str, int], Image.Image] = dict(tier_buffers or {})
    ink_cache: dict[tuple[int, int], np.ndarray] = {}

    def get_scaled(lid: str, cap: int) -> Image.Image:
        key = (lid, cap)
        img = scaled.get(key)
        if img is None:
            img = _scale_logo(logos_dict[lid], cap)
            scaled[key] = img
        return img

    def get_ink(img: Image.Image, grout: int) -> np.ndarray:
        key = (id(img), grout)
        mask = ink_cache.get(key)
        if mask is None:
            mask = _ink_mask(img, grout)
            ink_cache[key] = mask
        return mask

    max_items = 20000
    boxes = np.zeros((max_items, 4), dtype=np.int32)
    inks: list[np.ndarray] = []
    n_box = 0
    covered = 0
    canvas_area = float(canvas_w * canvas_h)
    placed_items: list[dict] = []
    placed_positions: dict[str, list[tuple[float, float]]] = {k: [] for k in logo_keys}
    mid_x = canvas_w * 0.5
    mid_y = canvas_h * 0.5

    def occupancy_frac() -> float:
        return covered / canvas_area if canvas_area else 0.0

    def qfracs() -> tuple[float, float, float, float]:
        return quadrant_bbox_fracs(placed_items, canvas_w, canvas_h)

    def needs_fill() -> bool:
        if occupancy_frac() < TARGET_OCCUPANCY:
            return True
        return min(qfracs()) < TARGET_OCCUPANCY

    def overlap_indices(x: int, y: int, w: int, h: int, pad: int) -> np.ndarray:
        if n_box == 0:
            return np.empty(0, dtype=np.intp)
        b = boxes[:n_box]
        x1 = x - pad
        y1 = y - pad
        x2 = x + w + pad
        y2 = y + h + pad
        hit = (x1 < b[:, 2]) & (x2 > b[:, 0]) & (y1 < b[:, 3]) & (y2 > b[:, 1])
        return np.flatnonzero(hit)

    def alpha_collides(x: int, y: int, mask: np.ndarray, idxs: np.ndarray) -> bool:
        mh, mw = mask.shape
        for i in idxs:
            ox1, oy1, ox2, oy2 = (int(v) for v in boxes[i])
            ix1 = max(x, ox1)
            iy1 = max(y, oy1)
            ix2 = min(x + mw, ox2)
            iy2 = min(y + mh, oy2)
            if ix2 <= ix1 or iy2 <= iy1:
                continue
            a = mask[iy1 - y : iy2 - y, ix1 - x : ix2 - x]
            b = inks[int(i)][iy1 - oy1 : iy2 - oy1, ix1 - ox1 : ix2 - ox1]
            if a.size and b.size and (a & b).any():
                return True
        return False

    def sample_xy(w: int, h: int, quad: int | None) -> tuple[int, int]:
        max_x = canvas_w - w
        max_y = canvas_h - h
        if max_x < 0 or max_y < 0:
            return 0, 0
        if quad is None:
            return rng.randint(0, max_x), rng.randint(0, max_y)
        if quad % 2 == 0:
            x0, x1 = 0, min(max_x, int(mid_x) - 1)
        else:
            x0, x1 = max(0, int(mid_x) - w + 1), max_x
        if quad < 2:
            y0, y1 = 0, min(max_y, int(mid_y) - 1)
        else:
            y0, y1 = max(0, int(mid_y) - h + 1), max_y
        if x1 < x0:
            x0, x1 = 0, max_x
        if y1 < y0:
            y0, y1 = 0, max_y
        return rng.randint(x0, x1), rng.randint(y0, y1)

    def find_spot(
        img: Image.Image,
        lid: str,
        grout: int,
        n_random: int = PLACE_TRIES,
        quad: int | None = None,
    ) -> tuple[int, int] | None:
        w, h = img.size
        if w > canvas_w or h > canvas_h:
            return None
        mask = get_ink(img, grout)
        if not mask.any():
            return None
        prev = placed_positions[lid]
        tries = min(max(n_random, PLACE_TRIES), PLACE_TRIES_MAX)
        for _ in range(tries):
            x, y = sample_xy(w, h, quad)
            cx, cy = x + w * 0.5, y + h * 0.5
            if quad is not None:
                qi = (0 if cy < mid_y else 2) + (0 if cx < mid_x else 1)
                if qi != quad:
                    continue
            if _too_close_id(cx, cy, prev):
                continue
            hits = overlap_indices(x, y, w, h, grout)
            if hits.size and alpha_collides(x, y, mask, hits):
                continue
            return x, y
        return None

    def commit(lid: str, img: Image.Image, tier: str, grout: int, pos: tuple[int, int]) -> None:
        nonlocal n_box, covered
        x, y = pos
        w, h = img.size
        mask = get_ink(img, grout)
        if n_box >= boxes.shape[0]:
            return
        boxes[n_box] = (x, y, x + w, y + h)
        inks.append(mask)
        n_box += 1
        covered += w * h
        placed_items.append({"id": lid, "img": img, "pos": (x, y), "tier": tier})
        placed_positions[lid].append((x + w * 0.5, y + h * 0.5))

    def try_place(
        lid: str,
        img: Image.Image,
        tier: str,
        grout: int,
        quad: int | None = None,
        tries: int = PLACE_TRIES,
    ) -> bool:
        pos = find_spot(img, lid, grout, n_random=tries, quad=quad)
        if pos is None:
            return False
        commit(lid, img, tier, grout, pos)
        return True

    n_hero = max(1, int(round(PRIMARY_QUEUE * HERO_FRACTION / (1.0 - MICRO_FRACTION))))
    n_med = int(round(PRIMARY_QUEUE * MEDIUM_FRACTION / (1.0 - MICRO_FRACTION)))
    n_small = max(0, PRIMARY_QUEUE - n_hero - n_med)
    queue = (
        [(rng.choice(logo_keys), "hero") for _ in range(n_hero)]
        + [(rng.choice(logo_keys), "medium") for _ in range(n_med)]
        + [(rng.choice(logo_keys), "small") for _ in range(n_small)]
    )
    rng.shuffle(queue)

    print(
        f"\nPhase 2a: bbox + exact-alpha pack "
        f"(grout {BOX_PAD}px, {PLACE_TRIES} random samples/item)..."
    )
    t0 = time.perf_counter()
    placed_n = {"hero": 0, "medium": 0, "small": 0, "micro": 0}
    for lid, tier in queue:
        placed = False
        for _try in range(8):
            cand = lid if _try == 0 else rng.choice(logo_keys)
            img = get_scaled(cand, TIER_MAX[tier])
            if try_place(cand, img, tier, BOX_PAD):
                placed_n[tier] += 1
                placed = True
                break
        if not placed and tier != "small":
            img = get_scaled(lid, TIER_MAX["small"])
            if try_place(lid, img, "small", BOX_PAD, tries=PLACE_TRIES_MAX):
                placed_n["small"] += 1
        n_pri = placed_n["hero"] + placed_n["medium"] + placed_n["small"]
        if placed and n_pri % 250 == 0:
            print(
                f"    placed {n_pri}  occ {occupancy_frac() * 100:.1f}%",
                flush=True,
            )

    fails = 0
    while occupancy_frac() < TARGET_OCCUPANCY and fails < 200:
        lid = rng.choice(logo_keys)
        img = get_scaled(lid, TIER_MAX["small"])
        if try_place(lid, img, "small", BOX_PAD, tries=PLACE_TRIES_MAX):
            placed_n["small"] += 1
            fails = 0
        else:
            fails += 1
    print(
        f"  primaries {placed_n['hero'] + placed_n['medium'] + placed_n['small']} "
        f"({placed_n['hero']} hero / {placed_n['medium']} medium / {placed_n['small']} small) "
        f"in {time.perf_counter() - t0:.3f}s  occupancy {occupancy_frac() * 100:.1f}%"
    )

    print(
        f"Phase 2b: micro-fill "
        f"({TIER_MIN['micro']}-{TIER_MAX['micro']}px, {PLACE_TRIES_MAX} samples) until occupancy "
        f">{TARGET_OCCUPANCY * 100:.0f}%..."
    )
    t1 = time.perf_counter()
    empty_passes = 0
    micro_before = placed_n["micro"]
    while needs_fill() and empty_passes < 24:
        progressed = 0
        order = logo_keys[:]
        rng.shuffle(order)
        for lid in order:
            if not needs_fill():
                break
            cap = rng.randint(25, 75)
            img = get_scaled(lid, cap)
            if try_place(lid, img, "micro", MICRO_PAD, tries=PLACE_TRIES_MAX):
                placed_n["micro"] += 1
                progressed += 1
                if placed_n["micro"] % 250 == 0:
                    print(
                        f"    micro {placed_n['micro']}  "
                        f"occ {occupancy_frac() * 100:.1f}%  "
                        f"quads {[round(q * 100, 1) for q in qfracs()]}",
                        flush=True,
                    )
        if progressed == 0:
            empty_passes += 1
        else:
            empty_passes = 0
    print(
        f"  +{placed_n['micro'] - micro_before} micro-fillers in {time.perf_counter() - t1:.3f}s  "
        f"(occupancy {occupancy_frac() * 100:.1f}%, "
        f"quads {[round(q * 100, 1) for q in qfracs()]})"
    )

    print("Phase 2c: even micro-fill sweep (25–75px, weakest quadrant)...")
    t2 = time.perf_counter()
    empty_passes = 0
    even_before = placed_n["micro"]
    while empty_passes < 16:
        fracs = qfracs()
        spread = max(fracs) - min(fracs)
        if spread <= 0.015 and min(fracs) >= min(TARGET_OCCUPANCY, occupancy_frac()):
            break
        qi = int(np.argmin(fracs))
        progressed = 0
        order = logo_keys[:]
        rng.shuffle(order)
        for lid in order:
            cap = rng.randint(25, 75)
            img = get_scaled(lid, cap)
            if try_place(lid, img, "micro", MICRO_PAD, quad=qi, tries=PLACE_TRIES_MAX):
                placed_n["micro"] += 1
                progressed += 1
                if placed_n["micro"] % 250 == 0:
                    print(
                        f"    even {placed_n['micro']}  "
                        f"occ {occupancy_frac() * 100:.1f}%  "
                        f"quads {[round(q * 100, 1) for q in qfracs()]}",
                        flush=True,
                    )
        if progressed == 0:
            empty_passes += 1
        else:
            empty_passes = 0
    print(
        f"  +{placed_n['micro'] - even_before} even-fill in {time.perf_counter() - t2:.3f}s  "
        f"(occupancy {occupancy_frac() * 100:.1f}%, "
        f"quads {[round(q * 100, 1) for q in qfracs()]})"
    )
    return placed_items


def paste_layer(canvas: Image.Image, layer: Image.Image, x: int, y: int) -> None:
    if x >= canvas.width or y >= canvas.height:
        return
    src_x = 0
    src_y = 0
    if x < 0:
        src_x = -x
        x = 0
    if y < 0:
        src_y = -y
        y = 0
    width = min(layer.width - src_x, canvas.width - x)
    height = min(layer.height - src_y, canvas.height - y)
    if width <= 0 or height <= 0:
        return
    if src_x or src_y or width != layer.width or height != layer.height:
        layer = layer.crop((src_x, src_y, src_x + width, src_y + height))
    canvas.paste(layer, (x, y), mask=layer)


def _composite_band(
    job: tuple[int, int, list[tuple[int, int, bytes]], tuple[int, int, int]],
) -> tuple[int, bytes]:
    """Process-pool worker: alpha-composite one horizontal band of the canvas."""
    y0, y1, placements, gray = job
    band = Image.new("RGBA", (CANVAS_WIDTH, y1 - y0), gray + (255,))
    for x, y, blob in placements:
        layer = Image.open(BytesIO(blob)).convert("RGBA")
        paste_layer(band, layer, x, y - y0)
    buf = BytesIO()
    band.save(buf, format="PNG", compress_level=1)
    return y0, buf.getvalue()


def render_mosaic(placed_items: list[dict]) -> Image.Image:
    gray = hex_to_rgb(BACKGROUND_COLOR)
    total = len(placed_items)
    print(
        f"\nPhase 3: process-pool composite ({total} tiles, {RENDER_WORKERS} cores, "
        f"{CANVAS_WIDTH}x{CANVAS_HEIGHT} @ {OUTPUT_DPI} DPI)..."
    )
    payloads: list[tuple[int, int, bytes, int]] = []
    for item in placed_items:
        buf = BytesIO()
        item["img"].save(buf, format="PNG", compress_level=1)
        x, y = item["pos"]
        payloads.append((x, y, buf.getvalue(), item["img"].height))

    n_bands = max(1, RENDER_WORKERS)
    band_h = (CANVAS_HEIGHT + n_bands - 1) // n_bands
    jobs = []
    for i in range(n_bands):
        y0 = i * band_h
        y1 = min(CANVAS_HEIGHT, (i + 1) * band_h)
        items = [
            (x, y, blob)
            for x, y, blob, h in payloads
            if not (y + h <= y0 or y >= y1)
        ]
        jobs.append((y0, y1, items, gray))

    canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), gray + (255,))
    bands = _map_parallel(_composite_band, jobs)
    for y0, blob in bands:
        band = Image.open(BytesIO(blob)).convert("RGBA")
        canvas.paste(band, (0, y0))
    print(f"  Composited {total} logos across {len(jobs)} bands")
    return canvas


def main() -> int:
    wall0 = time.perf_counter()
    print("=" * 72)
    print("Logo mosaic collage (Spoonflower 54x36 @ 150 DPI)")
    print(f"Canvas: {CANVAS_WIDTH}x{CANVAS_HEIGHT} px  |  54x36 in @ {OUTPUT_DPI} DPI")
    print(f"Background: {BACKGROUND_COLOR}  |  layout: bbox + exact-alpha (2px grout)")
    print(
        "Tiers (LANCZOS max side): "
        f"hero {TIER_MAX['hero']}px / medium {TIER_MAX['medium']}px / "
        f"small {TIER_MAX['small']}px / micro {TIER_MIN['micro']}-{TIER_MAX['micro']}px"
    )
    print(
        f"Duplicate ≥ {MIN_DUPLICATE_DISTANCE}px  |  "
        f"ProcessPool x{RENDER_WORKERS}  |  seed={LAYOUT_SEED}"
    )
    print("=" * 72)

    paths = discover_logo_paths()
    print(f"\nFound {len(paths)} logo files in {LOGOS_DIR}")
    if not paths:
        print(f"No logo images found. Place PNG/JPG files in {LOGOS_DIR}")
        return 1

    t_norm = time.perf_counter()
    logos_dict, tier_buffers = preprocess_assets(paths)
    print(f"[time] Phase 1 preprocess: {time.perf_counter() - t_norm:.3f}s  ({len(logos_dict)} sources)")
    if not logos_dict:
        print("No usable logos after normalization.")
        return 1
    loaded_max = max(max(img.size) for img in logos_dict.values())
    print(f"  Loaded source max side: {loaded_max}px (cap {ABSOLUTE_MAX_PX}px)")
    if loaded_max > ABSOLUTE_MAX_PX:
        print(f"ERROR: a source exceeded the {ABSOLUTE_MAX_PX}px load cap")
        return 1

    rng = random.Random(LAYOUT_SEED)
    t_place = time.perf_counter()
    placed_items = generate_mosaic_layout(
        logos_dict, CANVAS_WIDTH, CANVAS_HEIGHT, rng, tier_buffers
    )
    print(f"[time] Phase 2 layout total: {time.perf_counter() - t_place:.3f}s  ({len(placed_items)} placed)")

    long_sides = [max(item["img"].size) for item in placed_items]
    xs = [item["pos"][0] + item["img"].width / 2 for item in placed_items]
    ys = [item["pos"][1] + item["img"].height / 2 for item in placed_items]
    weights = [item["img"].width * item["img"].height for item in placed_items]
    used = sum(weights)
    coverage = used / float(CANVAS_WIDTH * CANVAS_HEIGHT)
    wsum = float(used) if used else 1.0
    com_x = sum(x * w for x, w in zip(xs, weights)) / wsum
    com_y = sum(y * w for y, w in zip(ys, weights)) / wsum
    tiers = Counter(item.get("tier", "?") for item in placed_items)
    print("\n" + "-" * 72)
    print(f"Original logos: {len(logos_dict)}")
    print(f"Placed tiles: {len(placed_items)}")
    print(
        f"Tiers: {tiers.get('hero', 0)} hero / {tiers.get('medium', 0)} medium / "
        f"{tiers.get('small', 0)} small / {tiers.get('micro', 0)} micro"
    )
    print(f"Pixel coverage: {coverage * 100:.1f}% of canvas")
    print(f"X center of mass: {com_x:.0f}  |  Y center of mass: {com_y:.0f}")
    print(
        "Quadrant mix (H/M/S): "
        + "  ".join(
            f"{lab} {h}/{m}/{s}"
            for lab, (h, m, s) in zip(QUAD_LABELS, quadrant_tier_counts(placed_items))
        )
    )
    print(
        f"Logo longest side px: min {min(long_sides)} / median {int(np.median(long_sides))} / "
        f"max {max(long_sides)} ({max(long_sides) / OUTPUT_DPI:.2f} in, "
        f"hard cap {ABSOLUTE_MAX_PX}px / {ABSOLUTE_MAX_PX / OUTPUT_DPI:.2f}\")"
    )
    print("-" * 72)
    if min(long_sides) < MIN_LOGO_PX:
        print(f"ERROR: placed logo {min(long_sides)}px is below the {MIN_LOGO_PX}px floor")
        return 1
    if max(long_sides) > ABSOLUTE_MAX_PX:
        print(
            f"ERROR: placed logo {max(long_sides)}px exceeds {ABSOLUTE_MAX_PX}px cap; "
            "refusing to render"
        )
        return 1

    t_render = time.perf_counter()
    canvas = render_mosaic(placed_items)
    print(f"[time] parallel composite: {time.perf_counter() - t_render:.3f}s")

    output_path = Path(OUTPUT_FILE)
    if not output_path.is_absolute():
        output_path = PROJECT_DIR / output_path
    t_save = time.perf_counter()
    canvas.save(output_path, "PNG", dpi=(150, 150), compress_level=2)
    print(f"[time] save PNG: {time.perf_counter() - t_save:.3f}s")
    print(f"\nSaved {output_path} ({CANVAS_WIDTH}x{CANVAS_HEIGHT} @ {OUTPUT_DPI} DPI)")
    print(f"[time] TOTAL: {time.perf_counter() - wall0:.3f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
