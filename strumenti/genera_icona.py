#!/usr/bin/env python3
"""
genera_icona.py — Disegna l'icona di BiaAmp (PNG 1024 px + ICNS).

Uso:  python3 strumenti/genera_icona.py        (richiede Pillow)

Disegno originale: piastra in metallo brunito con display LCD e
analizzatore di spettro nei colori della skin (teal, oro, rosa).
Segue la griglia delle icone macOS: piastra 824×824 su tela 1024×1024.

© 2026 B. Raucci — Licenza MIT
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

TELA = 1024
LATO, RAGGIO = 824, 185
TEAL, ORO, ROSA = (109, 240, 210), (247, 200, 115), (255, 138, 138)


def mescola(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def gradiente_verticale(w, h, alto, basso):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=mescola(alto, basso, y / max(1, h - 1)))
    return img


def maschera_arrotondata(w, h, r):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    return m


def disegna() -> Image.Image:
    icona = Image.new("RGBA", (TELA, TELA), (0, 0, 0, 0))
    o = (TELA - LATO) // 2

    # ombra portata
    ombra = Image.new("RGBA", (TELA, TELA), (0, 0, 0, 0))
    ImageDraw.Draw(ombra).rounded_rectangle([o, o + 18, o + LATO, o + LATO + 18], radius=RAGGIO, fill=(0, 0, 0, 140))
    icona = Image.alpha_composite(icona, ombra.filter(ImageFilter.GaussianBlur(22)))

    # piastra in metallo brunito con spazzolatura
    piastra = gradiente_verticale(LATO, LATO, (64, 71, 89), (25, 28, 37)).convert("RGBA")
    # ImageDraw sostituisce i pixel senza fonderli: la spazzolatura semitrasparente
    # va disegnata su un livello a parte e poi composta.
    spazzolato = Image.new("RGBA", (LATO, LATO), (0, 0, 0, 0))
    ds = ImageDraw.Draw(spazzolato)
    for y in range(0, LATO, 6):
        ds.line([(0, y), (LATO, y)], fill=(255, 255, 255, 9))
    piastra = Image.alpha_composite(piastra, spazzolato)
    d = ImageDraw.Draw(piastra)
    d.rounded_rectangle([3, 3, LATO - 4, LATO - 4], radius=RAGGIO - 3, outline=(120, 128, 146, 255), width=5)
    icona.paste(piastra, (o, o), maschera_arrotondata(LATO, LATO, RAGGIO))

    # display LCD incassato
    d = ImageDraw.Draw(icona)
    lx0, ly0, lx1, ly1 = o + 104, o + 150, o + LATO - 104, o + LATO - 150
    d.rounded_rectangle([lx0 - 10, ly0 - 10, lx1 + 10, ly1 + 10], radius=48, fill=(8, 9, 12))
    d.rounded_rectangle([lx0, ly0, lx1, ly1], radius=40, fill=(4, 10, 9))

    # analizzatore di spettro: 9 barre, gradiente teal → oro → rosa
    n, margine = 9, 46
    largo = (lx1 - lx0 - 2 * margine) / n
    altezze = [0.42, 0.66, 0.88, 0.74, 0.58, 0.80, 0.52, 0.36, 0.22]
    base, alto_max = ly1 - 52, ly1 - ly0 - 104
    for i, f in enumerate(altezze):
        x0 = lx0 + margine + i * largo + 7
        x1 = x0 + largo - 14
        h = f * alto_max
        passo = 18
        k = 0
        while k * passo < h:
            yb = base - k * passo
            t = (k * passo) / alto_max
            col = mescola(TEAL, ORO, t / 0.6) if t < 0.6 else mescola(ORO, ROSA, (t - 0.6) / 0.4)
            d.rectangle([x0, yb - passo + 5, x1, yb], fill=col)
            k += 1
        yp = base - h - 26                       # cappello del picco
        d.rectangle([x0, yp, x1, yp + 7], fill=(255, 227, 227))

    # riflesso sul vetro del display
    riflesso = Image.new("RGBA", (TELA, TELA), (0, 0, 0, 0))
    ImageDraw.Draw(riflesso).polygon(
        [(lx0 + 30, ly0 + 8), (lx1 - 30, ly0 + 8), (lx1 - 30, ly0 + 70), (lx0 + 30, ly0 + 150)], fill=(255, 255, 255, 9))
    icona = Image.alpha_composite(icona, riflesso)

    # tre viti decorative in basso
    d = ImageDraw.Draw(icona)
    for i in range(3):
        cx, cy, r = o + LATO / 2 + (i - 1) * 64, o + LATO - 78, 14
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(30, 33, 42), outline=(110, 118, 136), width=3)
        a = math.radians(35 + 40 * i)
        d.line([(cx - r * 0.7 * math.cos(a), cy - r * 0.7 * math.sin(a)),
                (cx + r * 0.7 * math.cos(a), cy + r * 0.7 * math.sin(a))], fill=(110, 118, 136), width=3)
    return icona


def main():
    radice = Path(__file__).resolve().parent.parent / "risorse"
    radice.mkdir(exist_ok=True)
    img = disegna()
    img.save(radice / "BiaAmp.png")
    img.save(radice / "BiaAmp.icns", sizes=[(16, 16), (32, 32), (64, 64), (128, 128),
                                            (256, 256), (512, 512), (1024, 1024)])
    print("Icona scritta in", radice)


if __name__ == "__main__":
    main()
