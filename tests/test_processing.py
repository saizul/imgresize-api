import io
from dataclasses import replace

import pytest
from PIL import Image, ImageCms

from conftest import make_image
from imgresize_api.processing import ImageError, Params, avif_available, negotiate, parse_params, process
from imgresize_api.settings import Settings

SETTINGS = Settings(allow_unsigned=True, cache_dir="")
needs_avif = pytest.mark.skipif(not avif_available(), reason="Pillow built without AVIF")


def run(data: bytes, settings: Settings = SETTINGS, negotiated: str | None = None, **params):
    query = {k: str(v) for k, v in params.items()}
    result = process(data, parse_params(query, settings), settings, negotiated)
    image = Image.open(io.BytesIO(result.content))
    return result, image


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ({"w": "0"}, "between 1"),
        ({"w": "abc"}, "whole number"),
        ({"h": "5000"}, "between 1 and 4096"),
        ({"w": "-5"}, "whole number"),
        ({"fit": "stretch"}, "fit must be"),
        ({"format": "tiff"}, "format must be"),
        ({"q": "101"}, "between 1 and 100"),
        ({"dpr": "8"}, "dpr"),
        ({"dpr": "x"}, "dpr"),
        ({"gravity": "up"}, "gravity"),
        ({"bg": "red"}, "hex"),
    ],
)
def test_parse_params_rejects(query, message):
    with pytest.raises(ImageError, match=message) as info:
        parse_params(query, SETTINGS)
    assert info.value.status == 400


def test_parse_params_defaults_and_aliases():
    params = parse_params({"w": "100", "fm": "jpg", "bg": "#00ff00", "enlarge": "1"}, SETTINGS)
    assert params == Params(width=100, format="jpeg", background=(0, 255, 0), enlarge=True)


def test_avif_can_be_disabled():
    with pytest.raises(ImageError, match="AVIF"):
        parse_params({"format": "avif"}, replace(SETTINGS, enable_avif=False))


def test_contain_keeps_aspect_ratio():
    result, image = run(make_image((400, 300)), w=200, h=200)
    assert image.size == (200, 150)
    assert (result.width, result.height) == (200, 150)
    assert result.format == "jpeg"


def test_single_dimension():
    assert run(make_image((400, 300)), w=100)[1].size == (100, 75)
    assert run(make_image((400, 300)), h=30)[1].size == (40, 30)


def test_cover_crops_to_exact_size():
    _, image = run(make_image((400, 300), fmt="PNG"), w=100, h=100, fit="cover")
    assert image.size == (100, 100)


def test_cover_gravity_selects_region():
    # The blue marker covers the top-left quarter of a 400x300 image.
    _, west = run(make_image((400, 300), fmt="PNG"), w=50, h=300, fit="cover", gravity="west")
    _, east = run(make_image((400, 300), fmt="PNG"), w=50, h=300, fit="cover", gravity="east")
    assert west.convert("RGB").getpixel((5, 5)) == (0, 0, 255)
    assert east.convert("RGB").getpixel((5, 5)) == (200, 40, 40)


def test_fill_stretches():
    assert run(make_image((400, 300)), w=50, h=200, fit="fill")[1].size == (50, 200)


def test_no_upscaling_unless_enlarge():
    assert run(make_image((100, 50)), w=400)[1].size == (100, 50)
    assert run(make_image((100, 50)), w=400, enlarge=1)[1].size == (400, 200)
    # cover without enlarge keeps the requested aspect ratio at the largest size available
    assert run(make_image((100, 50)), w=400, h=400, fit="cover")[1].size == (50, 50)


def test_dpr_multiplies_and_is_capped():
    assert run(make_image((1000, 500)), w=200, dpr=2)[1].size == (400, 200)
    small = replace(SETTINGS, max_dimension=300)
    assert run(make_image((1000, 500)), small, w=200, dpr=3)[1].size == (300, 150)


def test_no_size_keeps_dimensions_but_caps_to_max():
    assert run(make_image((120, 80)))[1].size == (120, 80)
    assert run(make_image((1000, 500)), replace(SETTINGS, max_dimension=200))[1].size == (200, 100)


