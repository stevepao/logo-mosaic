# Zach Portland logo mosaic

Print-ready fabric pattern of Portland venue and artist logos, packed as a dense mosaic for a **Spoonflower** upload.

The current deliverable is `spoonflower_logo_mosaic_54x36.png`: a **54″ × 36″** canvas at **150 DPI** (8100 × 5400 px) on mid-gray `#808080`. Logos are grayscale with clean alpha, a soft Gaussian halo so black and white marks both read on gray, and a **3″ maximum printed width**.

## Layout

| Path | What it is |
|---|---|
| `logo_mosaic.py` | Generator: normalize logos, build a duplicate pool, pack with rectpack, render PNG |
| `logos/` | Original source JPEGs (52 marks) |
| `spoonflower_logo_mosaic_54x36.png` | Current print file |
| `test_runs/` | Earlier proofs and rejected looks |
| `requirements.txt` | Python dependencies |
| `LICENSE` | MIT License, Copyright (c) 2026 Hillwork LLC |
| `.gitignore` | Ignores `.venv`, bytecode, OS junk |

### `test_runs/`

These are obsolete generator outputs kept for reference. They are not used at runtime.

| File | What it was |
|---|---|
| `logo_mosaic_16x16.png` | First 16″ canvas, white cards on dark grout |
| `logo_mosaic_grayscale_scattered.png` | Early gray-field packing |
| `logo_mosaic_final_legible.png` / `fluid_seamless.png` / `dense_seamless.png` | Coverage and spacing experiments |
| `logo_mosaic_8x8_high_density.png` | 8″ high-density proof |
| `logo_mosaic_adaptable_color.png` | Color-theme tests |
| `logo_mosaic_clean_vector.png` | Contrast/stroke treatment (rejected) |
| `logo_mosaic_mid_gray_final.png` | 8″ `#808080` alpha-paste proof that led to the Spoonflower file |

## Technical requirements

- **Canvas:** 54″ × 36″ at 150 DPI → 8100 × 5400 px
- **Background:** `#808080` (tile cards match so grout is invisible)
- **Logo sizes:** area-based tiers (~1.5″–3″ in print), then width-capped at 3″ (450 px)
  - Hero (~10% of pool): ~120,000–160,000 px²
  - Medium (~35%): ~45,000–70,000 px²
  - Small (~55%): ~15,000–25,000 px²
  - Micro fillers: ~4,000–8,000 px²
- **Pool:** duplicate source logos until combined logo area is at least **1.15×** the canvas (~50.3M px)
- **Packing:** Poisson-scattered hero seeds, then MaxRectsBssf; no visible quadrant grid
- **Duplicates:** identical logos kept at least ~4.8″ apart (720 px)
- **Coverage:** packed tile rectangles cover ≥ 90% of the canvas
- **Processing:** JPEG box/flood-fill punch → RGBA grayscale → `LANCZOS` resize → alpha paste. No binary threshold, Otsu, or dilation/MaxFilter strokes
- **Halo:** Gaussian blur of the alpha mask; dark shadow on light marks, light glow on dark marks
- **Output:** PNG with `dpi=(150, 150)`

A 54×36 render typically takes a few minutes (pool + packing attempts + LANCZOS composite).

## Setup

Python 3.10+ with a virtualenv:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Dependencies: Pillow, rectpack.

## Run

```bash
.venv/bin/python logo_mosaic.py
```

The script prints pool growth, each packing attempt, and render progress, then overwrites `spoonflower_logo_mosaic_54x36.png` in this directory.

Add or replace source files in `logos/` (`jpg`, `png`, `webp`, etc.). Filenames can stay as `Zach ….jpg`; the script uses the stem as the display name.

## Parameters to tweak

All of these live at the top of `logo_mosaic.py`.

### Canvas and color

| Parameter | Current | Notes |
|---|---|---|
| `CANVAS_WIDTH` / `CANVAS_HEIGHT` | 8100 / 5400 | Pixels = inches × `OUTPUT_DPI` |
| `OUTPUT_DPI` | 150 | Spoonflower print DPI |
| `BACKGROUND_COLOR` | `#808080` | Mid-gray field |
| `TILE_CARD_COLOR` | same as background | Set a contrast color if you want visible cards |
| `OUTPUT_FILE` | `./spoonflower_logo_mosaic_54x36.png` | Written next to the script |
| `GAPS_BETWEEN_TILES` | 8 | Grout between pack rects |
| `LOGO_INTERNAL_PADDING` | 8 | Inset around each mark |

### Logo scale

| Parameter | Current | Notes |
|---|---|---|
| `MAX_LOGO_WIDTH_INCHES` | 3.0 | Hard cap; panoramic logos shrink to this width and keep aspect ratio |
| `HERO_TARGET_AREA` | 140,000 | Square heroes ≈ 2.5″; wide heroes hit the width cap first |
| `MEDIUM_TARGET_AREA` | 55,000 | |
| `SMALL_TARGET_AREA` | 20,000 | |
| `MICRO_TARGET_AREA` | 6,000 | Hole-fill leftovers |
| `HERO_FRACTION` / `MEDIUM_FRACTION` | 0.10 / 0.35 | Rest of the pool is small |

### Packing and spacing

| Parameter | Current | Notes |
|---|---|---|
| `MIN_POOL_COVERAGE` | 0.90 | Reject layouts sparser than this |
| `POOL_AREA_MULTIPLIER` | 1.15 | How oversized the duplicate pool is before packing |
| `MIN_DUPLICATE_DISTANCE` | 720 | Min gap between copies of the same logo (~4.8″) |
| `MINIMUM_HERO_DISTANCE` | 1100 | Poisson separation of seeded heroes |
| `MAX_SHUFFLE_ATTEMPTS` | 40 | Packer shuffles per round; then take the best coverage layout |
| `HALO_BLUR` / `HALO_STRENGTH` | 8.0 / 0.5 | Print-scale glow/shadow |
| `SHADOW_COLOR` / `GLOW_COLOR` | `#2A2A2A` / `#F2F2F2` | Halo tint for light vs dark marks |

`LOGO_OVERRIDES` can force invert/no-invert for a specific filename stem if a mark comes out wrong.

## Pipeline notes

Source files are JPEGs with white, tan, or black boxes. The script flood-fills those pads and crops to the remaining alpha so marks sit on the gray field without a rectangle. Do not restore binary thresholding or morphological outlines — those produced jagged, blown-out “stickers.”

If you change canvas size, also rescale COM windows (`TARGET_COM_X_*` / `TARGET_COM_Y_*`), duplicate distance, and hero distance. Width cap is derived from `MAX_LOGO_WIDTH_INCHES * OUTPUT_DPI`.

## License

Copyright (c) 2026 Hillwork LLC. This project is released under the [MIT License](LICENSE).

The MIT license covers the generator, documentation, and project files. Venue and artist marks in `logos/` remain the property of their respective owners.

