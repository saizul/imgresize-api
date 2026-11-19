"""Decode, orient, resize and encode images with Pillow, safely.

- Only the configured input formats are ever handed to a decoder.
- The pixel count is checked from the header **before** any pixel is decoded, and Pillow's
  decompression-bomb warning is turned into an error.
- EXIF orientation is applied and then all metadata (EXIF, XMP, comments, text chunks) is
  dropped. The ICC colour profile is kept unless ``IMGRESIZE_KEEP_ICC=false``.
"""

from __future__ import annotations

import io
import re
import warnings
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageOps, features

from imgresize_api.settings import Settings

try:  # Pillow >= 11.2 has native AVIF; older versions can use pillow-avif-plugin.
    if not features.check("avif"):
        import pillow_avif  # type: ignore[import-not-found]  # noqa: F401
except ImportError:  # pragma: no cover - depends on the installed wheels
    pass

FITS = ("cover", "contain", "fill")
OUTPUT_FORMATS = ("jpeg", "png", "webp", "avif")
FORMAT_ALIASES = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp", "avif": "avif", "auto": "auto"}
PIL_INPUT_NAMES = {
    "jpeg": "JPEG",
    "png": "PNG",
    "webp": "WEBP",
    "gif": "GIF",
    "avif": "AVIF",
    "bmp": "BMP",
    "tiff": "TIFF",
}
MIME = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "avif": "image/avif"}
GRAVITY = {
    "center": (0.5, 0.5),
    "north": (0.5, 0.0),
    "south": (0.5, 1.0),
    "east": (1.0, 0.5),
    "west": (0.0, 0.5),
    "northeast": (1.0, 0.0),
    "northwest": (0.0, 0.0),
    "southeast": (1.0, 1.0),
    "southwest": (0.0, 1.0),
}
ROTATING_ORIENTATIONS = (5, 6, 7, 8)


class ImageError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def avif_available() -> bool:
    try:
        return bool(features.check("avif")) or "AVIF" in Image.SAVE
    except Exception:  # pragma: no cover
        return False


@dataclass(frozen=True)
class Params:
    width: int | None = None
    height: int | None = None
    fit: str = "contain"
    format: str | None = None  # None: keep the source format family; "auto": negotiate with Accept
    quality: int | None = None
    dpr: float = 1.0
    enlarge: bool = False
    gravity: str = "center"
    background: tuple[int, int, int] = (255, 255, 255)

    def key(self) -> dict[str, Any]:
        return {
            "w": self.width,
            "h": self.height,
            "fit": self.fit,
            "q": self.quality,
            "dpr": self.dpr,
            "enlarge": self.enlarge,
            "gravity": self.gravity,
            "bg": self.background,
        }


def _parse_int(value: str, name: str, low: int, high: int) -> int:
    if not re.fullmatch(r"\d{1,6}", value.strip()):
        raise ImageError(400, f"{name} must be a whole number")
    number = int(value)
    if not low <= number <= high:
        raise ImageError(400, f"{name} must be between {low} and {high}")
    return number


def parse_params(query: dict[str, str], settings: Settings) -> Params:
    """Validate the w, h, fit, format, q, dpr, enlarge, gravity and bg query parameters."""
    width = height = quality = None
    if query.get("w"):
        width = _parse_int(query["w"], "w", 1, settings.max_dimension)
    if query.get("h"):
        height = _parse_int(query["h"], "h", 1, settings.max_dimension)
    fit = (query.get("fit") or "contain").lower()
    if fit not in FITS:
        raise ImageError(400, f"fit must be one of {', '.join(FITS)}")
    fmt = query.get("format") or query.get("fm") or None
    if fmt is not None:
        fmt = FORMAT_ALIASES.get(fmt.lower())
        if fmt is None:
            raise ImageError(400, f"format must be one of {', '.join([*OUTPUT_FORMATS, 'auto'])}")
        if fmt == "avif" and not (settings.enable_avif and avif_available()):
            raise ImageError(400, "AVIF output is not available on this server")
    if query.get("q"):
        quality = _parse_int(query["q"], "q", 1, 100)
    dpr = 1.0
    if query.get("dpr"):
        try:
            dpr = float(query["dpr"])
        except ValueError as exc:
            raise ImageError(400, "dpr must be a number") from exc
        if not 1 <= dpr <= 4:
            raise ImageError(400, "dpr must be between 1 and 4")
    enlarge = (query.get("enlarge") or "0").lower() in ("1", "true", "yes")
    gravity = (query.get("gravity") or "center").lower()
    if gravity not in GRAVITY:
        raise ImageError(400, f"gravity must be one of {', '.join(GRAVITY)}")
    background = (255, 255, 255)
    if query.get("bg"):
        match = re.fullmatch(r"#?([0-9a-fA-F]{6})", query["bg"].strip())
        if not match:
            raise ImageError(400, "bg must be a hex colour such as ffffff")
        hex_value = match.group(1)
        background = (int(hex_value[0:2], 16), int(hex_value[2:4], 16), int(hex_value[4:6], 16))
    return Params(width, height, fit, fmt, quality, dpr, enlarge, gravity, background)


