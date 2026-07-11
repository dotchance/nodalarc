from __future__ import annotations

import asyncio
from collections.abc import Iterable

from vs_api.main import (
    _MAX_BODY_BYTES,
    _YAML_IMPORT_BODY_BYTES,
    _YAML_IMPORT_PATH,
    BodySizeLimitMiddleware,
)


def _drive(path: str, chunks: Iterable[bytes], *, declared_length: int | None = None):
    chunk_list = list(chunks)
    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": position < len(chunk_list) - 1,
        }
        for position, chunk in enumerate(chunk_list)
    ] or [{"type": "http.request", "body": b"", "more_body": False}]
    sent = []
    downstream_bodies: list[bytes] = []

    async def downstream(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            downstream_bodies.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    headers = []
    if declared_length is not None:
        headers.append((b"content-length", str(declared_length).encode("ascii")))
    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers,
        "server": ("test", 80),
        "client": ("test", 1),
        "root_path": "",
        "http_version": "1.1",
    }

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    asyncio.run(BodySizeLimitMiddleware(downstream)(scope, receive, send))
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    return status, b"".join(downstream_bodies)


def test_chunked_body_is_counted_without_content_length() -> None:
    status, body = _drive(
        "/api/v1/ordinary",
        (b"a" * _MAX_BODY_BYTES, b"b"),
    )

    assert status == 413
    assert body == b""


def test_yaml_import_has_its_own_bounded_multi_file_limit() -> None:
    payload = b"a" * (_MAX_BODY_BYTES + 1)

    status, body = _drive(_YAML_IMPORT_PATH, (payload,))

    assert status == 204
    assert body == payload


def test_yaml_import_rejects_actual_bytes_above_its_route_limit() -> None:
    status, body = _drive(
        _YAML_IMPORT_PATH,
        (b"a" * _YAML_IMPORT_BODY_BYTES, b"b"),
        declared_length=1,
    )

    assert status == 413
    assert body == b""
