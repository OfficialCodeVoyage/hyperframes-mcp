"""The free archives on a fake network for the media tools' tests: the archival fixtures (real captures of Wikimedia, loc.gov
and the Archive, and a response shaped like Openverse's) behind one httpx MockTransport, and a file host that serves one
picture. ``tests/server/conftest.py`` makes ``archives`` a fixture."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from hyperframes_mcp.motion import media
from tests.image_helpers import image_bytes

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "archival"
PICTURE = image_bytes("#c8b89a", "JPEG", (1600, 900))


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def archives(monkeypatch) -> list[httpx.Request]:
    """The four archives and a file host on one fake network; returns the requests made, in order."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        host, path = request.url.host, request.url.path
        if host == "commons.wikimedia.org":
            return httpx.Response(200, json=fixture("wikimedia_search.json"))
        if host == "www.loc.gov":
            return httpx.Response(200, json=fixture("loc_search.json"))
        if host == "api.openverse.org":
            return httpx.Response(200, json=fixture("openverse_images_search.json"))
        if host == "archive.org" and path == "/advancedsearch.php":
            return httpx.Response(200, json=fixture("ia_advancedsearch.json"))
        if host == "archive.org" and path.startswith("/metadata/"):
            name = f"ia_metadata_{path.rsplit('/', 1)[1]}.json"
            if (FIXTURES / name).is_file():
                return httpx.Response(200, json=fixture(name))
            return httpx.Response(404)
        if host == "upload.wikimedia.org":
            return httpx.Response(200, content=PICTURE, headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    monkeypatch.setattr(media, "TRANSPORT", httpx.MockTransport(handler))
    return seen
