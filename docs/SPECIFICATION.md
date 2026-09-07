# Zach Portland Spoonflower Logo Mosaic — Rebuild Specification

This is the contract for regenerating or reimplementing the print. Implement **only** the live pipeline in §5–§8. Do **not** restore the rejected algorithms, libraries, scripts, or output files in §4.

The generator is a single Python file: [`logo_mosaic.py`](../logo_mosaic.py). There is no `logo_mosaic_2.py` (or `_3` / `_4` / `_5`); those names were conversation aliases for the same file. Do not create them.

Last verified generate (2026-09-07): **8698** tiles, **8100×5400** RGBA PNG @ 150 DPI, **~23 s**, written to [`spoonflower_logo_mosaic_54x36.png`](../spoonflower_logo_mosaic_54x36.png).

---

## 1. Product

Print-ready fabric pattern of **52 Portland venue and artist logos**, packed as an organic sticker-bomb for a **Spoonflower** upload.

| Property | Value |
|---|---|
| Canvas | 54″ × 36″ |
| Resolution | 150 DPI → **8100 × 5400** px |
| Background | `#808080` (opaque mid-gray) |
| Look | Black or white luma-masked marks; light marks get a 2 px dark outer stroke |
| Packing | Organic nesting (script/curve cutouts may interlock); 1 px grout |
| Output | PNG with `dpi=(150, 150)`, `compress_level=2` |

Venue and artist marks in `logos/` remain the property of their respective owners. Generator and docs: MIT, Copyright (c) 2026 Hillwork LLC ([`LICENSE`](../LICENSE)).

---

## 2. Repository layout (live)

All paths are relative to the repository root.

| Path | Role |
|---|---|
| `logo_mosaic.py` | **Only** generator. Phase 1 luma-mask + LANCZOS, Phase 2 bbox+alpha pack, Phase 3 ProcessPool composite |
| `logos/` | **52** source JPEGs, named `Zach <mark>.jpg` (see §2.1) |
| `spoonflower_logo_mosaic_54x36.png` | Current print file (overwritten on each run) |
| `requirements.txt` | `numpy>=2.0.0` and `Pillow>=10.0.0` only |
| `README.md` | Human overview; if it disagrees with this file, **this file wins** |
| `docs/SPECIFICATION.md` | This contract |
| `LICENSE` | MIT |
| `.gitignore` | Ignores `.venv/`, `__pycache__/`, `_debug*` |
| `.venv/` | Local virtualenv (not committed) |

Sources live in `logos/`. The script discovers files there (`LOGOS_DIR = Path(__file__).parent / "logos"`), skipping anything whose name is the output PNG, starts with `logo_mosaic` / `spoonflower_`, or starts with `_debug`. ID for packing is `path.stem` (e.g. `Zach Kann.jpg` → `Zach Kann`).

### 2.1 Source files (`logos/`)

52 files. Web-scraped rasters: **no SVG/EPS**, almost all opaque JPEG “cards” (white or dark rectangles) with JPEG ringing and mixed aspect ratios.

- `logos/Zach Abigail Hall.jpg`
- `logos/Zach Alanis Morissette 2.jpg`
- `logos/Zach Alanis Morissette.jpg`
- `logos/Zach Bar Diane.jpg`
- `logos/Zach Billie Eilish.jpg`
- `logos/Zach Burger Syndicate.jpg`
- `logos/Zach Cafe Umbria.jpg`
- `logos/Zach Can Font.jpg`
- `logos/Zach Canard.jpg`
- `logos/Zach Cheese and Crack.jpg`
- `logos/Zach Constantinople.jpg`
- `logos/Zach Departure.jpg`
- `logos/Zach Eem.jpg`
- `logos/Zach Emerald Line.jpg`
- `logos/Zach Fillmore.jpg`
- `logos/Zach Fools and Horses.jpg`
- `logos/Zach Function Logo.jpg`
- `logos/Zach Good Coffee.jpg`
- `logos/Zach Guay Tiew.jpg`
- `logos/Zach Hey Love.jpg`
- `logos/Zach Hey Luigi.jpg`
- `logos/Zach Janken 2.jpg`
- `logos/Zach Janken.jpg`
- `logos/Zach Kann.jpg`
- `logos/Zach Karaoke from Hell.jpg`
- `logos/Zach Kasbah.jpg`
- `logos/Zach Lang Baan.jpg`
- `logos/Zach Lela_s.jpg`
- `logos/Zach Momoyama.jpg`
- `logos/Zach Multnomah Whiskey Library.jpg`
- `logos/Zach Ovation.jpg`
- `logos/Zach Ox.jpg`
- `logos/Zach Paa Dee.jpg`
- `logos/Zach Pleasure Burger.jpg`
- `logos/Zach Quaintrelle 2.jpg`
- `logos/Zach Quaintrelle.jpg`
- `logos/Zach Repulica.jpg`
- `logos/Zach Ringside.jpg`
- `logos/Zach Silk Road.jpg`
- `logos/Zach Snowbunny.jpg`
- `logos/Zach Stem Wine Bar.jpg`
- `logos/Zach Takara Sushi.jpg`
- `logos/Zach Tartuca.jpg`
- `logos/Zach Teardrop.jpg`
- `logos/Zach The Heathman.jpg`
- `logos/Zach The Paper Bridge 2.jpg`
- `logos/Zach The Paper Bridge.jpg`
- `logos/Zach The Star 2.jpg`
- `logos/Zach The Star.jpg`
- `logos/Zach Treebeerds.jpg`
- `logos/Zach Voicebox.jpg`
- `logos/Zach nimblefish.jpg`