@pytest.mark.parametrize(("fmt", "pil"), [("webp", "WEBP"), ("png", "PNG"), ("jpeg", "JPEG")])
def test_output_formats(fmt, pil):
    result, image = run(make_image((64, 64)), format=fmt, q=70)
    assert image.format == pil
    assert result.content_type == f"image/{fmt}"


@needs_avif
def test_avif_output():
    result, image = run(make_image((64, 64)), format="avif", q=50)
    assert image.format == "AVIF"
    assert result.content_type == "image/avif"


def test_quality_changes_size():
    data = make_image((300, 300))
    noisy = Image.effect_noise((300, 300), 60).convert("RGB")
    buffer = io.BytesIO()
    noisy.save(buffer, "PNG")
    data = buffer.getvalue()
    low, _ = run(data, format="jpeg", q=20)
    high, _ = run(data, format="jpeg", q=95)
    assert len(low.content) < len(high.content)


def test_default_format_follows_source():
    assert run(make_image(fmt="PNG"))[0].format == "png"
    assert run(make_image(fmt="WEBP"))[0].format == "webp"
    assert run(make_image(fmt="GIF", mode="P", color=3))[0].format == "png"
    assert run(make_image(fmt="BMP"))[0].format == "png"


def test_auto_format_negotiation():
    assert negotiate("image/avif,image/webp,*/*", SETTINGS) == ("avif" if avif_available() else "webp")
    assert negotiate("image/webp,*/*", SETTINGS) == "webp"
    assert negotiate("*/*", SETTINGS) == "legacy"
    assert negotiate("image/avif", replace(SETTINGS, enable_avif=False)) == "legacy"
    assert run(make_image(), negotiated="webp", format="auto")[0].format == "webp"
    assert run(make_image(), negotiated="legacy", format="auto")[0].format == "jpeg"
    assert (
        run(make_image(mode="RGBA", fmt="PNG", color=(1, 2, 3, 0)), negotiated="legacy", format="auto")[0].format
        == "png"
    )


def test_alpha_is_flattened_on_background_for_jpeg():
    data = make_image((40, 40), fmt="PNG", mode="RGBA", color=(255, 0, 0, 0))
    _, image = run(data, format="jpeg", bg="00ff00")
    r, g, b = image.getpixel((35, 35))
    assert g > 240 and r < 15 and b < 15


def test_alpha_is_kept_for_png_and_webp():
    data = make_image((40, 40), fmt="PNG", mode="RGBA", color=(255, 0, 0, 0))
    assert run(data, format="png")[1].mode == "RGBA"
    assert run(data, format="webp")[1].mode == "RGBA"


def test_palette_with_transparency_and_greyscale_and_cmyk():
    palette = Image.new("P", (20, 20), 0)
    palette.info["transparency"] = 0
    buffer = io.BytesIO()
    palette.save(buffer, "PNG", transparency=0)
    assert run(buffer.getvalue(), format="png")[1].mode == "RGBA"
    assert run(make_image((20, 20), mode="L", color=128))[1].mode == "L"
    cmyk = make_image((20, 20), mode="CMYK", color=(0, 255, 255, 0))
    _, image = run(cmyk, format="jpeg")
    assert image.mode == "RGB"


def test_16_bit_png():
    image = Image.new("I;16", (10, 10), 40000)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    _, out = run(buffer.getvalue(), format="png")
    assert out.mode == "L"
    assert out.getpixel((0, 0)) == 156


def _jpeg_with_exif(orientation: int) -> bytes:
    image = Image.new("RGB", (400, 200), (200, 40, 40))
    for x in range(100):
        for y in range(50):
            image.putpixel((x, y), (0, 0, 255))
    exif = Image.Exif()
    exif[0x0112] = orientation
    exif[0x010F] = "Camera Maker"
    exif[0x8825] = {2: (51.0, 30.0, 0.0)}  # GPS latitude, must be stripped
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif, comment=b"secret comment", quality=95)
    return buffer.getvalue()


