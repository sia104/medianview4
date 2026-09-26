from __future__ import annotations

import base64
import re
from io import BytesIO

import pytest
from PIL import Image

from medianview4.app import create_app


@pytest.fixture()
def client():
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def image_bytes(fmt: str) -> BytesIO:
    buffer = BytesIO()
    Image.new("RGB", (4, 4), (40, 80, 120)).save(buffer, format=fmt)
    buffer.seek(0)
    return buffer


def response_image_sizes(response_data: bytes) -> list[tuple[int, int]]:
    urls = re.findall(rb"data:image/png;base64,([A-Za-z0-9+/=]+)", response_data)
    sizes: list[tuple[int, int]] = []
    for encoded in urls:
        with Image.open(BytesIO(base64.b64decode(encoded))) as image:
            sizes.append(image.size)
    return sizes


def test_root_displays_upload_form(client) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert b'name="image"' in response.data
    assert b"Apply Median Filter" in response.data


@pytest.mark.parametrize(("fmt", "filename"), [("PNG", "test.png"), ("JPEG", "test.jpg")])
def test_valid_image_upload_displays_original_and_filtered_images(
    client, fmt: str, filename: str
) -> None:
    response = client.post(
        "/filter",
        data={"image": (image_bytes(fmt), filename)},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert b"Original" in response.data
    assert b"Filtered" in response.data
    assert response.data.count(b"data:image/png;base64,") == 2


def test_valid_image_upload_preserves_dimensions(client) -> None:
    response = client.post(
        "/filter",
        data={"image": (image_bytes("PNG"), "test.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response_image_sizes(response.data) == [(4, 4), (4, 4)]


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"image": (BytesIO(b""), "")},
        {"image": (BytesIO(b"not an image"), "bad.txt")},
    ],
)
def test_invalid_uploads_return_clear_error_without_traceback(client, data) -> None:
    response = client.post("/filter", data=data, content_type="multipart/form-data")

    assert response.status_code == 400
    assert b"Traceback" not in response.data
    assert b"Choose an image file." in response.data or b"Upload a valid image file." in response.data
