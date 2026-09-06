#!/usr/bin/env python3
"""
Spoonflower logo mosaic: 54" x 36" @ 150 DPI, mid-gray RGBA alpha paste, LANCZOS, soft Gaussian halo.

Copyright (c) 2026 Hillwork LLC
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import math
import random
import sys
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps
from rectpack.geometry import Rectangle as PackRect
from rectpack.maxrects import MaxRectsBssf

# ---------------------------------------------------------------------------
# Configuration & Parameters (edit these to re-theme or re-size)
# ---------------------------------------------------------------------------

CANVAS_WIDTH = 8100   # 54 inches @ 150 DPI
CANVAS_HEIGHT = 5400  # 36 inches @ 150 DPI
BACKGROUND_COLOR = "#808080"  # Mid-gray target color
TILE_CARD_COLOR = BACKGROUND_COLOR
OUTPUT_FILE = "./spoonflower_logo_mosaic_54x36.png"
GAPS_BETWEEN_TILES = 8  # Crisp spacing suitable for high-res print

DRAW_TILE_CARDS = True
CARD_CORNER_RADIUS = 0  # Cards blend into the canvas grout
LOGO_INTERNAL_PADDING = 8  # Breathing room around the core mark

PROJECT_DIR = Path(__file__).resolve().parent
LOGOS_DIR = PROJECT_DIR / "logos"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff"}

# Compact ~1.5"–3" printed logos on the 54x36 Spoonflower canvas.
HERO_FRACTION = 0.10
MEDIUM_FRACTION = 0.35
HERO_TARGET_AREA = 140_000
MEDIUM_TARGET_AREA = 55_000
SMALL_TARGET_AREA = 20_000
MICRO_TARGET_AREA = 6_000

MIN_POOL_COVERAGE = 0.90  # Reject layouts below 90% occupied pack-rect density
POOL_AREA_MULTIPLIER = 1.15  # Duplicate until logo pixel area is 1.15x the canvas (~50.3M px)
TARGET_PACKED_COVERAGE = 0.92  # Elastic-expand if still under this after packing
MAX_EMPTY_EDGE_MARGIN = 24  # Packed bbox must reach this close to every canvas edge
MAX_HOLE_FRACTION = 0.015  # Reject layouts whose largest leftover rect is a giant hole
HOLE_FILL_MIN_SIDE = 48  # Plug leftover voids at least 48x48
HOLE_FILL_MAX_ITERS = 280
MICRO_LOGO_MIN = 40
MICRO_LOGO_MAX = 120
MICRO_PADDING = 2
ELASTIC_SCALE_MIN = 1.03
ELASTIC_SCALE_MAX = 1.06
MAX_TILES = 4000
MIN_DUPLICATE_DISTANCE = 720  # Identical logos stay apart (~4.8" at 150 DPI)
DUPLICATE_SIZE_MATCH = 0.8  # Swap partners must match pack width/height within this ratio

MID_X = CANVAS_WIDTH // 2
MID_Y = CANVAS_HEIGHT // 2
MIN_SEEDED_HEROES = 5
MAX_SEEDED_HEROES = 8
MINIMUM_HERO_DISTANCE = 1100  # Poisson-style separation between seeded hero centers
TARGET_COM_X_MIN = 3240
TARGET_COM_X_MAX = 4860
TARGET_COM_Y_MIN = 2160
TARGET_COM_Y_MAX = 3240
LIGHT_BG_LUMA = 185  # Off-white / light gray field
MEDIUM_BG_LUMA = 110  # Medium gray (e.g. #808080) sits between this and LIGHT_BG_LUMA

MAX_SHUFFLE_ATTEMPTS = 40
AREA_SHRINK_FACTOR = 0.88
MAX_SHRINK_ROUNDS = 40
MAX_EXPAND_ROUNDS = 12
EXPAND_SEED = 2026

OUTPUT_DPI = 150
MAX_LOGO_WIDTH_INCHES = 3.0
MAX_LOGO_WIDTH = int(round(MAX_LOGO_WIDTH_INCHES * OUTPUT_DPI))  # 450 px @ 150 DPI

WHITE_LUMA_MIN = 210
BLACK_LUMA_MAX = 48
TAN_LUMA_MIN = 165
PAD_CHROMA_MAX = 50
CORNER_MATCH_TOL = 28
BG_FLOOD_TOL = 40
FRINGE_DESPILL = 52
BORDER_MATCH_MIN = 0.72
BORDER_STD_MAX = 8.0

HALO_BLUR = 8.0  # GaussianBlur on alpha at print scale (no MaxFilter / dilation)
HALO_STRENGTH = 0.5  # Soft shadow/glow opacity
SHADOW_COLOR = "#2A2A2A"
GLOW_COLOR = "#F2F2F2"
MICRO_DUP_DISTANCE = 140
MICRO_HOLE_MAX_SIDE = 120  # Holes this small (or smaller) get micro-fillers
MICRO_AREA_MIN = 4_000
MICRO_AREA_MAX = 8_000

# Filename keys match flexibly (exact name, stem, or normalized aliases like snow_bunny).
LOGO_OVERRIDES: dict[str, dict] = {
    "snow_bunny.png": {"invert": False},
    "billie_eilish.png": {},
    "lelas_bistro.png": {"invert": False},
    "lela_s": {},
}


@dataclass
class LogoSource:
    path: Path
    display_name: str
    image: Image.Image
    orig_w: int
    orig_h: int
    base_id: str
    copy_id: int | None = None
    normalize_note: str = ""


@dataclass
class Tile:
    index: int
    source: LogoSource
    tier: str
    target_area: float
    scaled_w: int
    scaled_h: int
    card_w: int
    card_h: int
    pack_w: int
    pack_h: int
    is_anchor: bool = False


@dataclass
class PlacedTile:
    tile: Tile
    pack_x: int
    pack_y: int


@dataclass
class PackResult:
    placed: list[PlacedTile]
    coverage: float
    max_x: int
    max_y: int
    largest_hole_fraction: float
    seed: int
    extra_copies: int = 0
    micro_fills: int = 0
    elastic_scale: float = 1.0
    swap_count: int = 0
    swap_log: list[str] = field(default_factory=list)
    duplicate_violations: int = 0
    x_center_of_mass: float = float(MID_X)
    y_center_of_mass: float = float(MID_Y)
    min_hero_distance: float = 0.0


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def luma(rgb: tuple[int, ...]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def srgb_to_linear(channel: float) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: tuple[int, ...]) -> float:
    return (
        0.2126 * srgb_to_linear(rgb[0])
        + 0.7152 * srgb_to_linear(rgb[1])
        + 0.0722 * srgb_to_linear(rgb[2])
    )


def contrast_ratio(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    lighter = max(relative_luminance(a), relative_luminance(b))
    darker = min(relative_luminance(a), relative_luminance(b))
    return (lighter + 0.05) / (darker + 0.05)


def field_kind(bg_rgb: tuple[int, int, int] | None = None) -> str:
    rgb = bg_rgb if bg_rgb is not None else hex_to_rgb(BACKGROUND_COLOR)
    y = luma(rgb)
    if y >= LIGHT_BG_LUMA:
        return "light"
    if y >= MEDIUM_BG_LUMA:
        return "medium"
    return "dark"


def background_is_light(bg_rgb: tuple[int, int, int] | None = None) -> bool:
    return field_kind(bg_rgb) == "light"


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


def sample_corner(px, width: int, height: int, x: int, y: int) -> tuple[int, int, int] | None:
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
    if count == 0:
        return None
    return totals[0] // count, totals[1] // count, totals[2] // count


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


def composite_on_gray(img: Image.Image, gray_rgb: tuple[int, int, int]) -> Image.Image:
    card = Image.new("RGBA", img.size, gray_rgb + (255,))
    card.alpha_composite(img)
    return card


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
    gpx = gray.load()
    apx = alpha.load()
    width, height = gray.size
    total = 0.0
    total_sq = 0.0
    count = 0
    for y in range(height):
        for x in range(width):
            if apx[x, y] < 16:
                continue
            value = gpx[x, y]
            total += value
            total_sq += value * value
            count += 1
    if count == 0:
        return 128.0, 0.0, 0.0
    mean = total / count
    variance = max(0.0, total_sq / count - mean * mean)
    fill = count / float(width * height)
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


def apply_soft_halo(logo: Image.Image) -> Image.Image:
    """Soft drop shadow or glow from a blurred alpha — no MaxFilter, erosion, or binary mask."""
    rgba = logo.convert("RGBA")
    alpha = rgba.getchannel("A")
    mean, _std, _fill = opaque_luma_stats(rgba.convert("L"), alpha)
    tint = hex_to_rgb(SHADOW_COLOR if mean >= 140 else GLOW_COLOR)
    glow = alpha.filter(ImageFilter.GaussianBlur(HALO_BLUR))
    glow = glow.point(lambda a: min(255, int(a * HALO_STRENGTH)))
    pad = max(8, int(math.ceil(HALO_BLUR * 3)))
    width, height = rgba.size
    halo = Image.new("RGBA", (width + pad * 2, height + pad * 2), (0, 0, 0, 0))
    tinted = Image.new("RGBA", halo.size, tint + (255,))
    glow_pad = Image.new("L", halo.size, 0)
    glow_pad.paste(glow, (pad, pad))
    tinted.putalpha(glow_pad)
    halo.alpha_composite(tinted)
    return halo


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
    sampled = [
        sample_corner(px, width, height, inset_x, inset_y),
        sample_corner(px, width, height, width - 1 - inset_x, inset_y),
        sample_corner(px, width, height, inset_x, height - 1 - inset_y),
        sample_corner(px, width, height, width - 1 - inset_x, height - 1 - inset_y),
    ]
    return [corner for corner in sampled if corner is not None]


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


def load_logo_sources(paths: list[Path]) -> list[LogoSource]:
    gray_rgb = hex_to_rgb(BACKGROUND_COLOR)
    sources: list[LogoSource] = []
    print("\nNormalizing logo backgrounds...")
    for path in paths:
        with Image.open(path) as raw:
            normalized, note = normalize_logo(raw, gray_rgb, path)
        if normalized.width < 1 or normalized.height < 1:
            print(f"  Skipping empty image: {path.name}")
            continue
        sources.append(
            LogoSource(
                path=path,
                display_name=path.name,
                image=normalized,
                orig_w=normalized.width,
                orig_h=normalized.height,
                base_id=path.stem,
                normalize_note=note,
            )
        )
        print(
            f"  {path.name}: {note}; "
            f"{normalized.width}x{normalized.height} (aspect {normalized.width / normalized.height:.2f})"
        )
    return sources


def make_copy(source: LogoSource, copy_id: int) -> LogoSource:
    stem = source.path.stem
    suffix = source.path.suffix or ".png"
    return LogoSource(
        path=source.path,
        display_name=f"{stem}_copy_{copy_id}{suffix}",
        image=source.image,
        orig_w=source.orig_w,
        orig_h=source.orig_h,
        base_id=source.base_id,
        copy_id=copy_id,
        normalize_note=source.normalize_note,
    )


def clamp_logo_dimensions(scaled_w: int, scaled_h: int) -> tuple[int, int]:
    """Cap logo width at 3 inches, preserving aspect ratio."""
    if scaled_w > MAX_LOGO_WIDTH:
        scale = MAX_LOGO_WIDTH / float(scaled_w)
        scaled_w = MAX_LOGO_WIDTH
        scaled_h = max(1, int(round(scaled_h * scale)))
    return scaled_w, scaled_h


def dimensions_for_area(orig_w: int, orig_h: int, target_area: float) -> tuple[int, int]:
    aspect = orig_w / orig_h
    scaled_w = max(1, int(round(math.sqrt(target_area * aspect))))
    scaled_h = max(1, int(round(math.sqrt(target_area / aspect))))
    return clamp_logo_dimensions(scaled_w, scaled_h)


def build_tile(
    index: int,
    source: LogoSource,
    tier: str,
    target_area: float,
    max_pack_w: int = CANVAS_WIDTH,
    max_pack_h: int = CANVAS_HEIGHT,
) -> Tile:
    scaled_w, scaled_h = dimensions_for_area(source.orig_w, source.orig_h, target_area)
    card_w = scaled_w + (LOGO_INTERNAL_PADDING * 2)
    card_h = scaled_h + (LOGO_INTERNAL_PADDING * 2)
    pack_w = card_w + GAPS_BETWEEN_TILES
    pack_h = card_h + GAPS_BETWEEN_TILES

    if pack_w > max_pack_w or pack_h > max_pack_h:
        fit = min(max_pack_w / pack_w, max_pack_h / pack_h)
        scaled_w = max(1, int(scaled_w * fit))
        scaled_h = max(1, int(scaled_h * fit))
        card_w = scaled_w + (LOGO_INTERNAL_PADDING * 2)
        card_h = scaled_h + (LOGO_INTERNAL_PADDING * 2)
        pack_w = card_w + GAPS_BETWEEN_TILES
        pack_h = card_h + GAPS_BETWEEN_TILES

    return Tile(
        index=index,
        source=source,
        tier=tier,
        target_area=target_area,
        scaled_w=scaled_w,
        scaled_h=scaled_h,
        card_w=card_w,
        card_h=card_h,
        pack_w=pack_w,
        pack_h=pack_h,
    )


def tile_padding(tile: Tile) -> int:
    return MICRO_PADDING if tile.tier == "micro" else LOGO_INTERNAL_PADDING


def build_snug_tile(
    index: int,
    source: LogoSource,
    hole_w: int,
    hole_h: int,
    tier: str = "small",
) -> Tile:
    """Scale a logo to fill a leftover void as tightly as aspect ratio allows."""
    pad = MICRO_PADDING if tier == "micro" else LOGO_INTERNAL_PADDING
    max_card_w = max(1, hole_w - GAPS_BETWEEN_TILES)
    max_card_h = max(1, hole_h - GAPS_BETWEEN_TILES)
    max_logo_w = max(1, max_card_w - pad * 2)
    max_logo_h = max(1, max_card_h - pad * 2)
    fit = min(max_logo_w / source.orig_w, max_logo_h / source.orig_h)
    scaled_w = max(1, int(source.orig_w * fit))
    scaled_h = max(1, int(source.orig_h * fit))
    if tier == "micro":
        area = max(1, scaled_w * scaled_h)
        target = min(MICRO_AREA_MAX, max(MICRO_AREA_MIN, MICRO_TARGET_AREA))
        if area > MICRO_AREA_MAX or (area > target and min(hole_w, hole_h) <= MICRO_HOLE_MAX_SIDE):
            shrink = math.sqrt(target / float(area))
            scaled_w = max(MICRO_LOGO_MIN, int(scaled_w * shrink))
            scaled_h = max(MICRO_LOGO_MIN, int(scaled_h * shrink))
        short = min(scaled_w, scaled_h)
        if short > MICRO_LOGO_MAX and min(hole_w, hole_h) <= MICRO_HOLE_MAX_SIDE:
            shrink = MICRO_LOGO_MAX / float(short)
            scaled_w = max(MICRO_LOGO_MIN, int(scaled_w * shrink))
            scaled_h = max(MICRO_LOGO_MIN, int(scaled_h * shrink))
        short = min(scaled_w, scaled_h)
        if short < MICRO_LOGO_MIN:
            grow = MICRO_LOGO_MIN / float(max(short, 1))
            scaled_w = max(1, int(scaled_w * grow))
            scaled_h = max(1, int(scaled_h * grow))
    scaled_w, scaled_h = clamp_logo_dimensions(scaled_w, scaled_h)
    card_w = scaled_w + pad * 2
    card_h = scaled_h + pad * 2
    pack_w = card_w + GAPS_BETWEEN_TILES
    pack_h = card_h + GAPS_BETWEEN_TILES
    if pack_w > hole_w or pack_h > hole_h:
        fit = min(hole_w / pack_w, hole_h / pack_h)
        scaled_w = max(1, int(scaled_w * fit))
        scaled_h = max(1, int(scaled_h * fit))
        card_w = scaled_w + pad * 2
        card_h = scaled_h + pad * 2
        pack_w = min(hole_w, card_w + GAPS_BETWEEN_TILES)
        pack_h = min(hole_h, card_h + GAPS_BETWEEN_TILES)
    return Tile(
        index=index,
        source=source,
        tier=tier,
        target_area=float(scaled_w * scaled_h),
        scaled_w=scaled_w,
        scaled_h=scaled_h,
        card_w=card_w,
        card_h=card_h,
        pack_w=pack_w,
        pack_h=pack_h,
    )


def tier_counts(n: int) -> tuple[int, int, int]:
    n_hero = min(n, max(0, round(n * HERO_FRACTION)))
    remaining = n - n_hero
    n_medium = min(remaining, max(0, round(n * MEDIUM_FRACTION)))
    n_small = n - n_hero - n_medium
    return n_hero, n_medium, n_small


def assign_tiers(
    sources: list[LogoSource],
    seed: int,
    hero_area: float,
    medium_area: float,
    small_area: float,
) -> list[Tile]:
    n_hero, n_medium, _n_small = tier_counts(len(sources))
    order = list(enumerate(sources))
    rng = random.Random(seed)
    rng.shuffle(order)

    tiles: list[Tile] = []
    for rank, (index, source) in enumerate(order):
        if rank < n_hero:
            tier, area = "hero", hero_area
        elif rank < n_hero + n_medium:
            tier, area = "medium", medium_area
        else:
            tier, area = "small", small_area
        tiles.append(build_tile(index, source, tier, area))
    return tiles


def estimated_pack_coverage(
    sources: list[LogoSource],
    hero_area: float,
    medium_area: float,
    small_area: float,
) -> float:
    tiles = assign_tiers(sources, seed=1, hero_area=hero_area, medium_area=medium_area, small_area=small_area)
    used = sum(tile.pack_w * tile.pack_h for tile in tiles)
    return used / float(CANVAS_WIDTH * CANVAS_HEIGHT)


def pool_logo_pixel_area(
    sources: list[LogoSource],
    hero_area: float,
    medium_area: float,
    small_area: float,
) -> int:
    """Sum of scaled logo pixel areas (not including grout/padding) for the current pool."""
    tiles = assign_tiers(sources, seed=1, hero_area=hero_area, medium_area=medium_area, small_area=small_area)
    return sum(tile.scaled_w * tile.scaled_h for tile in tiles)


def largest_empty_fraction(placed: list[PlacedTile], grid: int = 48) -> float:
    occupied = [[False] * grid for _ in range(grid)]
    cell_w = CANVAS_WIDTH / grid
    cell_h = CANVAS_HEIGHT / grid
    for item in placed:
        x0 = min(grid - 1, max(0, int(item.pack_x / cell_w)))
        y0 = min(grid - 1, max(0, int(item.pack_y / cell_h)))
        x1 = min(grid - 1, max(0, int((item.pack_x + item.tile.pack_w - 1) / cell_w)))
        y1 = min(grid - 1, max(0, int((item.pack_y + item.tile.pack_h - 1) / cell_h)))
        for y in range(y0, y1 + 1):
            row = occupied[y]
            for x in range(x0, x1 + 1):
                row[x] = True

    largest = 0
    height = [0] * grid
    for y in range(grid):
        for x in range(grid):
            height[x] = 0 if occupied[y][x] else height[x] + 1
        stack: list[int] = []
        for x in range(grid + 1):
            current = height[x] if x < grid else 0
            while stack and height[stack[-1]] > current:
                h = height[stack.pop()]
                left = stack[-1] + 1 if stack else 0
                largest = max(largest, h * (x - left))
            stack.append(x)
    return largest / float(grid * grid)


def summarize_pack(placed: list[PlacedTile]) -> tuple[float, int, int, float]:
    used = sum(item.tile.pack_w * item.tile.pack_h for item in placed)
    coverage = used / float(CANVAS_WIDTH * CANVAS_HEIGHT)
    max_x = max(item.pack_x + item.tile.pack_w for item in placed)
    max_y = max(item.pack_y + item.tile.pack_h for item in placed)
    return coverage, max_x, max_y, largest_empty_fraction(placed)


def interleave_by_base_id(tiles: list[Tile], rng: random.Random) -> list[Tile]:
    """Spread copies of the same logo through the packer queue so they are not adjacent."""
    groups: dict[str, deque[Tile]] = defaultdict(deque)
    order: list[str] = []
    for tile in tiles:
        base_id = tile.source.base_id
        if base_id not in groups:
            order.append(base_id)
        groups[base_id].append(tile)
    rng.shuffle(order)
    for base_id in order:
        items = list(groups[base_id])
        rng.shuffle(items)
        groups[base_id] = deque(items)

    interleaved: list[Tile] = []
    last_id: str | None = None
    while any(groups.values()):
        candidates = [base_id for base_id, queue in groups.items() if queue and base_id != last_id]
        if not candidates:
            candidates = [base_id for base_id, queue in groups.items() if queue]
        pick = max(candidates, key=lambda base_id: len(groups[base_id]))
        interleaved.append(groups[pick].popleft())
        last_id = pick
    return interleaved


def rects_adjacent(
    x1: int, y1: int, w1: int, h1: int,
    x2: int, y2: int, w2: int, h2: int,
) -> bool:
    ax2, ay2 = x1 + w1, y1 + h1
    bx2, by2 = x2 + w2, y2 + h2
    h_gap = max(0, max(x1, x2) - min(ax2, bx2))
    v_gap = max(0, max(y1, y2) - min(ay2, by2))
    neighbor_gap = GAPS_BETWEEN_TILES + 2
    if h_gap == 0 and v_gap == 0:
        return True
    if v_gap <= neighbor_gap and h_gap == 0:
        return True
    if h_gap <= neighbor_gap and v_gap == 0:
        return True
    return False


def placement_distance(a: PlacedTile, b: PlacedTile) -> float:
    return math.hypot(a.pack_x - b.pack_x, a.pack_y - b.pack_y)


def pair_too_close(
    x1: int, y1: int, w1: int, h1: int,
    x2: int, y2: int, w2: int, h2: int,
) -> bool:
    if math.hypot(x1 - x2, y1 - y2) < MIN_DUPLICATE_DISTANCE:
        return True
    return rects_adjacent(x1, y1, w1, h1, x2, y2, w2, h2)


def duplicate_violations(placed: list[PlacedTile]) -> list[tuple[PlacedTile, PlacedTile, float]]:
    by_id: dict[str, list[PlacedTile]] = defaultdict(list)
    for item in placed:
        by_id[item.tile.source.base_id].append(item)
    found: list[tuple[PlacedTile, PlacedTile, float]] = []
    for items in by_id.values():
        if len(items) < 2:
            continue
        for i, left in enumerate(items):
            for right in items[i + 1 :]:
                if pair_too_close(
                    left.pack_x, left.pack_y, left.tile.pack_w, left.tile.pack_h,
                    right.pack_x, right.pack_y, right.tile.pack_w, right.tile.pack_h,
                ):
                    found.append((left, right, placement_distance(left, right)))
    found.sort(key=lambda row: row[2])
    return found


def similar_pack_size(a: PlacedTile, b: PlacedTile, ratio: float = DUPLICATE_SIZE_MATCH) -> bool:
    aw, ah = a.tile.pack_w, a.tile.pack_h
    bw, bh = b.tile.pack_w, b.tile.pack_h
    return min(aw, bw) / max(aw, bw) >= ratio and min(ah, bh) / max(ah, bh) >= ratio


def position_ok_for(item: PlacedTile, new_x: int, new_y: int, placed: list[PlacedTile]) -> bool:
    ncx = new_x + item.tile.pack_w / 2.0
    ncy = new_y + item.tile.pack_h / 2.0
    for other in placed:
        if other is item:
            continue
        if other.tile.source.base_id == item.tile.source.base_id:
            if pair_too_close(
                new_x, new_y, item.tile.pack_w, item.tile.pack_h,
                other.pack_x, other.pack_y, other.tile.pack_w, other.tile.pack_h,
            ):
                return False
        if item.tile.is_anchor and other.tile.is_anchor:
            ocx = other.pack_x + other.tile.pack_w / 2.0
            ocy = other.pack_y + other.tile.pack_h / 2.0
            if math.hypot(ncx - ocx, ncy - ocy) < MINIMUM_HERO_DISTANCE:
                return False
    return True


def find_swap_partner(
    item: PlacedTile,
    placed: list[PlacedTile],
    rng: random.Random,
) -> PlacedTile | None:
    candidates = [other for other in placed if other is not item]
    rng.shuffle(candidates)
    for ratio in (DUPLICATE_SIZE_MATCH, 0.65):
        for other in candidates:
            if other.tile.source.base_id == item.tile.source.base_id:
                continue
            if item.tile.is_anchor and other.tile.is_anchor:
                continue
            if not similar_pack_size(item, other, ratio=ratio):
                continue
            if other.pack_x + item.tile.pack_w > CANVAS_WIDTH or other.pack_y + item.tile.pack_h > CANVAS_HEIGHT:
                continue
            if item.pack_x + other.tile.pack_w > CANVAS_WIDTH or item.pack_y + other.tile.pack_h > CANVAS_HEIGHT:
                continue
            if not position_ok_for(item, other.pack_x, other.pack_y, placed):
                continue
            if not position_ok_for(other, item.pack_x, item.pack_y, placed):
                continue
            return other
    return None


def separate_duplicate_placements(
    placed: list[PlacedTile],
    rng: random.Random,
) -> list[str]:
    """Swap similarly sized non-identical tiles until identical logos are far enough apart."""
    swap_log: list[str] = []
    for _ in range(200):
        violations = duplicate_violations(placed)
        if not violations:
            break
        left, right, _dist = violations[0]
        mover = left
        partner = find_swap_partner(mover, placed, rng)
        if partner is None:
            mover = right
            partner = find_swap_partner(mover, placed, rng)
        if partner is None:
            break
        old_mover = (mover.pack_x, mover.pack_y)
        old_partner = (partner.pack_x, partner.pack_y)
        mover.pack_x, partner.pack_x = partner.pack_x, mover.pack_x
        mover.pack_y, partner.pack_y = partner.pack_y, mover.pack_y
        message = (
            f"Swapped '{mover.tile.source.display_name}' at {old_mover} with "
            f"'{partner.tile.source.display_name}' at {old_partner} "
            "to enforce distance constraint"
        )
        swap_log.append(message)
    return swap_log


def fill_leftover_holes(
    abin,
    placed: list[PlacedTile],
    originals: list[LogoSource],
    small_area: float,
    rng: random.Random,
) -> int:
    if abin is None:
        return 0
    next_index = max(item.tile.index for item in placed) + 1
    extra = 0
    for _ in range(HOLE_FILL_MAX_ITERS):
        holes = [rect for rect in getattr(abin, "_max_rects", [])]
        if not holes:
            break
        hole = max(holes, key=lambda rect: float(rect.width) * float(rect.height))
        if hole.width < HOLE_FILL_MIN_SIDE or hole.height < HOLE_FILL_MIN_SIDE:
            break

        placed_one = False
        counts = Counter(item.tile.source.base_id for item in placed)
        hole_x = int(hole.x + hole.width / 2)
        hole_y = int(hole.y + hole.height / 2)
        candidates = originals[:]
        rng.shuffle(candidates)
        candidates.sort(key=lambda src: counts[src.base_id])
        micro = min(hole.width, hole.height) < MICRO_HOLE_MAX_SIDE * 2
        for source in candidates:
            nearby_same = False
            min_dist = MICRO_DUP_DISTANCE if micro else MIN_DUPLICATE_DISTANCE
            for item in placed:
                if item.tile.source.base_id != source.base_id:
                    continue
                if math.hypot(item.pack_x - hole_x, item.pack_y - hole_y) < min_dist:
                    nearby_same = True
                    break
            if nearby_same:
                continue
            tile = build_snug_tile(
                next_index,
                source,
                int(hole.width),
                int(hole.height),
                tier="micro" if micro else "small",
            )
            if tile.pack_w > hole.width or tile.pack_h > hole.height:
                continue
            copy_id = 10_000 + extra + 1
            tile.source = make_copy(source, copy_id)
            rect = abin.add_rect(tile.pack_w, tile.pack_h, rid=tile.index)
            if rect is None:
                continue
            placed.append(PlacedTile(tile=tile, pack_x=int(rect.x), pack_y=int(rect.y)))
            next_index += 1
            extra += 1
            placed_one = True
            break
        if not placed_one:
            break
    return extra


def rebuild_free_bin(placed: list[PlacedTile]) -> MaxRectsBssf:
    algo = MaxRectsBssf(CANVAS_WIDTH, CANVAS_HEIGHT, rot=False)
    for item in placed:
        commit_rect(algo, item.pack_x, item.pack_y, item.tile.pack_w, item.tile.pack_h, item.tile.index)
    return algo


def fill_micro_voids(
    placed: list[PlacedTile],
    originals: list[LogoSource],
    rng: random.Random,
) -> int:
    """Inject snug micro logos into leftover voids larger than HOLE_FILL_MIN_SIDE."""
    algo = rebuild_free_bin(placed)
    added = fill_leftover_holes(algo, placed, originals, SMALL_TARGET_AREA, rng)
    return added


def placement_collides(
    placed: list[PlacedTile],
    item: PlacedTile,
    x: int,
    y: int,
    w: int,
    h: int,
) -> bool:
    if x < 0 or y < 0 or x + w > CANVAS_WIDTH or y + h > CANVAS_HEIGHT:
        return True
    for other in placed:
        if other is item:
            continue
        if rects_overlap(x, y, w, h, other.pack_x, other.pack_y, other.tile.pack_w, other.tile.pack_h):
            return True
    return False


def elastic_expand(placed: list[PlacedTile], coverage: float) -> float:
    """Grow packed tiles 3–6% into leftover dead space when coverage is under 92%."""
    if coverage >= TARGET_PACKED_COVERAGE or not placed:
        return 1.0
    scale = min(ELASTIC_SCALE_MAX, max(ELASTIC_SCALE_MIN, TARGET_PACKED_COVERAGE / max(coverage, 0.01)))
    ordered = sorted(placed, key=lambda item: item.tile.pack_w * item.tile.pack_h, reverse=True)
    for item in ordered:
        tile = item.tile
        new_pack_w = max(tile.pack_w, int(round(tile.pack_w * scale)))
        new_pack_h = max(tile.pack_h, int(round(tile.pack_h * scale)))
        pad = tile_padding(tile)
        max_pack_w = MAX_LOGO_WIDTH + pad * 2 + GAPS_BETWEEN_TILES
        new_pack_w = min(new_pack_w, max_pack_w)
        if new_pack_w == tile.pack_w and new_pack_h == tile.pack_h:
            continue
        new_x = item.pack_x - (new_pack_w - tile.pack_w) // 2
        new_y = item.pack_y - (new_pack_h - tile.pack_h) // 2
        new_x = max(0, min(CANVAS_WIDTH - new_pack_w, new_x))
        new_y = max(0, min(CANVAS_HEIGHT - new_pack_h, new_y))
        if placement_collides(placed, item, new_x, new_y, new_pack_w, new_pack_h):
            new_x, new_y = item.pack_x, item.pack_y
            if placement_collides(placed, item, new_x, new_y, new_pack_w, new_pack_h):
                continue
        ratio_w = (new_pack_w - GAPS_BETWEEN_TILES) / max(1, tile.card_w)
        ratio_h = (new_pack_h - GAPS_BETWEEN_TILES) / max(1, tile.card_h)
        pad = tile_padding(tile)
        tile.card_w = max(1, new_pack_w - GAPS_BETWEEN_TILES)
        tile.card_h = max(1, new_pack_h - GAPS_BETWEEN_TILES)
        tile.scaled_w = max(1, tile.card_w - pad * 2)
        tile.scaled_h = max(1, tile.card_h - pad * 2)
        tile.scaled_w, tile.scaled_h = clamp_logo_dimensions(tile.scaled_w, tile.scaled_h)
        tile.pack_w = new_pack_w
        tile.pack_h = new_pack_h
        item.pack_x = new_x
        item.pack_y = new_y
        _ = ratio_w, ratio_h
    return scale


def tile_center(item: PlacedTile) -> tuple[float, float]:
    return (
        item.pack_x + item.tile.pack_w / 2.0,
        item.pack_y + item.tile.pack_h / 2.0,
    )


def rects_overlap(
    x1: int, y1: int, w1: int, h1: int,
    x2: int, y2: int, w2: int, h2: int,
) -> bool:
    return not (x1 + w1 <= x2 or x2 + w2 <= x1 or y1 + h1 <= y2 or y2 + h2 <= y1)


def area_center_of_mass(placed: list[PlacedTile]) -> tuple[float, float]:
    total_area = 0.0
    weighted_x = 0.0
    weighted_y = 0.0
    for item in placed:
        area = float(item.tile.pack_w * item.tile.pack_h)
        cx, cy = tile_center(item)
        weighted_x += cx * area
        weighted_y += cy * area
        total_area += area
    if total_area == 0:
        return float(MID_X), float(MID_Y)
    return weighted_x / total_area, weighted_y / total_area


def min_anchor_distance(placed: list[PlacedTile]) -> float:
    anchors = [item for item in placed if item.tile.is_anchor]
    if len(anchors) < 2:
        return float(CANVAS_WIDTH)
    best = float("inf")
    for i, left in enumerate(anchors):
        lcx, lcy = tile_center(left)
        for right in anchors[i + 1 :]:
            rcx, rcy = tile_center(right)
            best = min(best, math.hypot(lcx - rcx, lcy - rcy))
    return best


def heroes_are_dispersed(placed: list[PlacedTile]) -> bool:
    return min_anchor_distance(placed) >= MINIMUM_HERO_DISTANCE * 0.92


def poisson_disk_points(
    count: int,
    min_dist: float,
    rng: random.Random,
    width: int,
    height: int,
    k: int = 30,
) -> list[tuple[float, float]]:
    """Bridson Poisson-disc samples. Returns more points than `count` when possible."""
    if count <= 0:
        return []
    cell = min_dist / math.sqrt(2)
    grid_w = max(1, int(math.ceil(width / cell)))
    grid_h = max(1, int(math.ceil(height / cell)))
    grid = [-1] * (grid_w * grid_h)
    samples: list[tuple[float, float]] = []
    active: list[int] = []

    def cell_index(x: float, y: float) -> int:
        gx = min(grid_w - 1, max(0, int(x / cell)))
        gy = min(grid_h - 1, max(0, int(y / cell)))
        return gy * grid_w + gx

    def far_enough(x: float, y: float) -> bool:
        gx = min(grid_w - 1, max(0, int(x / cell)))
        gy = min(grid_h - 1, max(0, int(y / cell)))
        for iy in range(max(0, gy - 2), min(grid_h, gy + 3)):
            for ix in range(max(0, gx - 2), min(grid_w, gx + 3)):
                idx = grid[iy * grid_w + ix]
                if idx < 0:
                    continue
                sx, sy = samples[idx]
                if math.hypot(x - sx, y - sy) < min_dist:
                    return False
        return True

    x0 = rng.uniform(0, width)
    y0 = rng.uniform(0, height)
    samples.append((x0, y0))
    grid[cell_index(x0, y0)] = 0
    active.append(0)
    target = max(count * 5, count)

    while active and len(samples) < target:
        ai = rng.randrange(len(active))
        px, py = samples[active[ai]]
        placed = False
        for _ in range(k):
            angle = rng.uniform(0.0, math.tau)
            radius = rng.uniform(min_dist, 2.0 * min_dist)
            x = px + math.cos(angle) * radius
            y = py + math.sin(angle) * radius
            if 0 <= x < width and 0 <= y < height and far_enough(x, y):
                grid[cell_index(x, y)] = len(samples)
                samples.append((x, y))
                active.append(len(samples) - 1)
                placed = True
                break
        if not placed:
            active.pop(ai)
    rng.shuffle(samples)
    return samples


def commit_rect(algo: MaxRectsBssf, x: int, y: int, w: int, h: int, rid: int) -> PackRect:
    rect = PackRect(x, y, w, h, rid=rid)
    algo._split(rect)
    algo._remove_duplicates()
    algo.rectangles.append(rect)
    return rect


def choose_seeded_heroes(tiles: list[Tile], rng: random.Random) -> list[Tile]:
    heroes = [tile for tile in tiles if tile.tier == "hero"]
    heroes.sort(key=lambda tile: tile.pack_w * tile.pack_h, reverse=True)
    if not heroes:
        return []
    n_seed = min(len(heroes), rng.randint(MIN_SEEDED_HEROES, MAX_SEEDED_HEROES))
    return heroes[:n_seed]


def place_seeded_heroes(
    algo: MaxRectsBssf,
    anchors: list[Tile],
    rng: random.Random,
) -> bool:
    """Park 5-8 largest heroes with Poisson-disc center spacing; no quadrant boxes."""
    if not anchors:
        return True
    candidates = poisson_disk_points(
        len(anchors),
        MINIMUM_HERO_DISTANCE,
        rng,
        CANVAS_WIDTH,
        CANVAS_HEIGHT,
    )
    occupied_centers: list[tuple[float, float]] = []
    occupied_rects: list[tuple[int, int, int, int]] = []
    ordered = sorted(anchors, key=lambda tile: tile.pack_w * tile.pack_h, reverse=True)

    for tile in ordered:
        tile.is_anchor = True
        best: tuple[int, int, float, float] | None = None
        best_score = -1.0
        search = list(candidates)
        for _ in range(80):
            search.append((rng.uniform(0, CANVAS_WIDTH), rng.uniform(0, CANVAS_HEIGHT)))
        rng.shuffle(search)
        for px, py in search:
            x = int(round(px - tile.pack_w / 2.0))
            y = int(round(py - tile.pack_h / 2.0))
            x = max(0, min(CANVAS_WIDTH - tile.pack_w, x))
            y = max(0, min(CANVAS_HEIGHT - tile.pack_h, y))
            cx = x + tile.pack_w / 2.0
            cy = y + tile.pack_h / 2.0
            if any(
                math.hypot(cx - ox, cy - oy) < MINIMUM_HERO_DISTANCE
                for ox, oy in occupied_centers
            ):
                continue
            if any(
                rects_overlap(x, y, tile.pack_w, tile.pack_h, ox, oy, ow, oh)
                for ox, oy, ow, oh in occupied_rects
            ):
                continue
            if occupied_centers:
                score = min(math.hypot(cx - ox, cy - oy) for ox, oy in occupied_centers)
            else:
                score = MINIMUM_HERO_DISTANCE
            if score > best_score:
                best_score = score
                best = (x, y, cx, cy)
        if best is None:
            return False
        x, y, cx, cy = best
        commit_rect(algo, x, y, tile.pack_w, tile.pack_h, tile.index)
        occupied_centers.append((cx, cy))
        occupied_rects.append((x, y, tile.pack_w, tile.pack_h))
    return True


def try_pack(
    tiles: list[Tile],
    seed: int,
    originals: list[LogoSource],
    small_area: float,
) -> PackResult | None:
    rng = random.Random(seed)
    algo = MaxRectsBssf(CANVAS_WIDTH, CANVAS_HEIGHT, rot=False)
    by_index = {tile.index: tile for tile in tiles}
    for tile in tiles:
        tile.is_anchor = False

    anchors = choose_seeded_heroes(tiles, rng)
    if not place_seeded_heroes(algo, anchors, rng):
        return None

    anchor_ids = {tile.index for tile in anchors}
    fillers = [tile for tile in tiles if tile.index not in anchor_ids]
    rng.shuffle(fillers)  # SORT_NONE: insertion order is a pure shuffle
    packed_ids = set(anchor_ids)
    for tile in fillers:
        rect = algo.add_rect(tile.pack_w, tile.pack_h, rid=tile.index)
        if rect is None:
            continue
        packed_ids.add(tile.index)

    if len(packed_ids) < len(anchors) + max(1, len(fillers) // 2):
        return None

    placed: list[PlacedTile] = []
    for rect in algo.rectangles:
        placed.append(PlacedTile(tile=by_index[rect.rid], pack_x=int(rect.x), pack_y=int(rect.y)))

    hole_rng = random.Random(seed + 17)
    extra_copies = fill_leftover_holes(algo, placed, originals, small_area, hole_rng)
    swap_log = separate_duplicate_placements(placed, random.Random(seed + 17))
    coverage, max_x, max_y, hole = summarize_pack(placed)
    elastic_scale = elastic_expand(placed, coverage)
    micro_fills = fill_micro_voids(placed, originals, random.Random(seed + 31))
    coverage, max_x, max_y, hole = summarize_pack(placed)
    x_com, y_com = area_center_of_mass(placed)
    return PackResult(
        placed=placed,
        coverage=coverage,
        max_x=max_x,
        max_y=max_y,
        largest_hole_fraction=hole,
        seed=seed,
        extra_copies=extra_copies + micro_fills,
        micro_fills=micro_fills,
        elastic_scale=elastic_scale,
        swap_count=len(swap_log),
        swap_log=swap_log,
        duplicate_violations=len(duplicate_violations(placed)),
        x_center_of_mass=x_com,
        y_center_of_mass=y_com,
        min_hero_distance=min_anchor_distance(placed),
    )


def layout_is_balanced(result: PackResult) -> bool:
    return (
        TARGET_COM_X_MIN < result.x_center_of_mass < TARGET_COM_X_MAX
        and TARGET_COM_Y_MIN < result.y_center_of_mass < TARGET_COM_Y_MAX
        and heroes_are_dispersed(result.placed)
    )


def fills_canvas(result: PackResult) -> bool:
    return (
        result.coverage >= MIN_POOL_COVERAGE
        and result.max_x >= CANVAS_WIDTH - MAX_EMPTY_EDGE_MARGIN
        and result.max_y >= CANVAS_HEIGHT - MAX_EMPTY_EDGE_MARGIN
        and result.largest_hole_fraction <= MAX_HOLE_FRACTION
        and result.duplicate_violations == 0
        and layout_is_balanced(result)
    )


def expand_pool(
    originals: list[LogoSource],
    hero_area: float,
    medium_area: float,
    small_area: float,
    rng: random.Random,
    start_copy_id: int,
) -> tuple[list[LogoSource], int]:
    """Duplicate random logos until combined logo pixel area is at least 1.15x the canvas."""
    pool = list(originals)
    copy_id = start_copy_id
    need = int(CANVAS_WIDTH * CANVAS_HEIGHT * POOL_AREA_MULTIPLIER)
    print(
        f"\nBuilding duplicate pool until logo area >= {need:,} px "
        f"(1.15x of {CANVAS_WIDTH * CANVAS_HEIGHT:,})..."
    )
    while pool_logo_pixel_area(pool, hero_area, medium_area, small_area) < need and len(pool) < MAX_TILES:
        counts = Counter(item.base_id for item in pool)
        min_count = min(counts[item.base_id] for item in originals)
        candidates = [item for item in originals if counts[item.base_id] == min_count]
        copy_id += 1
        pool.append(make_copy(rng.choice(candidates), copy_id))
        if copy_id % 50 == 0:
            area = pool_logo_pixel_area(pool, hero_area, medium_area, small_area)
            print(f"  Pool {len(pool)} tiles, logo area {area:,} / {need:,}")
    return pool, copy_id


def find_layout(
    originals: list[LogoSource],
) -> tuple[PackResult, list[LogoSource], float, int, int]:
    hero_area = float(HERO_TARGET_AREA)
    medium_area = float(MEDIUM_TARGET_AREA)
    small_area = float(SMALL_TARGET_AREA)
    rng = random.Random(EXPAND_SEED)
    pool, copy_id = expand_pool(originals, hero_area, medium_area, small_area, rng, start_copy_id=0)
    copies = copy_id

    logo_area = pool_logo_pixel_area(pool, hero_area, medium_area, small_area)
    need = int(CANVAS_WIDTH * CANVAS_HEIGHT * POOL_AREA_MULTIPLIER)
    print(
        f"\nExpanded pool: {len(originals)} originals + {copies} copies = {len(pool)} tiles"
    )
    est = estimated_pack_coverage(pool, hero_area, medium_area, small_area)
    print(
        f"Logo pixel area: {logo_area:,} / target {need:,} "
        f"({POOL_AREA_MULTIPLIER:.2f}x canvas {CANVAS_WIDTH * CANVAS_HEIGHT:,})"
    )
    print(f"Estimated pack coverage before layout: {est * 100:.1f}% of canvas")

    best: PackResult | None = None

    for expand_round in range(1, MAX_EXPAND_ROUNDS + 1):
        for shrink_round in range(1, MAX_SHRINK_ROUNDS + 1):
            area_scale = hero_area / HERO_TARGET_AREA
            n_hero, n_medium, n_small = tier_counts(len(pool))
            est = estimated_pack_coverage(pool, hero_area, medium_area, small_area)
            print(
                f"\nExpand round {expand_round}, scale round {shrink_round}: "
                f"{len(pool)} tiles ({n_hero} hero / {n_medium} medium / {n_small} small), "
                f"area scale {area_scale * 100:.1f}%, estimated coverage {est * 100:.1f}%"
            )

            packed_any = False
            for attempt in range(1, MAX_SHUFFLE_ATTEMPTS + 1):
                seed = expand_round * 100_000 + shrink_round * 1000 + attempt
                tiles = assign_tiers(pool, seed, hero_area, medium_area, small_area)
                result = try_pack(tiles, seed, originals, small_area)
                packed_count = len(result.placed) if result else 0
                extra = ""
                if result:
                    packed_any = True
                    extra = (
                        f", coverage {result.coverage * 100:.1f}%, "
                        f"bbox {result.max_x}x{result.max_y}, "
                        f"hole {result.largest_hole_fraction * 100:.1f}%, "
                        f"X_com={result.x_center_of_mass:.0f}, "
                        f"Y_com={result.y_center_of_mass:.0f}, "
                        f"hero_min_d={result.min_hero_distance:.0f}"
                        f", dups_close={result.duplicate_violations}"
                        f"{f', swaps={result.swap_count}' if result.swap_count else ''}"
                        f"{f', +{result.extra_copies} hole fills' if result.extra_copies else ''}"
                        f"{f', elastic={result.elastic_scale:.3f}' if result.elastic_scale > 1.0 else ''}"
                    )
                    if result.coverage < MIN_POOL_COVERAGE:
                        extra += " [too sparse, retry]"
                    elif not layout_is_balanced(result):
                        extra += " [weight off-center, retry]"
                    com_penalty = math.hypot(
                        result.x_center_of_mass - MID_X,
                        result.y_center_of_mass - MID_Y,
                    )
                    scatter_penalty = 0 if heroes_are_dispersed(result.placed) else 1
                    sparse_penalty = 0 if result.coverage >= MIN_POOL_COVERAGE else 1
                    if best is None or (
                        result.duplicate_violations,
                        sparse_penalty,
                        scatter_penalty,
                        0 if layout_is_balanced(result) else 1,
                        result.largest_hole_fraction,
                        com_penalty,
                        -result.coverage,
                        -result.max_x - result.max_y,
                    ) < (
                        best.duplicate_violations,
                        0 if best.coverage >= MIN_POOL_COVERAGE else 1,
                        0 if heroes_are_dispersed(best.placed) else 1,
                        0 if layout_is_balanced(best) else 1,
                        best.largest_hole_fraction,
                        math.hypot(best.x_center_of_mass - MID_X, best.y_center_of_mass - MID_Y),
                        -best.coverage,
                        -best.max_x - best.max_y,
                    ):
                        best = result
                print(
                    f"  Attempt {attempt}/{MAX_SHUFFLE_ATTEMPTS} (seed={seed}) "
                    f"... packed {packed_count}/{len(pool)}{extra}"
                )
                if result and fills_canvas(result):
                    print(
                        f"\nPacked all {len(pool)} logos edge-to-edge "
                        f"(coverage {result.coverage * 100:.1f}%, "
                        f"X_center_of_mass={result.x_center_of_mass:.0f}, "
                        f"Y_center_of_mass={result.y_center_of_mass:.0f})."
                    )
                    if result.swap_log:
                        print(f"Applied {result.swap_count} coordinate swaps to separate duplicate logos:")
                        for message in result.swap_log:
                            print(f"  {message}")
                    else:
                        print("No duplicate-logo coordinate swaps were needed.")
                    return result, pool, area_scale, shrink_round, attempt

            if packed_any:
                break

            print(
                f"  Pool overflowed the {CANVAS_WIDTH}x{CANVAS_HEIGHT} canvas "
                f"after {MAX_SHUFFLE_ATTEMPTS} shuffle attempts. Shrinking target areas by 12%..."
            )
            hero_area *= AREA_SHRINK_FACTOR
            medium_area *= AREA_SHRINK_FACTOR
            small_area *= AREA_SHRINK_FACTOR
        else:
            break

        if best and fills_canvas(best):
            return best, pool, hero_area / HERO_TARGET_AREA, shrink_round, best.seed

        if packed_any and best is not None:
            if fills_canvas(best):
                return best, pool, hero_area / HERO_TARGET_AREA, shrink_round, best.seed
            print(
                f"  Using best layout from this round: coverage {best.coverage * 100:.1f}%, "
                f"COM X={best.x_center_of_mass:.0f} Y={best.y_center_of_mass:.0f}, "
                f"dups_close={best.duplicate_violations}."
            )
            return best, pool, hero_area / HERO_TARGET_AREA, shrink_round, best.seed

        if len(pool) >= MAX_TILES or pool_logo_pixel_area(pool, hero_area, medium_area, small_area) >= need * 1.25:
            print("  Coverage still has holes but the pool is at the density cap.")
            break

        added = 0
        while added < 6 and len(pool) < MAX_TILES:
            counts = Counter(item.base_id for item in pool)
            min_count = min(counts[item.base_id] for item in originals)
            candidates = [item for item in originals if counts[item.base_id] == min_count]
            copy_id += 1
            pool.append(make_copy(rng.choice(candidates), copy_id))
            added += 1
        copies = copy_id
        print(
            f"  Layout left empty holes. Duplicated {added} more logos "
            f"({copies} copies total, {len(pool)} tiles)."
        )

    if best is None:
        raise RuntimeError(
            f"Could not pack the expanded pool onto {CANVAS_WIDTH}x{CANVAS_HEIGHT}."
        )

    print(
        f"\nUsing best effort layout: coverage {best.coverage * 100:.1f}%, "
        f"bbox {best.max_x}x{best.max_y}, hole {best.largest_hole_fraction * 100:.1f}%, "
        f"X_center_of_mass={best.x_center_of_mass:.0f}, "
        f"Y_center_of_mass={best.y_center_of_mass:.0f}, "
        f"dups_close={best.duplicate_violations}."
    )
    if best.swap_log:
        print(f"Applied {best.swap_count} coordinate swaps to separate duplicate logos:")
        for message in best.swap_log:
            print(f"  {message}")
    return best, pool, hero_area / HERO_TARGET_AREA, 0, best.seed


def render_mosaic(placed: list[PlacedTile]) -> Image.Image:
    gray = hex_to_rgb(BACKGROUND_COLOR)
    canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), gray + (255,))
    draw = ImageDraw.Draw(canvas)
    card_rgb = hex_to_rgb(TILE_CARD_COLOR or BACKGROUND_COLOR)

    total = len(placed)
    print(f"\nRendering mosaic ({total} tiles onto {CANVAS_WIDTH}x{CANVAS_HEIGHT} @ {OUTPUT_DPI} DPI)...")
    for index, item in enumerate(placed, start=1):
        tile = item.tile
        pad = tile_padding(tile)
        card_x = item.pack_x
        card_y = item.pack_y
        logo_x = card_x + pad
        logo_y = card_y + pad

        if DRAW_TILE_CARDS:
            box = (card_x, card_y, card_x + tile.card_w, card_y + tile.card_h)
            if CARD_CORNER_RADIUS > 0:
                draw.rounded_rectangle(box, radius=CARD_CORNER_RADIUS, fill=card_rgb)
            else:
                draw.rectangle(box, fill=card_rgb)

        logo = tile.source.image.resize(
            (tile.scaled_w, tile.scaled_h),
            Image.Resampling.LANCZOS,
        )
        if logo.mode != "RGBA":
            logo = logo.convert("RGBA")
        halo = apply_soft_halo(logo)
        ox = logo_x - (halo.width - tile.scaled_w) // 2
        oy = logo_y - (halo.height - tile.scaled_h) // 2
        canvas.paste(halo, (ox, oy), halo)
        canvas.paste(logo, (logo_x, logo_y), mask=logo)
        if index % 50 == 0 or index == total:
            print(f"  Rendered {index}/{total} tiles...")

    return canvas


def main() -> int:
    print("=" * 72)
    print("Logo mosaic collage (Spoonflower 54x36 @ 150 DPI, mid-gray alpha paste)")
    print(f"Canvas: {CANVAS_WIDTH}x{CANVAS_HEIGHT} px  |  54x36 in @ {OUTPUT_DPI} DPI")
    print(
        f"Background: {BACKGROUND_COLOR}  |  Cards: {TILE_CARD_COLOR}  |  "
        f"{field_kind()} field  |  grout={GAPS_BETWEEN_TILES}px  |  "
        f"max logo width={MAX_LOGO_WIDTH}px ({MAX_LOGO_WIDTH_INCHES:.0f}\")"
    )
    print("Packer: Poisson heroes + MaxRectsBssf; LANCZOS paste + Gaussian halo")
    print("=" * 72)

    paths = discover_logo_paths()
    print(f"\nFound {len(paths)} logo files in {LOGOS_DIR}")
    if not paths:
        print(f"No logo images found. Place PNG/JPG files in {LOGOS_DIR}")
        return 1

    originals = load_logo_sources(paths)
    result, pool, area_scale, shrink_round, attempt = find_layout(originals)
    copies = sum(1 for src in pool if src.copy_id is not None) + result.extra_copies
    n_hero, n_medium, n_small = tier_counts(len(pool))

    print("\n" + "-" * 72)
    print(f"Original logos: {len(originals)}")
    print(f"Duplicated copies: {copies} ({result.extra_copies} of those filled leftover holes)")
    print(f"Placed tiles: {len(result.placed)}")
    print(f"Tiers: {n_hero} hero / {n_medium} medium / {n_small} small")
    print(f"Pack coverage: {result.coverage * 100:.1f}% of canvas (min {MIN_POOL_COVERAGE * 100:.0f}%)")
    print(f"Micro-fillers injected: {result.micro_fills}  |  Elastic scale: {result.elastic_scale:.3f}")
    print(f"X center of mass: {result.x_center_of_mass:.0f} (target {TARGET_COM_X_MIN}-{TARGET_COM_X_MAX})")
    print(f"Y center of mass: {result.y_center_of_mass:.0f} (target {TARGET_COM_Y_MIN}-{TARGET_COM_Y_MAX})")
    print(f"Seeded hero min distance: {result.min_hero_distance:.0f}px (min {MINIMUM_HERO_DISTANCE})")
    print(f"Duplicate spacing: min {MIN_DUPLICATE_DISTANCE}px, remaining close pairs={result.duplicate_violations}, swaps={result.swap_count}")
    max_logo_w = max(item.tile.scaled_w for item in result.placed)
    print(
        f"Max placed logo width: {max_logo_w}px "
        f"({max_logo_w / OUTPUT_DPI:.2f} in, cap {MAX_LOGO_WIDTH}px / {MAX_LOGO_WIDTH_INCHES:.0f}\")"
    )
    print(
        f"Packed bbox: {result.max_x}x{result.max_y} of {CANVAS_WIDTH}x{CANVAS_HEIGHT} "
        f"(largest hole {result.largest_hole_fraction * 100:.1f}%)"
    )
    print(
        f"Winning shuffle: seed={result.seed}, attempt={attempt}, "
        f"shrink round={shrink_round}, area scale={area_scale * 100:.1f}%"
    )
    print("-" * 72)

    canvas = render_mosaic(result.placed)
    output_path = Path(OUTPUT_FILE)
    if not output_path.is_absolute():
        output_path = PROJECT_DIR / output_path
    canvas.save(output_path, "PNG", dpi=(150, 150), quality=95)
    print(f"\nSaved {output_path} ({CANVAS_WIDTH}x{CANVAS_HEIGHT} @ {OUTPUT_DPI} DPI)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
