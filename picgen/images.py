"""Picture helpers: sizes, grids, clean PNGs, JPEGs that fit Discord's upload limit."""
import io
import math

from PIL import Image

ASPECTS = {"1:1": 1.0, "16:9": 16 / 9, "9:16": 9 / 16, "4:3": 4 / 3, "3:4": 3 / 4, "3:2": 3 / 2, "2:3": 2 / 3, "21:9": 21 / 9}


def parse_aspect(text):
    """'16:9' -> 1.777 (falls back to 1, clamped to 1:4..4:1)."""
    try:
        a, b = str(text).split(":")
        return min(4.0, max(0.25, float(a) / float(b)))
    except (ValueError, ZeroDivisionError):
        return 1.0


def size_for(aspect, base, multiple=64):
    """Width/height with the given aspect ratio and about base*base pixels, rounded to multiples of `multiple`."""
    area = base * base
    w = math.sqrt(area * aspect)
    h = w / aspect
    return max(256, round(w / multiple) * multiple), max(256, round(h / multiple) * multiple)


def image_size(data):
    """(width, height) of encoded image bytes, or (0, 0) if unreadable."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.size
    except Exception:
        return 0, 0


def clean_png(data):
    """Re-encodes as PNG without metadata (ComfyUI puts its whole workflow into PNG text chunks)."""
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        if img.mode not in ("RGB", "RGBA", "L"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        out = io.BytesIO()
        img.save(out, "PNG", optimize=False, compress_level=6)
        return out.getvalue()


def to_jpeg(data, max_bytes):
    """JPEG, lowering quality until it fits max_bytes."""
    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        result = b""
        for quality in (93, 88, 82, 75, 65):
            out = io.BytesIO()
            img.save(out, "JPEG", quality=quality, optimize=True)
            result = out.getvalue()
            if len(result) <= max_bytes:
                break
        return result


def shrink_to(data, max_side):
    """PNG with the long side at most max_side (unchanged bytes if already small enough)."""
    w, h = image_size(data)
    if max(w, h) <= max_side:
        return data
    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        img.thumbnail((max_side, max_side), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "PNG")
        return out.getvalue()


def make_grid(images, cell=768, gap=8):
    """2x2 (or 1xN) preview grid as JPEG; each picture shrunk to fit cell x cell."""
    loaded = []
    for data in images:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((cell, cell), Image.LANCZOS)
        loaded.append(img)
    cols = 1 if len(loaded) == 1 else 2
    rows = math.ceil(len(loaded) / cols)
    cw = max(i.width for i in loaded)
    ch = max(i.height for i in loaded)
    grid = Image.new("RGB", (cols * cw + (cols - 1) * gap, rows * ch + (rows - 1) * gap), (24, 24, 27))
    for n, img in enumerate(loaded):
        x = (n % cols) * (cw + gap) + (cw - img.width) // 2
        y = (n // cols) * (ch + gap) + (ch - img.height) // 2
        grid.paste(img, (x, y))
    out = io.BytesIO()
    grid.save(out, "JPEG", quality=90)
    return out.getvalue()


def upscale_target(width, height, factor, max_side):
    """Output size for an upscale: factor times bigger, long side at most max_side, even numbers."""
    scale = min(factor, max_side / max(width, height))
    return int(round(width * scale / 2) * 2), int(round(height * scale / 2) * 2)
