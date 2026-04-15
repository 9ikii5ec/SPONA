# SVG Tools

A desktop GUI application for converting SVG files to PNG, generating normal maps, building sprite atlases, and batch renaming files. Built with Python and Tkinter.

![Python](https://img.shields.io/badge/Python-3.9+-blue) ![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)

---

## Requirements

- Python 3.9+
- [cairosvg](https://cairosvg.org/) — SVG rendering
- [Pillow](https://pillow.readthedocs.io/) — image processing
- [numpy](https://numpy.org/) — normal map generation

Optional (for better PNG compression):
- [pngquant](https://pngquant.org/) — lossy compression
- [oxipng](https://github.com/shssoichiro/oxipng) — lossless optimization

Install dependencies:
```
pip install cairosvg Pillow numpy
```

> **Windows note:** The app includes a bundled GTK3 runtime in `GTK3-Runtime Win64/` — no separate GTK installation needed.

---

## How to Run

### Option 1 — Download Release (no Python required)

Download the latest prebuilt `.exe` from the [Releases](../../releases) page and run it directly. No installation needed.

### Option 2 — Run from source (latest version)

Install dependencies once:
```
pip install cairosvg Pillow numpy
```

Then run:
```
cd Program
python svg_converter_gui.py
```

---

## Tabs

### 1. SVG → PNG

Converts SVG files to PNG with optional compression and normal map generation.

| Setting | Description |
|---|---|
| Files or folder | Pick individual SVG files or an entire folder (processes recursively) |
| Save to | Output directory. Defaults to same folder as source files |
| Scale | Size multiplier for the output PNG. `2×` = twice the resolution. Use this to increase quality — DPI alone won't help for most SVGs |
| DPI | Only affects SVGs that use physical units (mm, pt). Leave at 96 for pixel-based SVGs |
| Normal map strength | How sharp the relief edges are on the normal map |
| Normal map blur | Gaussian blur applied before normal map generation. Higher values = smoother normals, fewer artifacts from drawn shadows/outlines. Recommended: 4–10 for cartoon sprites |
| Normal map mode | **By shape (alpha)** — uses the alpha channel as a height map, gives clean volume on silhouette edges. Recommended for 2D sprites. **By brightness (grayscale)** — uses pixel brightness as height, better for textures and heightmaps |
| Relief | **Convex** — light areas protrude forward. **Concave** — light areas go inward |
| Flip Y (for Unity) | Flips the Y axis of the normal map. Keep enabled when using with Unity 2D lighting |
| Compression quality | Controls lossy PNG compression. 100 = best quality, largest file. 10 = smallest file, visible quality loss. Uses pngquant if installed, falls back to Pillow |
| No compression | Skip compression entirely — output PNG is saved as-is after conversion |
| Skip normal map | Convert SVG to PNG only, without generating a `_normal.png` file |
| Delete source SVG | Removes the original SVG file after successful conversion |

**Output files per SVG:**
- `filename.png` — converted image
- `filename_normal.png` — normal map (unless skipped)

---

### 2. Sprite Atlas

Packs multiple images into a single atlas texture.

| Setting | Description |
|---|---|
| Files or folder | Source images (PNG, JPG, SVG, WebP, BMP) |
| Save atlas | Output file path for the atlas PNG |
| Padding | Gap in pixels between sprites |
| Columns | Number of columns in the grid layout |
| Background | Transparent, white, or black fill for empty areas |

Each sprite keeps its original size. The cell size is determined by the largest sprite. Sprites are centered within their cell.

---

### 3. File Renaming

Batch rename files with find/replace, prefix/suffix, and auto-numbering.

| Setting | Description |
|---|---|
| Files or folder | Files to rename |
| Extensions | Filter by file type. `*` = all files, `.png .svg` = only those types |
| Find / Replace | Text substitution in the filename (without extension) |
| Regex | Enables regular expressions in the Find field |
| Case sensitive | When off, `Old` and `old` are treated the same |
| Prefix / Suffix | Text added before or after the filename stem |
| Numbering | Appends a sequential counter `_001`, `_002`... to each file. Set the starting number with the field next to it |

The **Preview** table shows "Before → After" for all files. Files that will change are highlighted green, unchanged files are grey. Preview updates automatically as you type.

**Example — replace broken numbering with sequential:**
- Find: `_\d+` (Regex enabled)
- Replace: *(empty)*
- Numbering: on, starting from `1`
- Result: `Grass_001.png`, `Grass_002.png`, `Grass_003.png`...

---

### 4. Normal Map

Generate normal maps from existing PNG files — useful when you've already converted SVGs and deleted the originals.

| Setting | Description |
|---|---|
| PNG files or folder | Source images to process |
| Save to | Output directory |
| Normal map strength | Sharpness of the relief |
| Blur | Smoothing before normal map calculation |
| Normal map mode | By shape (alpha) or by brightness (grayscale) |
| Relief | Convex or concave |
| Flip Y | For Unity 2D compatibility |
| Name suffix | Appended to the output filename. Default: `_normal` → `image_normal.png` |

---

## Normal Maps in Unity 2D

For normal maps to work correctly with Unity's 2D lighting:

1. Select the `_normal.png` in the Project panel
2. In the Inspector set **Texture Type → Normal map**
3. Create a material with shader `Universal Render Pipeline/2D/Sprite-Lit-Default`
4. Assign the normal map to the **Normal Map** slot in the material
5. Assign the material to the **Sprite Renderer** component
6. Make sure your `Light 2D` has **Normal Maps → Quality: Accurate** and **Distance > 0**

**Recommended settings for cartoon/2D sprites:**
- Mode: By shape (alpha)
- Strength: 3–5
- Blur: 6–10
- Flip Y: enabled