Early in the project these JPEGs sat in the **repository root**. They were moved into `logos/`. Do not look for sources at `./Zach *.jpg`.

---

## 3. Requirements (what “done” looks like)

1. **Density.** Micro-fill until bbox occupancy ≥ **0.90**, or every quadrant’s bbox-area fraction ≥ 0.90, or 24 consecutive empty micro passes. Occupancy is **sum of placed bounding-box areas / canvas area**. Nested transparent regions **overcount**; do not treat this as ink-pixel coverage.
2. **Card isolation.** Opaque JPEG/PNG rectangles must disappear. Marks become solid **black** or **white** ink with a luminance-derived alpha. No leftover white/tan/black cards.
3. **Legibility on `#808080`.** Light/white marks get a **2 px** black `MaxFilter` stroke when opaque RGB mean luma ≥ **140**. Dark marks stay black, anti-aliased (no binary snap).
4. **Duplicate distance.** Same logo ID: Euclidean distance between **bbox centers** ≥ **350 px** (all tiers).
5. **Size mix.** A shuffled primary queue of 9000 hero/medium/small placements (fractions renormalized; see §6), then unbounded micro-fill. **Not** a strict 10/35/35/20 mix of all tiles on the canvas.
6. **Speed.** Finish on a modern multi-core laptop in tens of seconds, not hours. Phase 1 uses threads; Phase 3 uses `ProcessPoolExecutor` (thread fallback).
7. **Reproducible sampling.** `LAYOUT_SEED = 2026` for shuffle and `randint`. Tile count can still vary slightly if process/thread scheduling changes work order in Phase 1 padding; the layout RNG itself is seeded.

---

## 4. Do not restore (rejected paths)

If you are implementing this from the spec, **skip everything in this section**. These were tried, failed or were superseded, and were deleted. Re-introducing them will reproduce known bad looks or hangs.

### 4.1 Phantom / scratch scripts (never the live entry point)

These paths must **not** be created or used as the generator:

| Path | Why it is listed |
|---|---|
| `logo_mosaic_2.py` | User alias for `logo_mosaic.py`; file never existed |
| `logo_mosaic_3.py` | Same |
| `logo_mosaic_4.py` | Same |
| `logo_mosaic_5.py` | Same |
| `_engine_tail.py` | Temporary scratch during the occupancy-grid rewrite; deleted |

The only script to edit or run is `logo_mosaic.py`.

### 4.2 Rejected proof outputs

These files are **historical proofs**. Do not point `OUTPUT_FILE` at them. Do not treat them as the Spoonflower deliverable. They live under `test_runs/` after being moved off the repo root.

