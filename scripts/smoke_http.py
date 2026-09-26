from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from contextlib import closing

import requests


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    port = free_port()
    env = os.environ.copy()
    env["PORT"] = str(port)
    process = subprocess.Popen(
        [sys.executable, "-m", "medianview4.app"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        url = f"http://127.0.0.1:{port}/"
        deadline = time.time() + 10
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                response = requests.get(url, timeout=1)
                if response.status_code == 200 and 'name="image"' in response.text:
                    print(f"smoke ok: {url}")
                    return 0
            except requests.RequestException as error:
                last_error = error
            time.sleep(0.2)
        print(f"smoke failed: {last_error}", file=sys.stderr)
        return 1
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
