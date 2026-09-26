from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageFilter, UnidentifiedImageError


class InvalidImageError(ValueError):
    """Raised when uploaded bytes cannot be decoded as an image."""


def load_image(content: bytes) -> Image.Image:
    if not content:
        raise InvalidImageError("Upload an image file.")
    try:
        with Image.open(BytesIO(content)) as image:
            image.load()
            return image.convert("RGB")
    except (UnidentifiedImageError, OSError) as error:
        raise InvalidImageError("Upload a valid image file.") from error


def apply_median_filter(image: Image.Image) -> Image.Image:
    return image.filter(ImageFilter.MedianFilter(size=3))


def encode_png_data_url(image: Image.Image) -> str:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + __import__("base64").b64encode(
        buffer.getvalue()
    ).decode("ascii")
