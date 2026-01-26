#!/usr/bin/env python3
"""
Generate favicon assets from a source PNG/SVG using Pillow.

Outputs into server/app/static/ by default:
  - favicon.ico (16,32,48,64,128,256)
  - favicon-16x16.png
  - favicon-32x32.png
  - apple-touch-icon.png (180x180)

Usage:
  python tools/make_favicon.py path/to/source.png

If no path is given, looks for server/app/static/icon-source.png

Requires: Pillow (pip install pillow)
"""

from __future__ import annotations

import sys
from pathlib import Path
import argparse
from typing import List, Tuple

try:
    from PIL import Image
except Exception:
    print(
        "ERROR: Pillow not installed. Install with: pip install pillow", file=sys.stderr
    )
    raise


REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = REPO_ROOT / "server" / "app" / "static"


def load_image(src_path: Path) -> Image.Image:
    img = Image.open(src_path).convert("RGBA")
    return img


def trim_transparent(img: Image.Image, alpha_threshold: int = 1) -> Image.Image:
    """Trim fully transparent borders using the alpha channel.

    alpha_threshold: 0-255. Pixels with alpha <= threshold are considered empty.
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    alpha = img.split()[-1]
    # Create a binary mask where > threshold
    bbox = alpha.point(lambda a: 255 if a > alpha_threshold else 0).getbbox()
    if not bbox:
        return img
    return img.crop(bbox)


def pad_to_square(
    img: Image.Image,
    padding_ratio: float = 0.04,
    bg: Tuple[int, int, int, int] = (0, 0, 0, 0),
) -> Image.Image:
    """Place the image centered on a square canvas with uniform padding.

    padding_ratio defines the empty border on each side as a fraction of the
    final square side. E.g. 0.06 = 6% padding around.
    """
    w, h = img.size
    content_side = max(w, h)
    # Compute the final square side so that content fits after padding
    side = int(round(content_side / (1.0 - 2.0 * padding_ratio)))
    side = max(side, content_side)
    canvas = Image.new("RGBA", (side, side), bg)
    # Scale up content to fill the target, keeping aspect
    scale = (1.0 - 2.0 * padding_ratio) * side / content_side
    new_w = int(round(w * scale))
    new_h = int(round(h * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    off = ((side - new_w) // 2, (side - new_h) // 2)
    canvas.paste(resized, off, mask=resized if resized.mode == "RGBA" else None)
    return canvas


def save_ico(img: Image.Image, out_path: Path, sizes: List[int]) -> None:
    variants = []
    for s in sizes:
        variants.append(img.resize((s, s), Image.LANCZOS))
    # Use the largest image as the base; Pillow will embed all sizes
    base = variants[-1]
    base.save(out_path, format="ICO", sizes=[(s, s) for s in sizes])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate favicon assets from an image"
    )
    parser.add_argument("src", nargs="?", help="Path to source image (PNG/SVG)")
    parser.add_argument(
        "--padding",
        type=float,
        default=0.04,
        help="Padding ratio around content (default: 0.04)",
    )
    args = parser.parse_args()

    if args.src:
        src = Path(args.src).expanduser().resolve()
    else:
        src = STATIC_DIR / "icon-source.png"

    if not src.exists():
        print(f"ERROR: Source image not found: {src}", file=sys.stderr)
        print("Place your PNG at that path or pass a path explicitly.", file=sys.stderr)
        sys.exit(1)

    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    img = load_image(src)
    # 1) Trim away fully transparent borders, then 2) add a consistent padding
    # to visually match typical favicon fill (crisper and not too small).
    img = trim_transparent(img)
    img = pad_to_square(img, padding_ratio=max(0.0, min(args.padding, 0.2)))

    # Export multi-size ICO
    ico_path = STATIC_DIR / "favicon.ico"
    save_ico(img, ico_path, [16, 32, 48, 64, 128, 256])
    print(f"Wrote {ico_path}")

    # Export PNGs
    for s in (16, 32):
        out = STATIC_DIR / f"favicon-{s}x{s}.png"
        img.resize((s, s), Image.LANCZOS).save(out, format="PNG")
        print(f"Wrote {out}")

    # Apple touch icon (180x180); keep transparency.
    apple = STATIC_DIR / "apple-touch-icon.png"
    img.resize((180, 180), Image.LANCZOS).save(apple, format="PNG")
    print(f"Wrote {apple}")


if __name__ == "__main__":
    main()
