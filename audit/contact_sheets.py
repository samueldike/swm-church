"""Tile screenshots into contact sheets for review: audit/screenshots/_sheets/<theme>-<width>-NN.png"""
import pathlib
import sys

from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).parent / "screenshots"
OUT = ROOT / "_sheets"
OUT.mkdir(exist_ok=True)
COLS = {360: 6, 768: 4, 1280: 3}
CROP = {360: 1500, 768: 1400, 1280: 1100}       # top of each page (px of the screenshot)
TILE_W = {360: 300, 768: 420, 1280: 560}

for theme in sys.argv[1:] or ["light", "dark"]:
    for width in [int(w) for w in (__import__("os").environ.get("WIDTHS") or "360,768,1280").split(",")]:
        files = sorted((ROOT / theme / str(width)).glob("*.jpg"))
        cols, tw = COLS[width], TILE_W[width]
        th = int(CROP[width] * tw / width)
        per = cols * 2
        for n in range(0, len(files), per):
            sheet = Image.new("RGB", (cols * tw, 2 * (th + 22)), "white")
            draw = ImageDraw.Draw(sheet)
            for i, f in enumerate(files[n:n + per]):
                im = Image.open(f).convert("RGB")
                im = im.crop((0, 0, im.width, min(im.height, CROP[width] * im.width // width)))
                im = im.resize((tw, int(im.height * tw / im.width)))
                x, y = (i % cols) * tw, (i // cols) * (th + 22)
                sheet.paste(im, (x, y + 22))
                draw.rectangle((x, y, x + tw, y + 21), fill="black")
                draw.text((x + 4, y + 5), f.stem[:48], fill="yellow")
            sheet.save(OUT / f"{theme}-{width}-{n // per:02d}.jpg", quality=70)
print("sheets in", OUT)