| Path | What it was | Why rejected |
|---|---|---|
| `test_runs/logo_mosaic_16x16.png` (was `./logo_mosaic_16x16.png`) | First 16″×16″ / 2400² @ 150 DPI; white rounded cards, dark-slate grout, rectpack MaxRects | Card collage, not a sticker-bomb; MaxRects stacked by area |
| `test_runs/logo_mosaic_grayscale_scattered.png` (was `./logo_mosaic_grayscale_scattered.png`) | High-contrast binary B/W + `SORT_NONE` MaxRectsBssf | Jagged thresholding; still rectangular cards |
| `test_runs/logo_mosaic_final_legible.png` (was `./logo_mosaic_final_legible.png`) | Autocontrast + per-logo `LOGO_OVERRIDES` strokes | Override soup; still MaxRects |
| `test_runs/logo_mosaic_fluid_seamless.png` (was `./logo_mosaic_fluid_seamless.png`) | Bridson Poisson hero seeds + MaxRectsBaf | Poisson heroes + MaxRects fillers still made packing walls |
| `test_runs/logo_mosaic_dense_seamless.png` (was `./logo_mosaic_dense_seamless.png`) | Tighter grout, hole-fill, elastic scale | Still MaxRects; “elastic” overdraw |
| `test_runs/logo_mosaic_8x8_high_density.png` (was `./logo_mosaic_8x8_high_density.png`) | 8″×8″ / 1200² charcoal `#1A1A1A` | Wrong print size; theme experiment |
| `test_runs/logo_mosaic_adaptable_color.png` (was `./logo_mosaic_adaptable_color.png`) | Theme-following strokes / B&W snap | Noisy, blown-out type |
| `test_runs/logo_mosaic_clean_vector.png` (was `./logo_mosaic_clean_vector.png`) | Contrast 1.8 + silhouette stroke, claimed “vector” | Morphological outlines, not isolation |
| `test_runs/logo_mosaic_mid_gray_final.png` (was `./logo_mosaic_mid_gray_final.png`) | 8″ `#808080` Gaussian-halo alpha paste | Soft halo, still MaxRects cards; led to the 54×36 size but not the packer |
| `test_runs/spoonflower_logo_mosaic_54x36_3in_cap.png` | 54×36 with **3″ / 450 px** max logo width | Logos too large; later capped near 1″ |

**Live output only:** `./spoonflower_logo_mosaic_54x36.png` (also `OUTPUT_FILE` in the script).

### 4.3 Debug dumps (gitignored)

| Path | Why rejected |
|---|---|
| `_debug_normalized/` | Per-logo flood-fill dumps (`Zach Ox__white_flood-fill.png`, etc.). Not inputs. `.gitignore` already has `_debug*` |

Do not commit or re-enable writing these unless debugging preprocess.

### 4.4 Rejected libraries and requirements

Do **not** add these back to `requirements.txt` or import them:

| Library | Tried for | Why rejected |
|---|---|---|
| `rectpack` (`MaxRectsBlsf`, `MaxRectsBssf`, `MaxRectsBaf`, `newPacker`, `PackingBin.BNF`, `SORT_AREA`, `SORT_NONE`) | Rectangle bin packing | Grid-like shelves, top-left gravity, column walls; hung or stalled on 54×36; cannot nest into transparent cutouts |
| `scipy` / `scipy.spatial` | Bridson / Poisson disc | Uniform blue-noise **points** ignore logo aspect ratios; left large gray gaps around wide wordmarks |
| OpenCV (`cv2`), Canny, morphological outlines | “Vector” edges | Destroyed anti-aliasing; jagged 150 DPI type |

Live stack: **Python 3.10+**, **Pillow**, **NumPy**.

### 4.5 Rejected card-stripping (Phase 1)

Do not restore these functions or flags (deleted in commit `7d03fbe`):

- Corner-sampled **4-connected flood-fill** (`punch_background`, `flood_background_mask`, `classify_background`). JPEG ringing (`#FFFFFF` → `#FAFAFA`) stalled or leaked; letter counters (O, P, A, B) kept white plugs.
- **Interior-seeded** floods and `strip_bg` / `remove_bg` overrides.
- **Hard luma threshold** (e.g. drop pixels with \(L > 240\)) and binary B/W snap (`to_high_contrast_mono`, `BW_BLACK_CUTOFF` / `BW_WHITE_CUTOFF`). Stair-cased edges at 150 DPI.
- **`FRINGE_DESPILL`** (~52). Ate letter counters.
- **`remove_white_card_border`**: edge-only peel of luma ≥ 210. Left interior card fill or inverted to black/white rectangles when combined with invert.
- **`LOGO_OVERRIDES` invert / contrast / stroke dict** (Kann, Takara, nimblefish, Hey Luigi, Snowbunny, Heathman, Paper Bridge, Fillmore, Function Logo, …). Per-file polarity was wrong more often than right; luma-mean 128 replaced it. The dict must stay **absent** (not an empty dict that later grows invert flags).
- **Gaussian halo** (`HALO_BLUR`, `HALO_STRENGTH`, `SHADOW_COLOR`, `GLOW_COLOR`). Soft mush on gray.
- **`DRAW_TILE_CARDS`**, rounded-rect cards, `TILE_CARD_COLOR`, `GAPS_BETWEEN_TILES` as card grout, `LOGO_INTERNAL_PADDING`.
- **`force_full_opacity`**: snap alpha to 0/255. Kills AA.
- Nearest-neighbor / Box / Bilinear downscale. Always **LANCZOS**.