def test_exif_orientation_is_applied():
    # Orientation 6 means "rotate 90 degrees clockwise to display".
    _, image = run(_jpeg_with_exif(6), format="png")
    assert image.size == (200, 400)
    assert image.getpixel((190, 10))[2] > 200  # marker moved to the top-right corner
    _, resized = run(_jpeg_with_exif(6), w=100, format="png")
    assert resized.size == (100, 200)


def test_exif_orientation_with_cover_and_draft():
    big = Image.new("RGB", (4000, 2000), (200, 40, 40))
    exif = Image.Exif()
    exif[0x0112] = 8
    buffer = io.BytesIO()
    big.save(buffer, "JPEG", exif=exif)
    _, image = run(buffer.getvalue(), w=100, h=100, fit="cover")
    assert image.size == (100, 100)


@pytest.mark.parametrize("fmt", ["jpeg", "png", "webp"])
def test_metadata_is_stripped(fmt):
    result, image = run(_jpeg_with_exif(1), format=fmt)
    assert not image.getexif()
    assert "comment" not in image.info and "exif" not in image.info and "xmp" not in image.info
    assert b"Camera Maker" not in result.content
    assert b"secret comment" not in result.content


def test_icc_profile_kept_or_dropped():
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    image = Image.new("RGB", (20, 20), (10, 20, 30))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", icc_profile=profile)
    _, kept = run(buffer.getvalue(), format="webp")
    assert kept.info.get("icc_profile") == profile
    _, dropped = run(buffer.getvalue(), replace(SETTINGS, keep_icc=False), format="webp")
    assert not dropped.info.get("icc_profile")


def test_animated_gif_uses_first_frame():
    frames = [Image.new("RGB", (30, 30), color) for color in ((255, 0, 0), (0, 255, 0))]
    buffer = io.BytesIO()
    frames[0].save(buffer, "GIF", save_all=True, append_images=frames[1:], duration=100)
    _, image = run(buffer.getvalue(), format="png")
    assert image.convert("RGB").getpixel((5, 5))[0] > 200


def test_pixel_limit_checked_before_decoding():
    settings = replace(SETTINGS, max_pixels=10_000)
    with pytest.raises(ImageError, match="pixels") as info:
        run(make_image((200, 200)), settings)
    assert info.value.status == 413


def test_decompression_bomb_png_is_rejected_quickly():
    # 1-bit PNG: tiny file, 400 million pixels once decoded.
    bomb = Image.new("1", (20000, 20000))
    buffer = io.BytesIO()
    bomb.save(buffer, "PNG")
    data = buffer.getvalue()
    assert len(data) < 200_000
    with pytest.raises(ImageError) as info:
        run(data)
    assert info.value.status == 413


def test_forged_header_dimensions(monkeypatch):
    # A PNG whose IHDR claims huge dimensions but has no data must not be decoded.
    data = bytearray(make_image((10, 10), fmt="PNG"))
    import struct
    import zlib

    ihdr = struct.pack(">IIBBBBB", 60000, 60000, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    data[8:33] = struct.pack(">I", 13) + chunk + struct.pack(">I", zlib.crc32(chunk))
    with pytest.raises(ImageError) as info:
        run(bytes(data))
    assert info.value.status == 413


def test_disallowed_and_corrupt_input():
    tiff = make_image((10, 10), fmt="TIFF")
    with pytest.raises(ImageError) as info:
        run(tiff)
    assert info.value.status == 415
    assert run(tiff, replace(SETTINGS, input_formats=("tiff",)))[1].size == (10, 10)
    with pytest.raises(ImageError) as info:
        run(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    assert info.value.status == 415
    with pytest.raises(ImageError) as info:
        run(make_image((50, 50))[:200])
    assert info.value.status in (415, 422)


def test_input_format_restriction():
    with pytest.raises(ImageError):
        run(make_image(fmt="PNG"), replace(SETTINGS, input_formats=("jpeg",)))
