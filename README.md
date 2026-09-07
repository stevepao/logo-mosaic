# Zach Portland logo mosaic

Print-ready fabric pattern of Portland venue and artist logos, packed as an organic sticker-bomb mosaic for a **Spoonflower** upload.

The current deliverable is `spoonflower_logo_mosaic_54x36.png`: a **54″ × 36″** canvas at **150 DPI** (8100 × 5400 px) on mid-gray `#808080`. Opaque source JPEGs are converted with luminance-to-alpha (no invert overrides, no interior flood-fill). Light-on-dark cards become white ink; dark-on-light cards become black ink. White marks get a 2px dark stroke. Placement uses bounding-box culling plus exact alpha collision so curved and script marks can nest; grout is 1px.

## Layout

| Path | What it is |
|---|---|
| `logo_mosaic.py` | Generator: Phase 1 luma-mask + LANCZOS, Phase 2 bbox+alpha pack, Phase 3 ProcessPool PNG |
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
| `spoonflower_logo_mosaic_54x36_3in_cap.png` | Prior 54×36 file with 3″ max logo width |

## Technical requirements

- **Canvas:** 54″ × 36″ at 150 DPI → 8100 × 5400 px
- **Background:** `#808080`
- **Logo sizes** (longest side, LANCZOS thumbnail @ 150 DPI):
  - Hero: 156 px (~1.04″), hard cap `ABSOLUTE_MAX_PX`
  - Medium: 106 px
  - Small: 71 px
  - Micro fill: 21–50 px (`randint(21, 50)`; `TIER_MAX['micro']` is 42)
- **Packing:** shuffled hero / medium / small queue (`PRIMARY_QUEUE` 9000), then micro-fill and an evenness sweep. Each item tries 250–300 random coordinates. Stage 1 is a vectorized bbox overlap test; Stage 2 (only on overlapping boxes) is a full-resolution alpha AND (`alpha ≥ 32`, dilated by grout). Transparent corners may nest.
- **Grout:** 1 px (`BOX_PAD` / `MICRO_PAD`)
- **Duplicates:** same logo ID at least 350 px apart (all tiers)
- **Coverage target:** >90% bbox occupancy; evenness pass prefers the weakest quadrant
- **Processing (opaque JPEG/PNG):** grayscale L → mean luminance. If mean ≥ 128 (dark ink on a light card): `alpha = 255 − L`, RGB = black. If mean < 128 (light ink on a dark card): `alpha = L`, RGB = white. Crop to the new alpha box. LANCZOS-cap, S-curve contrast on the alpha, 2px dark MaxFilter stroke on light marks (`opaque RGB mean ≥ 140`), then `canvas.paste(..., mask=logo)`. Sources that already have useful alpha skip luma-masking and keep their mask.
- **Render:** ProcessPool horizontal-band composite
- **Output:** PNG with `dpi=(150, 150)`

## Setup

Python 3.10+ with a virtualenv:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Dependencies: Pillow, NumPy.

## Run

```bash
PYTHONUNBUFFERED=1 .venv/bin/python logo_mosaic.py
```

The script prints timing for preprocess, pack, composite, and save, then overwrites `spoonflower_logo_mosaic_54x36.png` in this directory.

Add or replace source files in `logos/` (`jpg`, `png`, `webp`, etc.). Filenames can stay as `Zach ….jpg`; the script uses the stem as the ID.

## Parameters to tweak

All of these live at the top of `logo_mosaic.py`.

### Canvas and color

| Parameter | Current | Notes |
|---|---|---|
| `CANVAS_WIDTH` / `CANVAS_HEIGHT` | 8100 / 5400 | Pixels = inches × `OUTPUT_DPI` |
| `OUTPUT_DPI` | 150 | Spoonflower print DPI |
| `BACKGROUND_COLOR` | `#808080` | Mid-gray field |
| `OUTPUT_FILE` | `./spoonflower_logo_mosaic_54x36.png` | Written next to the script |

### Logo scale

| Parameter | Current | Notes |
|---|---|---|
| `ABSOLUTE_MAX_PX` | 156 | Hero cap (~1.04″); sources are LANCZOS-capped at load |
| `TIER_MAX['hero']` | 156 px | |
| `TIER_MAX['medium']` | 106 px | |
| `TIER_MAX['small']` | 71 px | |
| `TIER_MIN['micro']` / `TIER_MAX['micro']` | 21 / 42 px | Micro-fill samples `randint(21, 50)` |
| `MIN_LOGO_PX` | 21 | Refuses to save if a placed mark is smaller |

### Packing and spacing

| Parameter | Current | Notes |
|---|---|---|
| `BOX_PAD` / `MICRO_PAD` | 1 | Grout around opaque ink |
| `MIN_DUPLICATE_DISTANCE` | 350 | Same-ID center distance in pixels |
| `TARGET_OCCUPANCY` | 0.90 | Micro-fill until this bbox occupancy |
| `PRIMARY_QUEUE` | 9000 | Hero/medium/small mix before micro-fill |
| `PLACE_TRIES` / `PLACE_TRIES_MAX` | 250 / 300 | Random samples per item; skip if all miss |
| `LAYOUT_SEED` | 2026 | Reproducible shuffle and sampling |
| `RENDER_WORKERS` | CPU count | ProcessPool bands (threads for Phase 1) |
| `STROKE_PX` | 2 | Dark outer stroke on light (white) marks |
| `LIGHT_MARK_LUMA` | 140 | Stroke threshold on opaque mean RGB luma |
| `ALPHA_INK_MIN` | 32 | Exact-mask collision ignores fringe |

### `LOGO_OVERRIDES`

The dict is empty. Card backgrounds are stripped by mean-luminance masking, not per-file `invert` flags. Keys still match filename, stem, or a normalized alias (`Zach Takara Sushi.jpg` → `takara_sushi`) if you need a future override.

About 40% of medium and small **cached** buffers also get a 10–30px transparent left or right margin in Phase 1 so packed bboxes stagger without changing the packer.

## Pipeline notes

**Phase 1** does not flood-fill JPEG boxes or invert selected stems. Opaque sources become a solid black or white mark whose alpha is the (inverted or raw) luminance, then LANCZOS-cap at 156 px, S-curve contrast on that alpha, and a 2px dark stroke when the mark is light. Do not restore binary thresholding or morphological outlines — those produced jagged, blown-out “stickers.”

**Phase 2** does not use MaxRects or a downscaled occupancy grid. Random candidate points are accepted if (1) no same-ID copy is within 350 px, (2) no padded bbox overlap, or (3) overlapping bboxes have no opaque-alpha intersection.

**Phase 3** alpha-composites tiles in horizontal bands via `ProcessPoolExecutor`.

If you change canvas size, update `CANVAS_WIDTH` / `CANVAS_HEIGHT` only. Duplicate distance stays in pixels.

Printed “pixel coverage” is the sum of placed bounding boxes over the canvas, so it can overcount nested transparent regions.

## License

Copyright (c) 2026 Hillwork LLC. This project is released under the [MIT License](LICENSE).

The MIT license covers the generator, documentation, and project files. Venue and artist marks in `logos/` remain the property of their respective owners.