### 4.6 Rejected packers (Phase 2)

AABB overlap is **only** the broad phase. Do not use AABB as the sole collision test (caps density ~50–60% because wordmarks have empty boxes).

Do not restore:

- **rectpack MaxRects** (BLSF / BSSF / BAF), including 5×4 bin splits, 4-corner MaxRects, walking BAF/BSSF over the free-rect list, interior anchors.
- **Bridson Poisson** hero seeds + MaxRects fillers (`MINIMUM_HERO_DISTANCE`, dart throwing).
- **Gravity / shelf** packing (top-left fill, horizontal shelves).
- **Jittered spatial grid** (`CELL_SIZE_*`, `GRID_UNIT`).
- **Downscaled occupancy bitmask** (~1:10, `OCC_SCALE`, `OCC_ROWS`×`OCC_COLS` integral images, spiral search). Blocky, clustered, slow; “bitmask slicing” is **not** the live engine.
- **Pure Python pairwise** collision over all placed items without NumPy (hours at 5k+ tiles × 250 tries).
- **Center-of-mass gates**, `MAX_HOLE_FRACTION`, elastic upsample (`ELASTIC_SCALE_*`), area-based tier targets (`HERO_TARGET_AREA`, etc.).
- **Phase 2c evenness sweep** that biased samples into the weakest quadrant. Removed on purpose with unused-code cleanup. Quadrant math remains only as a **stop condition** for micro-fill (`needs_fill`), not as a sampler. Do not re-add `quad=` placement unless the user asks.

### 4.7 Rejected size regimes

Do not silently revert to these caps (longest side, unless noted):

| Regime | Hero / med / small / micro | Notes |
|---|---|---|
| 3″ print cap | 450 px max width | `test_runs/spoonflower_logo_mosaic_54x36_3in_cap.png` |
| First 54×36 bbox pack | 220 / 150 / 100 / 25–75, dup 500, queue 4500 | Too large on fabric |
| 50% linear (¼ area) | 110 / 75 / 50 / 15–38, queue 18000, dup 500 | Same-ID stalls; sparse |
| Occupancy-grid inches | 375 / 225 / 120 / 60 (and earlier 400–550 heroes) | Too big; grid artifacts |

Live caps: **156 / 106 / 71 / randint(21, 50)**, duplicate **350**, `PRIMARY_QUEUE` **9000**.

---

## 5. Phase 1 — Preprocess (ThreadPool)

For each path from `discover_logo_paths()`:

### 5.1 Normalize (`normalize_logo`)

Convert to RGBA.

**If the source already has useful alpha** (`RGBA`/`LA` with min alpha < 250, or palette transparency): `crop_to_alpha` (bbox of alpha, pad 1), then `to_soft_grayscale` (keep the alpha; RGB → gray). **Do not** luma-mask these.

**Else (typical JPEG card)** — `luma_mask_opaque`:

1. `gray = ImageOps.grayscale(RGB)`.
2. `mean = float(np.asarray(gray).mean())` over the **whole** image (not a masked subset).
3. If `mean >= 128`: dark-on-light card → `alpha = invert(gray)`, RGB = **(0,0,0)**.
4. If `mean < 128`: light-on-dark card → `alpha = gray`, RGB = **(255,255,255)**.
5. Merge RGBA; `crop_to_alpha(..., pad=0)`.

### 5.2 Cap, contrast, stroke (`_preprocess_job`)

1. `enforce_pixel_cap(normalized, ABSOLUTE_MAX_PX)` — LANCZOS thumbnail so longest side ≤ **156**.
2. `boost_contrast_keep_aa`:
   - Autocontrast the **alpha** (`cutoff=1`), then apply `CONTRAST_LUT`.
   - LUT for \(i \in [0,255]\):
     \[
     t = \mathrm{clamp}\big((i/255 - 0.5)\times 1.9 + 0.5,\, 0,\, 1\big)
     \]
     then hard-clip: if \(t < 0.06\) → 0; if \(t > 0.94\) → 1; store \( \mathrm{round}(t \times 255) \).
   - RGB contrast **only if** opaque (`alpha ≥ 16`) gray std > **8**; then same autocontrast + LUT on the gray RGB.