def negotiate(accept: str, settings: Settings) -> str:
    """Pick ``avif``, ``webp`` or ``legacy`` (JPEG/PNG chosen later by transparency) from an Accept header."""
    accept = accept.lower()
    if "image/avif" in accept and settings.enable_avif and avif_available():
        return "avif"
    if "image/webp" in accept:
        return "webp"
    return "legacy"


@dataclass
class Result:
    content: bytes
    format: str
    width: int
    height: int

    @property
    def content_type(self) -> str:
        return MIME[self.format]


def _target_size(src_w: int, src_h: int, params: Params, max_dim: int) -> tuple[int, int, tuple[int, int, int, int]]:
    """Return output (width, height) and the crop box in source pixels."""
    full = (0, 0, src_w, src_h)
    w = round(params.width * params.dpr) if params.width else None
    h = round(params.height * params.dpr) if params.height else None
    if w is not None:
        w = min(w, max_dim)
    if h is not None:
        h = min(h, max_dim)

    if w is None and h is None:
        scale = min(1.0, max_dim / src_w, max_dim / src_h)
        return max(1, round(src_w * scale)), max(1, round(src_h * scale)), full

    if w is not None and h is not None and params.fit == "fill":
        return w, h, full

    if w is not None and h is not None and params.fit == "cover":
        target_ratio = w / h
        if src_w / src_h > target_ratio:  # source is wider: crop the sides
            crop_w, crop_h = src_h * target_ratio, src_h
        else:
            crop_w, crop_h = src_w, src_w / target_ratio
        cx, cy = GRAVITY[params.gravity]
        left = (src_w - crop_w) * cx
        top = (src_h - crop_h) * cy
        box = (round(left), round(top), round(left + crop_w), round(top + crop_h))
        box_w, box_h = box[2] - box[0], box[3] - box[1]
        if not params.enlarge and (w > box_w or h > box_h):
            scale = min(box_w / w, box_h / h)
            w, h = max(1, round(w * scale)), max(1, round(h * scale))
        return w, h, box

    # contain (or only one side given): keep the aspect ratio inside the box.
    scales = []
    if w is not None:
        scales.append(w / src_w)
    if h is not None:
        scales.append(h / src_h)
    scale = min(scales)
    if not params.enlarge:
        scale = min(scale, 1.0)
    out_w, out_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    if out_w > max_dim or out_h > max_dim:
        shrink = min(max_dim / out_w, max_dim / out_h)
        out_w, out_h = max(1, round(out_w * shrink)), max(1, round(out_h * shrink))
    return out_w, out_h, full


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)


def _normalise_mode(image: Image.Image) -> Image.Image:
    if image.mode in ("RGB", "RGBA", "L"):
        return image
    if image.mode == "LA":
        return image.convert("RGBA")
    if _has_alpha(image):
        return image.convert("RGBA")
    if image.mode in ("I;16", "I;16B", "I;16L", "I"):
        return image.point(lambda v: v / 256).convert("L") if image.mode != "I" else image.convert("L")
    return image.convert("RGB")


def open_image(data: bytes, settings: Settings) -> Image.Image:
    formats = [PIL_INPUT_NAMES[f] for f in settings.input_formats if f in PIL_INPUT_NAMES]
    Image.MAX_IMAGE_PIXELS = settings.max_pixels
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            image = Image.open(io.BytesIO(data), formats=formats)
        except Image.DecompressionBombError as exc:
            raise ImageError(413, "image has too many pixels") from exc
        except Image.DecompressionBombWarning as exc:
            raise ImageError(413, "image has too many pixels") from exc
        except Exception as exc:  # UnidentifiedImageError and decoder errors
            raise ImageError(415, "unsupported or corrupt image") from exc
    width, height = image.size
    if width < 1 or height < 1 or width * height > settings.max_pixels:
        image.close()
        raise ImageError(413, f"image has {width}x{height} pixels, the limit is {settings.max_pixels}")
    return image


