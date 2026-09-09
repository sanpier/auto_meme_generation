from __future__ import annotations
import os
import queue
import re
import requests
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from dotenv import load_dotenv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from PIL import Image, ImageOps
from typing import Any


# --------------------------------------------------
# Env + Variables
# --------------------------------------------------
load_dotenv()

def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value

GRAPH_API_VERSION = _required_env("META_GRAPH_API_VERSION")
INSTAGRAM_USER_ID = _required_env("INSTAGRAM_USER_ID")
INSTAGRAM_ACCESS_TOKEN = _required_env("INSTAGRAM_ACCESS_TOKEN")

# This host is required for tokens issued by "Instagram API with Instagram Login".
GRAPH_BASE_URL = "https://graph.instagram.com"

LOCAL_HOST = os.getenv("LOCAL_HOST", "127.0.0.1")
LOCAL_PORT = int(os.getenv("LOCAL_PORT", "8765"))
CLOUDFLARED_BIN = os.getenv("CLOUDFLARED_BIN", "cloudflared")


# --------------------------------------------------
# Instagram Connection
# --------------------------------------------------
class InstagramPublishError(RuntimeError):
    """Raised when Meta rejects an Instagram API operation."""

def _graph_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Call Instagram Graph API without putting the token in the URL."""
    url = f"{GRAPH_BASE_URL}/{GRAPH_API_VERSION}/{path.lstrip('/')}"
    response = requests.request(
        method,
        url,
        params=params,
        data=data,
        headers={"Authorization": f"Bearer {INSTAGRAM_ACCESS_TOKEN}"},
        timeout=timeout,
    )

    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_response": response.text}

    if not response.ok or "error" in payload:
        raise InstagramPublishError(
            f"Instagram API error ({response.status_code}): {payload}"
        )

    return payload

def assert_instagram_dns() -> None:
    """Fail early if Proton/Windows DNS cannot resolve the Instagram host."""
    try:
        socket.getaddrinfo("graph.instagram.com", 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RuntimeError(
            "graph.instagram.com cannot be resolved. In Proton VPN for Windows, "
            "enable Custom DNS and add 1.1.1.1 and 1.0.0.1, reconnect, run "
            '`ipconfig /flushdns`, and restart the notebook kernel.'
        ) from exc

def test_connection() -> dict[str, Any]:
    """Verify DNS, token validity, and that the token belongs to the expected IG user."""
    assert_instagram_dns()
    profile = _graph_request("GET", "me", params={"fields": "id,username"}, timeout=30)
    returned_id = str(profile.get("id", ""))
    if returned_id and returned_id != INSTAGRAM_USER_ID:
        raise InstagramPublishError(
            "The token belongs to Instagram user "
            f"{returned_id}, but INSTAGRAM_USER_ID is {INSTAGRAM_USER_ID}."
        )

    return profile


# --------------------------------------------------
# Image Preparation
# --------------------------------------------------
def pad_to_aspect_ratio(
    image: Image.Image,
    target_width: int,
    target_height: int,
    fill: str = "black",
) -> Image.Image:
    """ Pads an image to the requested aspect ratio without cropping
    """
    target_ratio = target_width / target_height

    w, h = image.size
    current_ratio = w / h

    # Already correct
    if abs(current_ratio - target_ratio) < 1e-6:
        return image

    if current_ratio > target_ratio:
        # Too wide -> increase height
        new_height = round(w / target_ratio)
        pad = new_height - h

        return ImageOps.expand(
            image,
            border=(0, pad // 2, 0, pad - pad // 2),
            fill=fill,
        )
    else:
        # Too tall -> increase width
        new_width = round(h * target_ratio)
        pad = new_width - w

        return ImageOps.expand(
            image,
            border=(pad // 2, 0, pad - pad // 2, 0),
            fill=fill,
        )

def prepare_instagram_jpeg(
    source_path: str | Path,
    destination_path: str | Path,
    aspect_ratio: str = "1:1",
) -> Path:
    """Convert PNG/JPEG/etc. to a clean RGB JPEG without copying metadata."""
    source = Path(source_path).resolve()
    destination = Path(destination_path).resolve()

    if not source.is_file():
        raise FileNotFoundError(f"Image not found: {source}")
    
    destination.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original)

        if image.mode in {"RGBA", "LA"} or (
            image.mode == "P" and "transparency" in image.info
        ):
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, "black")
            background.paste(rgba, mask=rgba.getchannel("A"))
            image = background
        else:
            image = image.convert("RGB")
        if aspect_ratio != "1:1":
            width, height = map(float, aspect_ratio.split(":"))
            image = pad_to_aspect_ratio(
                image,
                target_width=width,
                target_height=height
            )

        image.save(
            destination,
            format="JPEG",
            quality=95,
            optimize=True,
            progressive=True,
        )

    return destination


# --------------------------------------------------
# Local Server + Cloudflare Tunnel
# --------------------------------------------------
def _make_file_handler(image_path: Path, route_token: str):
    """Create an HTTP handler that exposes exactly one unpredictable image URL."""
    route = f"/media/{route_token}.jpg"

    class SingleFileHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def _serve(self, include_body: bool) -> None:
            requested_path = self.path.split("?", 1)[0]
            if requested_path != route or not image_path.is_file():
                self.send_error(404)
                return

            file_size = image_path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(file_size))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()

            if include_body:
                with image_path.open("rb") as image_file:
                    while chunk := image_file.read(64 * 1024):
                        self.wfile.write(chunk)

        def do_GET(self) -> None:
            self._serve(include_body=True)

        def do_HEAD(self) -> None:
            self._serve(include_body=False)

    return SingleFileHandler, route

def _start_local_server(
    image_path: Path,
    route_token: str,
) -> tuple[ThreadingHTTPServer, threading.Thread, int, str]:
    handler, route = _make_file_handler(image_path, route_token)
    server = ThreadingHTTPServer((LOCAL_HOST, LOCAL_PORT), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    actual_port = int(server.server_address[1])
    return server, thread, actual_port, route

def _start_cloudflare_tunnel(local_port: int) -> tuple[subprocess.Popen[str], str]:
    """Start a temporary Cloudflare Quick Tunnel and return its public base URL."""
    process = subprocess.Popen(
        [
            CLOUDFLARED_BIN,
            "tunnel",
            "--url",
            f"http://{LOCAL_HOST}:{local_port}",
            "--no-autoupdate",
            # HTTP/2 avoids relying on UDP/QUIC through restrictive VPN paths.
            "--protocol",
            "http2",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    if process.stdout is None:
        process.kill()
        raise RuntimeError("Could not read cloudflared output.")

    lines: queue.Queue[str] = queue.Queue()

    def _reader() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line)

    threading.Thread(target=_reader, daemon=True).start()

    pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.I)
    deadline = time.monotonic() + 45
    recent_lines: list[str] = []

    while time.monotonic() < deadline:
        if process.poll() is not None:
            break

        try:
            line = lines.get(timeout=0.5)
        except queue.Empty:
            continue

        recent_lines.append(line.rstrip())
        recent_lines = recent_lines[-20:]
        match = pattern.search(line)
        if match:
            return process, match.group(0)

    process.terminate()
    details = "\n".join(recent_lines)
    raise RuntimeError(
        "Cloudflare Quick Tunnel did not produce a public URL.\n" + details
    )

def _create_media_container(image_url: str, caption: str) -> str:
    result = _graph_request(
        "POST",
        f"{INSTAGRAM_USER_ID}/media",
        data={"image_url": image_url, "caption": caption},
    )
    container_id = result.get("id")
    if not container_id:
        raise InstagramPublishError(f"No container ID returned: {result}")
    return str(container_id)

def _wait_for_container(
    container_id: str,
    timeout_seconds: int = 180,
    poll_interval_seconds: int = 3,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status: dict[str, Any] = {}

    while time.monotonic() < deadline:
        last_status = _graph_request(
            "GET",
            container_id,
            params={"fields": "status_code,status"},
            timeout=30,
        )
        status_code = last_status.get("status_code")

        if status_code == "FINISHED":
            return last_status

        if status_code in {"ERROR", "EXPIRED"}:
            raise InstagramPublishError(
                f"Instagram rejected the media container: {last_status}"
            )

        time.sleep(poll_interval_seconds)

    raise TimeoutError(
        f"Instagram did not finish processing the image: {last_status}"
    )

def _publish_media_container(container_id: str) -> str:
    result = _graph_request(
        "POST",
        f"{INSTAGRAM_USER_ID}/media_publish",
        data={"creation_id": container_id},
    )
    media_id = result.get("id")
    if not media_id:
        raise InstagramPublishError(f"No published media ID returned: {result}")
    return str(media_id)

def _stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


# --------------------------------------------------
# Instagram Post
# --------------------------------------------------
def instagram_post(
    image_path: str | Path,
    caption: str,
    aspect_ratio: str = "1:1.075",
) -> str:
    """Publish one local image and caption, then tear down the temporary endpoint."""
    assert_instagram_dns()

    server: ThreadingHTTPServer | None = None
    server_thread: threading.Thread | None = None
    tunnel_process: subprocess.Popen[str] | None = None

    with tempfile.TemporaryDirectory(prefix="instagram_publish_") as temp_dir:
        prepared_image = prepare_instagram_jpeg(
            image_path,
            Path(temp_dir) / "post.jpg",
            aspect_ratio=aspect_ratio,
        )
        route_token = secrets.token_urlsafe(32)

        try:
            server, server_thread, local_port, route = _start_local_server(
                prepared_image,
                route_token,
            )
            tunnel_process, public_base_url = _start_cloudflare_tunnel(local_port)
            image_url = f"{public_base_url}{route}"

            print("Public image URL:", image_url)
            last_error: Exception | None = None

            for attempt in range(3):
                time.sleep(10)
                try:
                    container_id = _create_media_container(image_url, caption)
                    break
                except InstagramPublishError as exc:
                    last_error = exc
                    print(
                        f"Instagram could not fetch the image yet "
                        f"(attempt {attempt + 1}/3). Retrying..."
                    )
            else:
                raise InstagramPublishError(
                    "Instagram could not fetch the temporary Cloudflare image URL "
                    f"after 3 attempts. URL={image_url}. "
                    f"Original error: {last_error}"
                ) from last_error

            print(f"Instagram container created: {container_id}")

            _wait_for_container(container_id)
            print("Instagram finished processing the image.")

            media_id = _publish_media_container(container_id)
            print(f"Published successfully. Instagram media ID: {media_id}")
            return media_id

        finally:
            _stop_process(tunnel_process)

            if server is not None:
                server.shutdown()
                server.server_close()

            if server_thread is not None:
                server_thread.join(timeout=5)