3. If opaque RGB mean luma ≥ `LIGHT_MARK_LUMA` (**140**): `apply_dark_stroke` — pad 2 px, `MaxFilter` on alpha with kernel `2*pad+1` = 5, paste black, then the mark. If longest side exceeds 156 after stroke, LANCZOS thumbnail again.

Cache PNG bytes. Print one line per file (`luma-mask dark-on-light` / `light-on-dark` and the pixel shrink).

### 5.3 Tier buffers (`preprocess_assets`)

Keep a master per stem in `logos_dict`. For each logo, also cache LANCZOS copies at `TIER_MAX["medium"]` **106** and `TIER_MAX["small"]` **71**. With `rng = Random(LAYOUT_SEED)`, **40%** of those cached medium/small buffers get `_add_transparent_hpad`: 10–30 px empty margin on left **or** right. That staggers bboxes; do not change the packer to fake jitter.

Phase 1 runs in `ThreadPoolExecutor` (`RENDER_WORKERS = cpu_count`).

---

## 6. Phase 2 — Layout (bbox cull + exact alpha)

Seed: `rng = random.Random(LAYOUT_SEED)`.

### 6.1 Collision

Store placed boxes in `boxes = np.zeros((80000, 4), dtype=np.int32)` as `(x1, y1, x2, y2)` and matching ink masks in a Python list.

**Ink mask:** alpha dilated with `MaxFilter(grout*2+1)` (odd kernel), then `alpha >= ALPHA_INK_MIN` (**32**). Grout is `BOX_PAD = 1` (hero/med/small) or `MICRO_PAD = 1` (micro).

For a candidate `(x, y, w, h)`:

1. Reject if same-ID center is within **350 px** (`_too_close_id`, compare squared distance).
2. Broad phase (NumPy, all `n_box` rows — this is quadratic in C, not a spatial index):

```python
b = boxes[:n_box]
hit = ((x - pad) < b[:, 2]) & ((x + w + pad) > b[:, 0]) & \
      ((y - pad) < b[:, 3]) & ((y + h + pad) > b[:, 1])
idxs = np.flatnonzero(hit)
```

3. Narrow phase **only** on `idxs`: intersect rectangles; if `(mask_a & mask_b).any()` on the overlap, reject. Transparent corners **may** nest.

Accept: write box, append ink, `n_box += 1`, add `w*h` to `covered`.

`try_place` samples `PLACE_TRIES` (250) up to `PLACE_TRIES_MAX` (300) uniform random top-left positions that keep the logo on-canvas (`sample_xy(..., quad=None)`). Do not pass a quadrant unless re-adding evenness by request.

### 6.2 Primary queue (Phase 2a)

Micro is **not** in this queue. Renormalize:

```text
n_hero  = max(1, round(PRIMARY_QUEUE * 0.10 / 0.80))   # 1125
n_med   = round(PRIMARY_QUEUE * 0.35 / 0.80)           # 3938
n_small = PRIMARY_QUEUE - n_hero - n_med               # 3937
```

Build `[(random logo id, tier)] * counts`, shuffle. For each item: up to **8** tries, first with the queued id then random ids, `get_scaled(id, TIER_MAX[tier])`. If still unplaced and tier ≠ small, one more attempt at **small** with `PLACE_TRIES_MAX`.

Micros are **not** in `TIER_MAX`. Phase 2b uses `randint(21, 50)` only. Do not add a `TIER_MAX['micro']` cap of 42.

### 6.3 Micro-fill (Phase 2b)

While `needs_fill()` and fewer than **24** consecutive empty passes:

- Shuffle all logo ids.
- For each, `try_place(..., lanczos to randint(21, 50), grout=MICRO_PAD, tries=PLACE_TRIES_MAX)`.
- If any placement succeeded, reset empty-pass counter; else increment.

`needs_fill()` is true if occupancy < 0.90 **or** the minimum of the four quadrant bbox-area fractions < 0.90. Quadrants are by **center** of each placed box. This is a stop condition only.

Return `placed_items`: `{id, img, pos: (x, y), tier}`.

---

## 7. Phase 3 — Composite (ProcessPool)

`hex_to_rgb("#808080")`. Split the canvas into `RENDER_WORKERS` horizontal bands. Each worker pastes overlapping tiles with `Image.paste(..., mask=layer)` onto an opaque gray band, PNG-compresses the band, parent pastes bands at `y0`.

If `ProcessPoolExecutor` fails, fall back to `ThreadPoolExecutor` (`_map_parallel`).

