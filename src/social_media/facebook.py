from __future__ import annotations
import os
import requests
import socket
from dotenv import load_dotenv
from pathlib import Path
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
FACEBOOK_PAGE_ID = _required_env("FACEBOOK_PAGE_ID")
FACEBOOK_PAGE_ACCESS_TOKEN = _required_env("FACEBOOK_PAGE_ACCESS_TOKEN")
GRAPH_BASE_URL = "https://graph.facebook.com"


# --------------------------------------------------
# Facebook Connection
# --------------------------------------------------
class FacebookPublishError(RuntimeError):
    """Raised when Meta rejects a Facebook Page API operation."""


def _graph_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Call Facebook Graph API without putting the token in the URL."""
    url = f"{GRAPH_BASE_URL}/{GRAPH_API_VERSION}/{path.lstrip('/')}"
    response = requests.request(
        method,
        url,
        params=params,
        data=data,
        files=files,
        headers={"Authorization": f"Bearer {FACEBOOK_PAGE_ACCESS_TOKEN}"},
        timeout=timeout,
    )

    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_response": response.text}

    if not response.ok or "error" in payload:
        raise FacebookPublishError(
            f"Facebook API error ({response.status_code}): {payload}"
        )

    return payload


def assert_facebook_dns() -> None:
    """Fail early if Windows/VPN DNS cannot resolve the Facebook Graph host."""
    try:
        socket.getaddrinfo("graph.facebook.com", 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RuntimeError(
            "graph.facebook.com cannot be resolved. Check the VPN/DNS settings, "
            "run `ipconfig /flushdns`, and restart the notebook kernel."
        ) from exc


def test_connection() -> dict[str, Any]:
    """Verify DNS, token validity, and that the token belongs to the expected Page."""
    assert_facebook_dns()
    page = _graph_request("GET", "me", params={"fields": "id,name"}, timeout=30)

    returned_id = str(page.get("id", ""))
    if returned_id and returned_id != FACEBOOK_PAGE_ID:
        raise FacebookPublishError(
            "The token belongs to Facebook Page "
            f"{returned_id}, but FACEBOOK_PAGE_ID is {FACEBOOK_PAGE_ID}."
        )

    return page


# --------------------------------------------------
# Posting
# --------------------------------------------------
def facebook_post(
    image_path: str | Path,
    caption: str,
) -> str:
    """Publish one local image and caption to the configured Facebook Page."""
    assert_facebook_dns()

    image = Path(image_path).resolve()
    if not image.is_file():
        raise FileNotFoundError(f"Image not found: {image}")

    with image.open("rb") as image_file:
        result = _graph_request(
            "POST",
            f"{FACEBOOK_PAGE_ID}/photos",
            data={"message": caption, "published": "true"},
            files={
                "source": (
                    image.name,
                    image_file,
                    "application/octet-stream",
                )
            },
            timeout=120,
        )

    post_id = result.get("post_id")
    photo_id = result.get("id")
    published_id = post_id or photo_id

    if not published_id:
        raise FacebookPublishError(f"No published post ID returned: {result}")

    print(f"Published successfully. Facebook post ID: {published_id}")
    return str(published_id)