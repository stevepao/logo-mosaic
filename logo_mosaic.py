#!/usr/bin/env python3
"""
Spoonflower logo mosaic: 54" x 36" @ 150 DPI, mid-gray RGBA alpha paste.

Phase 1: luma-to-alpha (or keep existing alpha), LANCZOS-cap, contrast LUT, optional 2px stroke.
Phase 2: random place with NumPy bbox cull then exact alpha AND; hero/med/small queue, then micro-fill.
Phase 3: ProcessPool horizontal-band composite onto #808080.

Copyright (c) 2026 Hillwork LLC
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

# ---------------------------------------------------------------------------
# Configuration & Parameters
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
MICRO_FRACTION = 0.20  # small count is PRIMARY_QUEUE minus hero/medium

ABSOLUTE_MAX_PX = 156  # Hero cap (~1.04" @ 150 DPI)
TIER_MAX = {
    "hero": 156,
    "medium": 106,
    "small": 71,
}

STROKE_PX = 2
STROKE_COLOR = (0, 0, 0)
LIGHT_MARK_LUMA = 140  # Light/white marks get a 2px dark outer stroke

MIN_DUPLICATE_DISTANCE = 350
BOX_PAD = 1              # 1px grout for dense sticker-bomb packing
MICRO_PAD = 1
TARGET_OCCUPANCY = 0.90
LAYOUT_SEED = 2026
PRIMARY_QUEUE = 9000
PLACE_TRIES = 250
PLACE_TRIES_MAX = 300
ALPHA_INK_MIN = 32       # exact-mask collision ignores near-transparent fringe

CPU_COUNT = os.cpu_count() or 8
RENDER_WORKERS = max(1, CPU_COUNT)
OUTPUT_DPI = 150


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def discover_logo_paths() -> list[Path]:
    output_name = Path(OUTPUT_FILE).name.lower()
    paths: list[Path] = []
    for path in sorted(LOGOS_DIR.iterdir()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if path.name.lower() == output_name or path.name.lower().startswith(("logo_mosaic", "spoonflower_")):
            continue
        if path.name.startswith("_debug"):
            continue
        paths.append(path)
    return paths


def has_useful_alpha(img: Image.Image) -> bool:
    if img.mode in ("RGBA", "LA"):
        return img.getchannel("A").getextrema()[0] < 250
    if img.mode == "P" and "transparency" in img.info:
        return img.convert("RGBA").getchannel("A").getextrema()[0] < 250
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


def opaque_luma_stats(gray: Image.Image, alpha: Image.Image) -> tuple[float, float, float]:
    g = np.asarray(gray, dtype=np.float64)
    a = np.asarray(alpha, dtype=np.uint8)
    mask = a >= 16
    count = int(mask.sum())
    if count == 0:
        return 128.0, 0.0, 0.0
    vals = g[mask]
    return float(vals.mean()), float(vals.var()) ** 0.5, count / float(g.size)


def to_soft_grayscale(img: Image.Image) -> tuple[Image.Image, str]:
    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")
    gray = ImageOps.grayscale(rgba.convert("RGB"))
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
    capped = lanczos_thumbnail(img, min(max_px, ABSOLUTE_MAX_PX))
    if max(capped.size) > ABSOLUTE_MAX_PX:
        capped.thumbnail((ABSOLUTE_MAX_PX, ABSOLUTE_MAX_PX), Image.Resampling.LANCZOS)
    if max(capped.size) > max_px:
        capped.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    return capped


def _contrast_lut() -> list[int]:
    lut: list[int] = []
    for i in range(256):
        t = (i / 255.0 - 0.5) * 1.9 + 0.5
        t = min(1.0, max(0.0, t))
        if t < 0.06:
            t = 0.0
        elif t > 0.94:
            t = 1.0
        lut.append(int(round(t * 255)))
    return lut


CONTRAST_LUT = _contrast_lut()


def boost_contrast_keep_aa(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    red, green, blue, alpha = rgba.split()
    alpha = ImageOps.autocontrast(alpha, cutoff=1).point(CONTRAST_LUT)
    rgb = Image.merge("RGB", (red, green, blue))
    gray = ImageOps.grayscale(rgb)
    g = np.asarray(gray, dtype=np.float64)
    am = np.asarray(alpha, dtype=np.uint8) >= 16
    if (float(g[am].std()) if am.any() else 0.0) > 8:
        gray = ImageOps.autocontrast(gray, cutoff=1).point(CONTRAST_LUT)
        rgb = Image.merge("RGB", (gray, gray, gray))
    return Image.merge("RGBA", (*rgb.split(), alpha))


def apply_dark_stroke(img: Image.Image, px: int = STROKE_PX) -> Image.Image:
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


def luma_mask_opaque(img: Image.Image) -> tuple[Image.Image, str]:
    gray = ImageOps.grayscale(img.convert("RGB"))
    mean = float(np.asarray(gray, dtype=np.float64).mean())
    if mean >= 128.0:
        alpha = ImageOps.invert(gray)
        color = (0, 0, 0)
        note = f"luma-mask dark-on-light (mean {mean:.0f})"
    else:
        alpha = gray
        color = (255, 255, 255)
        note = f"luma-mask light-on-dark (mean {mean:.0f})"
    rgb = Image.new("RGB", gray.size, color)
    out = Image.merge("RGBA", (*rgb.split(), alpha))
    return crop_to_alpha(out, pad=0), note


def normalize_logo(img: Image.Image) -> tuple[Image.Image, str]:
    rgba = img.convert("RGBA")
    if has_useful_alpha(rgba):
        work = crop_to_alpha(rgba)
        work, mono_note = to_soft_grayscale(work)
        return work, f"alpha mask + {mono_note}"
    return luma_mask_opaque(rgba)


def _preprocess_job(path_str: str) -> tuple[str, str, int, int, bytes] | None:
    path = Path(path_str)
    with Image.open(path) as raw:
        normalized, note = normalize_logo(raw)
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
    note = f"{note}; thumbnail≤{ABSOLUTE_MAX_PX}px {before[0]}x{before[1]}→{master.width}x{master.height}"
    buf = BytesIO()
    master.save(buf, format="PNG")
    return path_str, note, master.width, master.height, buf.getvalue()


def _map_parallel(fn, jobs):
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
    pad = max(0, int(px))
    if pad <= 0:
        return img
    work = img.convert("RGBA")
    out = Image.new("RGBA", (work.width + pad, work.height), (0, 0, 0, 0))
    out.paste(work, (0, 0) if side == "right" else (pad, 0), work)
    return out


def preprocess_assets(paths: list[Path]) -> tuple[dict[str, Image.Image], dict[tuple[str, int], Image.Image]]:
    print("\nPhase 1: preprocess sources (LANCZOS + contrast + stroke)...")
    jobs = [str(path) for path in paths]
    with ThreadPoolExecutor(max_workers=RENDER_WORKERS) as executor:
        results = list(executor.map(_preprocess_job, jobs, chunksize=1))
    logos_dict: dict[str, Image.Image] = {}
    for path, result in zip(paths, results):
        if result is None:
            continue
        _path_str, note, width, height, blob = result
        logos_dict[path.stem] = Image.open(BytesIO(blob)).convert("RGBA")
        print(f"  {path.name}: {note}")

    rng = random.Random(LAYOUT_SEED)
    tier_buffers: dict[tuple[str, int], Image.Image] = {}
    for lid, master in logos_dict.items():
        for cap in (TIER_MAX["medium"], TIER_MAX["small"]):
            img = lanczos_thumbnail(master, cap)
            if rng.random() < 0.40:
                img = _add_transparent_hpad(img, rng.choice(("left", "right")), rng.randint(10, 30))
            tier_buffers[(lid, cap)] = img
    return logos_dict, tier_buffers


def _too_close_id(x: float, y: float, previous: list[tuple[float, float]], min_dist: float = MIN_DUPLICATE_DISTANCE) -> bool:
    if not previous:
        return False
    min_sq = min_dist * min_dist
    for px, py in previous:
        if (x - px) ** 2 + (y - py) ** 2 < min_sq:
            return True
    return False


def _ink_mask(img: Image.Image, grout: int) -> np.ndarray:
    alpha = img.getchannel("A")
    if grout > 0:
        kernel = grout * 2 + 1
        alpha = alpha.filter(ImageFilter.MaxFilter(kernel if kernel % 2 != 0 else kernel + 1))
    return np.asarray(alpha) >= ALPHA_INK_MIN


def quadrant_index(x: float, y: float, canvas_w: int = CANVAS_WIDTH, canvas_h: int = CANVAS_HEIGHT) -> int:
    return (0 if y < canvas_h * 0.5 else 2) + (0 if x < canvas_w * 0.5 else 1)


def quadrant_bbox_fracs(placed_items: list[dict], canvas_w: int = CANVAS_WIDTH, canvas_h: int = CANVAS_HEIGHT) -> tuple[float, float, float, float]:
    areas = [0.0, 0.0, 0.0, 0.0]
    qarea = canvas_w * canvas_h / 4.0
    for item in placed_items:
        x, y = item["pos"]
        w, h = item["img"].size
        qi = quadrant_index(x + w / 2, y + h / 2, canvas_w, canvas_h)
        areas[qi] += w * h
    return tuple(a / qarea for a in areas)


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
            img = lanczos_thumbnail(logos_dict[lid], cap)
            scaled[key] = img
        return img

    def get_ink(img: Image.Image, grout: int) -> np.ndarray:
        key = (id(img), grout)
        mask = ink_cache.get(key)
        if mask is None:
            mask = _ink_mask(img, grout)
            ink_cache[key] = mask
        return mask

    boxes = np.zeros((80000, 4), dtype=np.int32)
    inks: list[np.ndarray] = []
    n_box = 0
    covered = 0
    canvas_area = float(canvas_w * canvas_h)
    placed_items: list[dict] = []
    placed_positions: dict[str, list[tuple[float, float]]] = {k: [] for k in logo_keys}
    mid_x, mid_y = canvas_w * 0.5, canvas_h * 0.5

    def occupancy_frac() -> float:
        return covered / canvas_area if canvas_area else 0.0

    def qfracs() -> tuple[float, float, float, float]:
        return quadrant_bbox_fracs(placed_items, canvas_w, canvas_h)

    def needs_fill() -> bool:
        return occupancy_frac() < TARGET_OCCUPANCY or min(qfracs()) < TARGET_OCCUPANCY

    def overlap_indices(x: int, y: int, w: int, h: int, pad: int) -> np.ndarray:
        if n_box == 0:
            return np.empty(0, dtype=np.intp)
        b = boxes[:n_box]
        hit = ((x - pad) < b[:, 2]) & ((x + w + pad) > b[:, 0]) & ((y - pad) < b[:, 3]) & ((y + h + pad) > b[:, 1])
        return np.flatnonzero(hit)

    def alpha_collides(x: int, y: int, mask: np.ndarray, idxs: np.ndarray) -> bool:
        mh, mw = mask.shape
        for i in idxs:
            ox1, oy1, ox2, oy2 = (int(v) for v in boxes[i])
            ix1, iy1 = max(x, ox1), max(y, oy1)
            ix2, iy2 = min(x + mw, ox2), min(y + mh, oy2)
            if ix2 <= ix1 or iy2 <= iy1:
                continue
            a = mask[iy1 - y : iy2 - y, ix1 - x : ix2 - x]
            b = inks[int(i)][iy1 - oy1 : iy2 - oy1, ix1 - ox1 : ix2 - ox1]
            if a.size and b.size and (a & b).any():
                return True
        return False

    def sample_xy(w: int, h: int, quad: int | None) -> tuple[int, int]:
        max_x, max_y = canvas_w - w, canvas_h - h
        if max_x < 0 or max_y < 0:
            return 0, 0
        if quad is None:
            return rng.randint(0, max_x), rng.randint(0, max_y)
        x0, x1 = (0, min(max_x, int(mid_x) - 1)) if quad % 2 == 0 else (max(0, int(mid_x) - w + 1), max_x)
        y0, y1 = (0, min(max_y, int(mid_y) - 1)) if quad < 2 else (max(0, int(mid_y) - h + 1), max_y)
        return rng.randint(min(x0, x1), max(x0, x1)), rng.randint(min(y0, y1), max(y0, y1))

    def try_place(lid: str, img: Image.Image, tier: str, grout: int, quad: int | None = None, tries: int = PLACE_TRIES) -> bool:
        nonlocal n_box, covered
        w, h = img.size
        mask = get_ink(img, grout)
        if not mask.any():
            return False
        prev = placed_positions[lid]
        for _ in range(min(max(tries, PLACE_TRIES), PLACE_TRIES_MAX)):
            x, y = sample_xy(w, h, quad)
            cx, cy = x + w * 0.5, y + h * 0.5
            if _too_close_id(cx, cy, prev):
                continue
            hits = overlap_indices(x, y, w, h, grout)
            if hits.size and alpha_collides(x, y, mask, hits):
                continue
            boxes[n_box] = (x, y, x + w, y + h)
            inks.append(mask)
            n_box += 1
            covered += w * h
            placed_items.append({"id": lid, "img": img, "pos": (x, y), "tier": tier})
            placed_positions[lid].append((cx, cy))
            return True
        return False

    n_hero = max(1, int(round(PRIMARY_QUEUE * HERO_FRACTION / (1.0 - MICRO_FRACTION))))
    n_med = int(round(PRIMARY_QUEUE * MEDIUM_FRACTION / (1.0 - MICRO_FRACTION)))
    n_small = max(0, PRIMARY_QUEUE - n_hero - n_med)
    queue = (
        [(rng.choice(logo_keys), "hero") for _ in range(n_hero)]
        + [(rng.choice(logo_keys), "medium") for _ in range(n_med)]
        + [(rng.choice(logo_keys), "small") for _ in range(n_small)]
    )
    rng.shuffle(queue)

    print("\nPhase 2a: bbox + exact-alpha pack...")
    for lid, tier in queue:
        placed = False
        for _try in range(8):
            cand = lid if _try == 0 else rng.choice(logo_keys)
            if try_place(cand, get_scaled(cand, TIER_MAX[tier]), tier, BOX_PAD):
                placed = True
                break
        if not placed and tier != "small":
            try_place(lid, get_scaled(lid, TIER_MAX["small"]), "small", BOX_PAD, tries=PLACE_TRIES_MAX)

    print("Phase 2b: micro-fill sweep...")
    empty_passes = 0
    while needs_fill() and empty_passes < 24:
        progressed = 0
        order = logo_keys[:]
        rng.shuffle(order)
        for lid in order:
            if try_place(lid, get_scaled(lid, rng.randint(21, 50)), "micro", MICRO_PAD, tries=PLACE_TRIES_MAX):
                progressed += 1
        empty_passes = 0 if progressed else empty_passes + 1

    return placed_items


def paste_layer(canvas: Image.Image, layer: Image.Image, x: int, y: int) -> None:
    if x >= canvas.width or y >= canvas.height:
        return
    src_x = max(0, -x)
    src_y = max(0, -y)
    dest_x = max(0, x)
    dest_y = max(0, y)
    width = min(layer.width - src_x, canvas.width - dest_x)
    height = min(layer.height - src_y, canvas.height - dest_y)
    if width <= 0 or height <= 0:
        return
    if src_x or src_y or width != layer.width or height != layer.height:
        layer = layer.crop((src_x, src_y, src_x + width, src_y + height))
    canvas.paste(layer, (dest_x, dest_y), mask=layer)


def _composite_band(job: tuple[int, int, list[tuple[int, int, bytes]], tuple[int, int, int]]) -> tuple[int, bytes]:
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
    print(f"\nPhase 3: process-pool composite ({len(placed_items)} tiles)...")
    payloads = []
    for item in placed_items:
        buf = BytesIO()
        item["img"].save(buf, format="PNG", compress_level=1)
        x, y = item["pos"]
        payloads.append((x, y, buf.getvalue(), item["img"].height))

    n_bands = max(1, RENDER_WORKERS)
    band_h = (CANVAS_HEIGHT + n_bands - 1) // n_bands
    jobs = [
        (
            i * band_h,
            min(CANVAS_HEIGHT, (i + 1) * band_h),
            [(x, y, blob) for x, y, blob, h in payloads if not (y + h <= i * band_h or y >= min(CANVAS_HEIGHT, (i + 1) * band_h))],
            gray,
        )
        for i in range(n_bands)
    ]

    canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), gray + (255,))
    for y0, blob in _map_parallel(_composite_band, jobs):
        band = Image.open(BytesIO(blob)).convert("RGBA")
        canvas.paste(band, (0, y0))
    return canvas


def main() -> int:
    wall0 = time.perf_counter()
    paths = discover_logo_paths()
    if not paths:
        print(f"No logo images found in {LOGOS_DIR}")
        return 1

    logos_dict, tier_buffers = preprocess_assets(paths)
    rng = random.Random(LAYOUT_SEED)
    placed_items = generate_mosaic_layout(logos_dict, CANVAS_WIDTH, CANVAS_HEIGHT, rng, tier_buffers)
    canvas = render_mosaic(placed_items)

    output_path = PROJECT_DIR / Path(OUTPUT_FILE)
    canvas.save(output_path, "PNG", dpi=(150, 150), compress_level=2)
    print(f"\nSaved {output_path} ({CANVAS_WIDTH}x{CANVAS_HEIGHT} @ {OUTPUT_DPI} DPI) in {time.perf_counter() - wall0:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())