"""Run: py scripts/optimize_images.py [DIR] [--apply] — shrink images uploaded before uploads were compressed (UX-02).

New uploads are already resized and saved as WebP (admin.py:_compress_image). Older JPEG/PNG files (one slider was
3.4 MB) are re-saved here at most MAX_WIDTH px wide, in the SAME format and under the SAME name, so nothing that
points at them changes. Each original is first copied to DIR/.originals/<same path>. Only files over MIN_BYTES are
touched, and a file is only replaced when the new one is at least 10% smaller. Without --apply it only reports.

On the server, DIR is the host folder mounted at /app/app/static/images (see deploy/backup/README.md).
"""
import io
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageOps

MAX_WIDTH = 1920
MIN_BYTES = 300 * 1024
JPEG_QUALITY = 80


def shrink(path: Path) -> bytes | None:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        if img.width > MAX_WIDTH:
            img = img.resize((MAX_WIDTH, round(img.height * MAX_WIDTH / img.width)), Image.LANCZOS)
        out = io.BytesIO()
        if path.suffix.lower() in (".jpg", ".jpeg"):
            img.convert("RGB").save(out, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
        else:
            img.save(out, "PNG", optimize=True)
        return out.getvalue()


def main(root: Path, apply: bool) -> None:
    before = after = count = 0
    for path in sorted(root.rglob("*")):
        if ".originals" in path.parts or path.suffix.lower() not in (".jpg", ".jpeg", ".png") or path.stat().st_size < MIN_BYTES:
            continue
        size = path.stat().st_size
        try:
            data = shrink(path)
        except Exception as exc:                           # a damaged file: leave it alone
            print(f"skip {path}: {exc}")
            continue
        if data is None or len(data) > size * 0.9:
            continue
        count += 1
        before += size
        after += len(data)
        print(f"{path.relative_to(root)}: {size // 1024} KB -> {len(data) // 1024} KB")
        if apply:
            backup = root / ".originals" / path.relative_to(root)
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                shutil.copy2(path, backup)
            path.write_bytes(data)
    verb = "Shrunk" if apply else "Would shrink (dry run; add --apply)"
    print(f"{verb} {count} file(s): {before // 1024} KB -> {after // 1024} KB")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--apply"]
    main(Path(args[0] if args else "app/static/images"), "--apply" in sys.argv)
