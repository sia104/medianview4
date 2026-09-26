from __future__ import annotations

import os

from flask import Flask, render_template, request

from medianview4.image_processing import (
    InvalidImageError,
    apply_median_filter,
    encode_png_data_url,
    load_image,
)


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.post("/filter")
    def filter_image() -> tuple[str, int] | str:
        upload = request.files.get("image")
        if upload is None or upload.filename == "":
            return render_template("index.html", error="Choose an image file."), 400

        try:
            original = load_image(upload.read())
        except InvalidImageError as error:
            return render_template("index.html", error=str(error)), 400

        filtered = apply_median_filter(original)
        return render_template(
            "result.html",
            original_image=encode_png_data_url(original),
            filtered_image=encode_png_data_url(filtered),
        )

    return app


def main() -> None:
    port = int(os.environ.get("PORT", "5000"))
    create_app().run(host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
