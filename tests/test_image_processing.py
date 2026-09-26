from PIL import Image

from medianview4.image_processing import apply_median_filter


def test_fixed_3x3_median_filter_replaces_noisy_center_pixel() -> None:
    image = Image.new("RGB", (3, 3), (10, 20, 30))
    image.putpixel((1, 1), (250, 250, 250))

    filtered = apply_median_filter(image)

    assert filtered.size == image.size
    assert filtered.getpixel((1, 1)) == (10, 20, 30)