def process(data: bytes, params: Params, settings: Settings, negotiated: str | None = None) -> Result:
    image = open_image(data, settings)
    source_format = (image.format or "").lower()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            return _process(image, source_format, params, settings, negotiated)
    except ImageError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageError(413, "image has too many pixels") from exc
    except (OSError, ValueError, SyntaxError) as exc:
        raise ImageError(422, "could not decode the image") from exc
    finally:
        image.close()


def _process(
    image: Image.Image, source_format: str, params: Params, settings: Settings, negotiated: str | None
) -> Result:
    if getattr(image, "is_animated", False):
        image.seek(0)
    icc_profile = image.info.get("icc_profile") if settings.keep_icc else None
    orientation = image.getexif().get(0x0112, 1)

    src_w, src_h = image.size
    if orientation in ROTATING_ORIENTATIONS:
        src_w, src_h = src_h, src_w
    out_w, out_h, box = _target_size(src_w, src_h, params, settings.max_dimension)

    if source_format == "jpeg":
        # Shrink-on-load: let libjpeg decode at 1/2, 1/4 or 1/8 scale while keeping at least
        # twice the pixels the output needs from the crop box.
        box_w, box_h = box[2] - box[0], box[3] - box[1]
        need_w = min(src_w, 2 * out_w * src_w // max(1, box_w))
        need_h = min(src_h, 2 * out_h * src_h // max(1, box_h))
        draft_size = (need_h, need_w) if orientation in ROTATING_ORIENTATIONS else (need_w, need_h)
        image.draft(None, (max(1, draft_size[0]), max(1, draft_size[1])))

    image.load()
    oriented = ImageOps.exif_transpose(image) or image
    oriented = _normalise_mode(oriented)
    # Map the crop box onto the (possibly draft-reduced) image.
    if box != (0, 0, src_w, src_h):
        sx, sy = oriented.width / src_w, oriented.height / src_h
        box = (round(box[0] * sx), round(box[1] * sy), round(box[2] * sx), round(box[3] * sy))
    else:
        box = (0, 0, oriented.width, oriented.height)
    if (out_w, out_h) != (oriented.width, oriented.height) or box != (0, 0, oriented.width, oriented.height):
        resized = oriented.resize((out_w, out_h), Image.Resampling.LANCZOS, box=box, reducing_gap=3.0)
    else:
        resized = oriented.copy()

    fmt = _output_format(params.format, negotiated, source_format, _has_alpha(resized), settings)
    if fmt == "jpeg" and _has_alpha(resized):
        background = Image.new("RGB", resized.size, params.background)
        background.paste(resized, mask=resized.getchannel("A"))
        resized = background
    elif fmt == "jpeg" and resized.mode not in ("RGB", "L"):
        resized = resized.convert("RGB")

    resized.info = {}  # drop EXIF, XMP, comments and text chunks
    quality = params.quality or settings.default_quality
    options: dict[str, Any] = {}
    if icc_profile:
        options["icc_profile"] = icc_profile
    if fmt == "jpeg":
        options.update(quality=quality, optimize=True, progressive=True)
    elif fmt == "png":
        options.update(optimize=False, compress_level=6)
    elif fmt == "webp":
        options.update(quality=quality, method=4)
    elif fmt == "avif":
        options.update(quality=quality, speed=6)
    buffer = io.BytesIO()
    resized.save(buffer, format=fmt.upper(), **options)
    return Result(buffer.getvalue(), fmt, resized.width, resized.height)


def _output_format(requested: str | None, negotiated: str | None, source: str, alpha: bool, settings: Settings) -> str:
    if requested == "auto":
        choice = negotiated or "legacy"
        if choice in ("avif", "webp"):
            return choice
        return "png" if alpha else "jpeg"
    if requested:
        return requested
    if source in ("jpeg", "png", "webp"):
        return source
    if source == "avif" and settings.enable_avif and avif_available():
        return "avif"
    return "png" if alpha or source in ("gif", "bmp") else "jpeg"
