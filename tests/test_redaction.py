import io

from PIL import Image

from queue_bot.redaction import MIMETYPE, redact_screenshot


def _png(size=(200, 100), left=(255, 0, 0), right=(0, 0, 255)) -> bytes:
    img = Image.new("RGB", size)
    img.paste(left, (0, 0, size[0] // 2, size[1]))
    img.paste(right, (size[0] // 2, 0, size[0], size[1]))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_redacts_whole_frame():
    out = redact_screenshot(_png())
    assert out[:4] == b"\xff\xd8\xff\xe0"
    img = Image.open(io.BytesIO(out))
    assert img.size == (200, 100)


def test_mosaic_removes_original_detail():
    out = redact_screenshot(_png(), mosaic_width=1)
    img = Image.open(io.BytesIO(out)).convert("RGB")
    colors = set(img.getdata())
    assert len(colors) <= 2
    original_colors = set(Image.open(io.BytesIO(_png())).convert("RGB").getdata())
    assert (255, 0, 0) not in colors or (0, 0, 255) not in colors


def test_reveal_keeps_region():
    out = redact_screenshot(
        _png(), reveal_rects=[(0, 0, 100, 100)], page_size=(200, 100), mosaic_width=1
    )
    img = Image.open(io.BytesIO(out)).convert("RGB")
    left_px = img.getpixel((50, 50))
    assert abs(left_px[0] - 255) < 20 and abs(left_px[1]) < 20


def test_mimetype():
    assert MIMETYPE == "image/jpeg"