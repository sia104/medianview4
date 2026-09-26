# MedianView4 Product Specification

Status: Approved by human on 2026-09-26

## Request

Build MedianView4, a simple Python web application where a user uploads an image, a fixed 3x3 median filter is applied, and the original and filtered images are displayed side-by-side.

## Scope

MedianView4 must provide a browser-accessible Python web application for single-image upload, median filtering, and visual comparison of the original and filtered image.

## Functional Requirements

1. The application must expose an upload page reachable from a local web browser after the server starts.
2. The upload page must allow a user to select and submit one image file.
3. The application must accept common browser-uploaded image formats supported by Pillow, including PNG and JPEG.
4. After a successful upload, the application must apply a fixed 3x3 median filter to the uploaded image.
5. The filter size must not be configurable by the user.
6. After filtering, the application must display the original uploaded image and the filtered image side-by-side on the result page.
7. The result page must make clear which image is original and which image is filtered.
8. The application must preserve the uploaded image's dimensions in the filtered output.
9. The application must reject missing, empty, or non-image uploads with a clear user-facing error and without crashing.
10. The application must not require persistent storage of uploaded or filtered images after the request completes.

## Non-Functional Requirements

1. The project must be runnable by a user from a clean checkout using documented setup and launch commands.
2. The application must use deterministic Python tests for the median-filter behavior and upload/error handling.
3. Verification must include starting the real web process and making HTTP requests against it.
4. The project must keep application requirements separate from reusable ADLC governance and runtime files.

## Acceptance Criteria

1. Given the server is running, when a user visits the root URL, then an upload form is displayed.
2. Given a valid PNG image is uploaded, when the request completes, then the response contains both an original image and a filtered image.
3. Given a valid JPEG image is uploaded, when the request completes, then the response contains both an original image and a filtered image.
4. Given a known 3x3 RGB test image with a single noisy center pixel, when the filter is applied, then the center pixel in the filtered result is replaced by the median neighborhood value.
5. Given any valid uploaded image, when filtering completes, then the filtered image has the same width and height as the original.
6. Given no file is submitted, an empty filename is submitted, or invalid image bytes are submitted, then the application responds with a clear error page/message and does not return a Python traceback.
7. Given the project dependencies are installed as documented, then the documented test command passes.
8. Given the documented launch command is run, then an HTTP request to the root URL succeeds.

## Product Test Expectations

1. Unit tests must cover the fixed 3x3 median-filter behavior with deterministic pixel assertions.
2. Integration tests must cover successful PNG upload, successful JPEG upload, and invalid upload handling through the web application.
3. Runtime verification must start the actual server process and perform at least one HTTP request to the running service.

## Optional Recommendations

1. The UI may use lightweight responsive styling so the side-by-side comparison remains readable on narrow screens.
2. The application may avoid writing uploaded files to disk by processing images in memory.
