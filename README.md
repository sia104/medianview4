# MedianView4

MedianView4 is a Python web app that accepts one image upload, applies a fixed 3x3 median filter, and displays the original and filtered images side-by-side.

## Setup

```bash
uv sync --extra dev
```

## Run

```bash
uv run medianview4
```

Open `http://127.0.0.1:5000/` in a browser.

## Verify

Run the deterministic tests:

```bash
uv run pytest
```

Run the real server smoke test:

```bash
uv run python scripts/smoke_http.py
```
