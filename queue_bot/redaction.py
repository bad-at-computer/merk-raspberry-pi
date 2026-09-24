from __future__ import annotations

import io

from PIL import Image, ImageDraw

MIMETYPE = "image/jpeg"


def redact_screenshot(
    png_bytes: bytes,
    reveal_rects: list[tuple[int, int, int, int]] | None = None,
    page_size: tuple[int, int] | None = None,
    mosaic_width: int = 160,
    jpeg_quality: int = 72,
) -> bytes:
    """Pixelate everything except reveal_rects; scales rects from CSS px to image px.

    reveal_rects are (x, y, w, h) in CSS pixels matching page_size (page CSS dims).
    Returns a JPEG that contains none of the original detail outside reveal_rects.
    """
    image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    img_w, img_h = image.size

    if page_size:
        css_w, css_h = page_size
        scale_x = img_w / css_w if css_w else 1.0
        scale_y = img_h / css_h if css_h else 1.0
        scaled = [
            (
                int(round(x * scale_x)),
                int(round(y * scale_y)),
                int(round(w * scale_x)),
                int(round(h * scale_y)),
            )
            for (x, y, w, h) in (reveal_rects or [])
        ]
    else:
        scaled = list(reveal_rects or [])

    canvas = image.resize(
        (max(1, mosaic_width), max(1, int(round(img_h * mosaic_width / img_w)))),
        Image.LANCZOS,
    )
    canvas = canvas.resize((img_w, img_h), Image.NEAREST)

    draw = ImageDraw.Draw(canvas)
    for x, y, w, h in scaled:
        if w <= 0 or h <= 0:
            continue
        box = (max(0, x), max(0, y), min(img_w, x + w), min(img_h, y + h))
        if box[0] >= box[2] or box[1] >= box[3]:
            continue
        region = image.crop(box)
        canvas.paste(region, box)
        draw.rectangle(box, outline=(255, 0, 0), width=3)

    output = io.BytesIO()
    canvas.save(output, format="JPEG", quality=jpeg_quality)
    return output.getvalue()