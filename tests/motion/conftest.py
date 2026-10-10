"""Every motion test keeps the central spend log in its own temporary folder, never the real home folder's."""

import pytest
from PIL import Image

from hyperframes_mcp.motion import generate, manifest, spend
from hyperframes_mcp.motion.project_edit import AI_CREDIT
from tests.motion.profile_helpers import profiles_root  # noqa: F401 -- fixture


@pytest.fixture(autouse=True)
def central_spend_log(tmp_path, monkeypatch):
    path = tmp_path / "central" / spend.CENTRAL_LOG_NAME
    monkeypatch.setattr(spend, "CENTRAL_LOG", path)
    return path


@pytest.fixture
def project(tmp_path):
    """A composed project of profile "history" for pipeline topic 1, with a picture to animate in it."""
    folder = tmp_path / "project"
    manifest.write_manifest(folder, manifest.ProjectManifest(profile="history", topic_id=1))
    (folder / "assets").mkdir()
    (folder / "assets" / "vault.png").write_bytes(b"png")
    return folder


@pytest.fixture
def standalone(tmp_path):
    """A project folder nobody composed or adopted: no manifest, so the default profile and no topic."""
    folder = tmp_path / "loose"
    (folder / "assets").mkdir(parents=True)
    (folder / "assets" / "vault.png").write_bytes(b"png")
    return folder


@pytest.fixture
def paid(monkeypatch):
    """Stand-ins for the two paid calls: they write the file, note the call and answer like the real ones."""
    calls: list[tuple] = []

    async def make_picture(
        request,
        out,
        *,
        quality="high",
        profile_name="default",
        canvas=None,
        client=None,
    ):
        calls.append(("picture", request, out.name, quality, profile_name))
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (320, 180), "#c8b89a").save(out)  # the format follows the suffix
        return {"credit": AI_CREDIT, "cost_usd": generate.picture_price(quality)}

    async def make_clip(still, motion, out, *, project, seconds, topic_id=None, canvas=None):
        calls.append(("clip", motion, out.name, seconds, topic_id, canvas))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"made")
        return {"credit": AI_CREDIT, "cost_usd": generate.clip_price(seconds)}

    monkeypatch.setattr(generate, "make_picture", make_picture)
    monkeypatch.setattr(generate, "make_clip", make_clip)
    return calls