Save `PROJECT_DIR / Path(OUTPUT_FILE)` as PNG, `dpi=(150, 150)`, `compress_level=2`.

There is **no** save-time size guard. The micro floor is the `randint(21, 50)` sampler in Phase 2b.

---

## 8. Parameters (authoritative)

All of these live at the top of `logo_mosaic.py`.

| Name | Value | Meaning |
|---|---|---|
| `CANVAS_WIDTH` / `CANVAS_HEIGHT` | 8100 / 5400 | 54″ × 36″ @ 150 DPI |
| `BACKGROUND_COLOR` | `#808080` | Field |
| `OUTPUT_FILE` | `./spoonflower_logo_mosaic_54x36.png` | Written next to the script |
| `OUTPUT_DPI` | 150 | PNG DPI metadata |
| `ABSOLUTE_MAX_PX` | 156 | Hero / master cap (~1.04″) |
| `TIER_MAX['hero']` | 156 | Primary-queue hero |
| `TIER_MAX['medium']` | 106 | |
| `TIER_MAX['small']` | 71 | |
| Micro fill | `randint(21, 50)` | Not a `TIER_MAX` key |
| `HERO_FRACTION` / `MEDIUM_FRACTION` / `MICRO_FRACTION` | 0.10 / 0.35 / 0.20 | Small count is `PRIMARY_QUEUE` minus hero/medium; micro excluded from the 9000 |
| `PRIMARY_QUEUE` | 9000 | Hero/med/small placements before micro |
| `PLACE_TRIES` / `PLACE_TRIES_MAX` | 250 / 300 | Random samples per item |
| `BOX_PAD` / `MICRO_PAD` | 1 / 1 | Grout dilation on ink masks |
| `ALPHA_INK_MIN` | 32 | Ignore near-clear fringe in collision |
| `MIN_DUPLICATE_DISTANCE` | 350 | Same-ID center distance (px) |
| `TARGET_OCCUPANCY` | 0.90 | Bbox occupancy stop |
| `LAYOUT_SEED` | 2026 | Shuffle + sampling |
| `STROKE_PX` / `STROKE_COLOR` | 2 / (0,0,0) | Light-mark outline |
| `LIGHT_MARK_LUMA` | 140 | When to stroke |
| `RENDER_WORKERS` | `os.cpu_count()` | Phase 3 processes; Phase 1 threads |

If you change canvas size, change `CANVAS_WIDTH` / `CANVAS_HEIGHT` only. Duplicate distance stays in **pixels**.

---

## 9. Setup and run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONUNBUFFERED=1 .venv/bin/python logo_mosaic.py
```

Use unbuffered stdio: preprocess logs one line per logo; packing can take ~10–20 s with no extra prints. ProcessPool needs a normal (non-sandbox) process. Kill leftover `logo_mosaic.py` processes if a previous run was interrupted (it can sit at high CPU).

Expected log shape:

1. `Phase 1: preprocess sources …` then 52 `Zach ….jpg: luma-mask … thumbnail≤156px …`
2. `Phase 2a: bbox + exact-alpha pack...`
3. `Phase 2b: micro-fill sweep...`
4. `Phase 3: process-pool composite (N tiles)...`
5. `Saved …/spoonflower_logo_mosaic_54x36.png (8100x5400 @ 150 DPI) in …s`

A healthy run is thousands of tiles (recent: ~8700), not 52, and finishes well under a minute.

---

## 10. Implementation checklist for another AI

- [ ] Single entry: `logo_mosaic.py`. No `logo_mosaic_N.py`, no `rectpack`, no OpenCV, no scipy.
- [ ] Inputs: `logos/Zach *.jpg` (52). Output: `./spoonflower_logo_mosaic_54x36.png`.
- [ ] Opaque cards → mean-128 luma-to-alpha, solid black or white RGB, crop, LANCZOS ≤156, autocontrast+clipped LUT on alpha, 2 px MaxFilter stroke if opaque luma ≥ 140.
- [ ] Pack: random samples → 350 px same-ID → NumPy AABB cull → full-res alpha AND. 1 px grout. Nest into holes.
- [ ] Queue 9000 (1125 / 3938 / 3937) then micro `randint(21,50)` until occupancy/quadrants or 24 empty passes.
- [ ] ProcessPool band composite onto `#808080`.
- [ ] Do not restore flood-fill, invert overrides, occupancy grids, MaxRects, Poisson, Gaussian halos, tile cards, or the files in `test_runs/`.
