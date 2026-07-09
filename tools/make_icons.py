"""Génère les icônes PWA de BetsFix -> static/icon-{180,192,512}.png.

Balle de tennis verte sur fond sombre (identité de la marque). Lancé une fois ;
re-lancer seulement si on change le design. python tools/make_icons.py
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(_ROOT, "static")
BG = (10, 12, 17)
BALL = (46, 226, 127)


def make(size: int) -> Image.Image:
    s = size
    img = Image.new("RGB", (s, s), BG)
    d = ImageDraw.Draw(img)
    # léger halo radial vert (rendu simple : un grand cercle très sombre dégradé manuel)
    pad = int(s * 0.17)
    box = [pad, pad, s - pad, s - pad]
    d.ellipse(box, fill=BALL)
    # couture de balle de tennis : deux arcs sombres
    w = max(3, int(s * 0.035))
    r = (s - 2 * pad) / 2
    cx = cy = s / 2
    d.arc([cx - r * 2.0, cy - r, cx + r * 0.2, cy + r], start=305, end=55, fill=BG, width=w)
    d.arc([cx - r * 0.2, cy - r, cx + r * 2.0, cy + r], start=125, end=235, fill=BG, width=w)
    return img


def from_logo(size: int, src: Image.Image) -> Image.Image:
    """Cadre le logo (transparent ou non, ratio quelconque) sur un carre a fond plein,
    SANS deformer : padding centre puis resize. Une icone PWA doit avoir un fond opaque.
    """
    logo = src.convert("RGBA")
    side = max(logo.size)
    canvas = Image.new("RGBA", (side, side), BG + (255,))
    canvas.paste(logo, ((side - logo.size[0]) // 2, (side - logo.size[1]) // 2), logo)
    return canvas.convert("RGB").resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    logo_path = os.path.join(OUT, "logo.png")
    src = Image.open(logo_path) if os.path.exists(logo_path) else None
    if src is not None:
        print(f"  source : logo.png ({src.size[0]}x{src.size[1]})")
    for size in (180, 192, 512):
        img = from_logo(size, src) if src is not None else make(size)
        img.save(os.path.join(OUT, f"icon-{size}.png"))
        print(f"  icon-{size}.png")
    print(f"icones -> {OUT}")


if __name__ == "__main__":
    main()
