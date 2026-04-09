#!/usr/bin/env python3
"""
SVG → PNG converter with optimization and normal map generation.
Usage:
    python svg_converter.py file.svg
    python svg_converter.py file1.svg file2.svg file3.svg
    python svg_converter.py ./folder_with_svgs
"""

import sys
import os
import argparse
from pathlib import Path

# ─── Auto-detect local GTK3 runtime ───────────────────────────────────────────
_gtk_local = Path(__file__).parent / "GTK3-Runtime Win64" / "bin"
if _gtk_local.exists():
    os.environ["PATH"] = str(_gtk_local) + os.pathsep + os.environ.get("PATH", "")

try:
    import cairosvg
except ImportError:
    print("Missing: cairosvg. Install with: pip install cairosvg")
    sys.exit(1)

try:
    from PIL import Image
    import numpy as np
except ImportError:
    print("Missing: Pillow / numpy. Install with: pip install Pillow numpy")
    sys.exit(1)

import subprocess
import shutil


# ─── SVG → PNG ────────────────────────────────────────────────────────────────

def svg_to_png(svg_path: Path, out_path: Path, dpi: int = 96) -> None:
    """Convert SVG to PNG using cairosvg."""
    cairosvg.svg2png(
        url=str(svg_path),
        write_to=str(out_path),
        dpi=dpi,
    )
    print(f"  [convert]  {svg_path.name} → {out_path.name}")


# ─── PNG optimization ─────────────────────────────────────────────────────────

def optimize_png(png_path: Path) -> None:
    """
    Optimize PNG in-place.
    Strategy (mirrors what online optimizers do):
      1. pngquant  – lossy palette quantization (huge size reduction)
      2. oxipng    – lossless re-compression / metadata strip
    Falls back gracefully if tools are missing.
    """
    original_size = png_path.stat().st_size

    # --- pngquant (lossy, ~60-80 % reduction) ---
    pngquant_bin = shutil.which("pngquant")
    if pngquant_bin:
        tmp = png_path.with_suffix(".tmp.png")
        result = subprocess.run(
            [pngquant_bin, "--quality=65-90", "--force",
             "--output", str(tmp), str(png_path)],
            capture_output=True,
        )
        if result.returncode == 0 and tmp.exists():
            tmp.replace(png_path)
    else:
        print("  [optimize] pngquant not found – skipping lossy step "
              "(install pngquant for best results)")

    # --- oxipng (lossless, optional system binary) ---
    oxipng_bin = shutil.which("oxipng")
    if oxipng_bin:
        subprocess.run(
            [oxipng_bin, "--opt", "4", "--strip", "all", str(png_path)],
            capture_output=True,
        )
    else:
        # fallback: Pillow re-save with max compression
        img = Image.open(png_path)
        img.save(png_path, format="PNG", optimize=True, compress_level=9)

    new_size = png_path.stat().st_size
    saved = original_size - new_size
    pct = saved / original_size * 100 if original_size else 0
    print(f"  [optimize] {png_path.name}  "
          f"{original_size//1024} KB → {new_size//1024} KB  "
          f"(−{pct:.1f} %)")


# ─── Normal map ───────────────────────────────────────────────────────────────

def generate_normal_map(png_path: Path, out_path: Path, strength: float = 4.0) -> None:
    """
    Generate a tangent-space normal map from a grayscale heightmap.
    The PNG is converted to grayscale, then Sobel gradients are used
    to compute per-pixel normals, which are packed into RGB (DirectX convention).
    """
    img = Image.open(png_path).convert("L")  # grayscale heightmap
    h = np.array(img, dtype=np.float32) / 255.0

    # Sobel kernels
    kx = np.array([[-1, 0, 1],
                   [-2, 0, 2],
                   [-1, 0, 1]], dtype=np.float32)
    ky = np.array([[-1, -2, -1],
                   [ 0,  0,  0],
                   [ 1,  2,  1]], dtype=np.float32)

    def convolve2d(arr, kernel):
        """Simple 3x3 convolution via numpy stride tricks."""
        pad = np.pad(arr, 1, mode="edge")
        out = np.zeros_like(arr)
        for i in range(3):
            for j in range(3):
                out += pad[i:i+arr.shape[0], j:j+arr.shape[1]] * kernel[i, j]
        return out

    gx = convolve2d(h, kx) * strength
    gy = convolve2d(h, ky) * strength

    # Normal vector N = normalize(-gx, -gy, 1)
    gz = np.ones_like(gx)
    length = np.sqrt(gx**2 + gy**2 + gz**2)
    nx = -gx / length
    ny = -gy / length
    nz =  gz / length

    # Pack to [0, 255]  (N * 0.5 + 0.5)
    r = ((nx * 0.5 + 0.5) * 255).clip(0, 255).astype(np.uint8)
    g = ((ny * 0.5 + 0.5) * 255).clip(0, 255).astype(np.uint8)
    b = ((nz * 0.5 + 0.5) * 255).clip(0, 255).astype(np.uint8)

    normal_img = Image.fromarray(np.stack([r, g, b], axis=-1), mode="RGB")
    normal_img.save(out_path)
    print(f"  [normal]   {out_path.name}")


# ─── Pipeline ─────────────────────────────────────────────────────────────────

def process_svg(svg_path: Path, output_dir: Path, dpi: int, strength: float) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    png_path    = output_dir / svg_path.with_suffix(".png").name
    normal_path = output_dir / (svg_path.stem + "_normal.png")

    print(f"\n▶ {svg_path}")
    svg_to_png(svg_path, png_path, dpi=dpi)
    optimize_png(png_path)
    generate_normal_map(png_path, normal_path, strength=strength)


def collect_svgs(inputs: list[str]) -> list[Path]:
    svgs = []
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            svgs.extend(sorted(p.glob("**/*.svg")))
        elif p.is_file() and p.suffix.lower() == ".svg":
            svgs.append(p)
        else:
            print(f"  [skip] {inp} – not an SVG file or directory")
    return svgs


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Convert SVG → PNG, optimize, and generate normal maps."
    )
    parser.add_argument(
        "inputs", nargs="+",
        help="SVG file(s) or folder(s)"
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output directory (default: same as each input file)"
    )
    parser.add_argument(
        "--dpi", type=int, default=96,
        help="Render DPI for SVG→PNG (default: 96)"
    )
    parser.add_argument(
        "--strength", type=float, default=4.0,
        help="Normal map gradient strength (default: 4.0)"
    )
    args = parser.parse_args()

    # scipy check (needed for normal map) — no longer needed, using pure numpy
    svgs = collect_svgs(args.inputs)
    if not svgs:
        print("No SVG files found.")
        sys.exit(1)

    print(f"Found {len(svgs)} SVG file(s).")

    for svg in svgs:
        out_dir = Path(args.output) if args.output else svg.parent
        process_svg(svg, out_dir, dpi=args.dpi, strength=args.strength)

    print("\n✓ Done.")


if __name__ == "__main__":
    main()
