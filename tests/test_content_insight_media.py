from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from funding_story import media
from funding_story.models import SourceImageRef


def _settings():
    return SimpleNamespace(internal_http_timeout_seconds=1)


def _image():
    output = BytesIO()
    Image.new("RGB", (1, 1), "red").save(output, format="PNG")
    return output.getvalue()


def test_public_image_reader_checks_mime_and_actual_bytes(monkeypatch):
    content = _image()
    monkeypatch.setattr(media, "settings", _settings)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"Content-Type": "image/png"}, content=content)
        )
    ) as client:
        monkeypatch.setattr(media.httpx, "stream", client.stream)
        assert media.read_public_image("https://files.example.com/body.png") == (content, "image/png")

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"Content-Type": "image/gif"}, content=content)
        )
    ) as client:
        monkeypatch.setattr(media.httpx, "stream", client.stream)
        with pytest.raises(ValueError, match="MIME"):
            media.read_public_image("https://files.example.com/body.png")

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(403, headers={"Content-Type": "application/xml"})
        )
    ) as client:
        monkeypatch.setattr(media.httpx, "stream", client.stream)
        with pytest.raises(httpx.HTTPStatusError):
            media.read_public_image("https://files.example.com/private.png")


def test_signed_image_reader_keeps_metadata_validation(monkeypatch):
    content = _image()
    reference = SourceImageRef(
        slot_id="story_content.1",
        read_url="https://files.example.com/body.png?signature=example",
        content_type="image/png",
        file_size=len(content),
        expires_at="2099-01-01T00:00:00Z",
    )
    monkeypatch.setattr(media, "settings", _settings)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"Content-Type": "image/png"}, content=content)
        )
    ) as client:
        monkeypatch.setattr(media.httpx, "stream", client.stream)
        assert media.read_source_image(reference) == (content, "image/png")

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"invalid")
        )
    ) as client:
        monkeypatch.setattr(media.httpx, "stream", client.stream)
        with pytest.raises(ValueError, match="크기가 계약과 다릅니다"):
            media.read_source_image(reference)
