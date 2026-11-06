"""A refused header's value masked in every JSON response.

An HTTP client refusing a header quotes the header's value, and the value is
usually a credential: a stored token saved with a stray space or line break.
That error text reaches the API from many places -- a provider's stored error,
a backup or restore result, a connection test, a status endpoint, rows stored
before any of them masked it -- so it is masked once, where every JSON body
leaves, rather than at each place that may hand one on. Logs are masked by
``logging_filters.RefusedHeaderRedactFilter``.
"""

from __future__ import annotations

import re

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_MARKER = b"Illegal header "
# Inside a JSON string: masked up to the string's closing quote, so the body
# stays valid JSON.
_REFUSED_HEADER_IN_JSON = re.compile(rb'(Illegal header (?:value|name) )(?:[^"\\]|\\.)*')


def redact_refused_headers_json(body: bytes) -> bytes:
    """*body*, a JSON document, with every quoted refused header masked."""
    if _MARKER not in body:
        return body
    return _REFUSED_HEADER_IN_JSON.sub(rb"\1[REDACTED]", body)


class RefusedHeaderResponseMask:
    """ASGI middleware masking refused headers in JSON response bodies.

    Install it innermost (added before any other middleware): outer
    ``@app.middleware`` layers re-stream a body in chunks. A body with a
    Content-Length is collected and masked whole, with the length fixed up; a
    streamed one is masked chunk by chunk.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start: Message | None = None
        sized = False
        chunks: list[bytes] = []

        async def masking_send(message: Message) -> None:
            nonlocal start, sized
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message["headers"])
                media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if not (media_type == "application/json" or media_type.endswith("+json")):
                    await send(message)
                    return
                start = message
                sized = "content-length" in headers
                if not sized:
                    await send(start)
                return
            if start is None or message["type"] != "http.response.body":
                await send(message)
                return
            if not sized:
                await send({**message, "body": redact_refused_headers_json(message.get("body", b""))})
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            original = b"".join(chunks)
            body = redact_refused_headers_json(original)
            if body is not original:
                # Only a masked body changes length; a HEAD response's empty
                # body keeps the length of the GET body it stands for.
                MutableHeaders(raw=start["headers"])["content-length"] = str(len(body))
            await send(start)
            await send({"type": "http.response.body", "body": body, "more_body": False})

        await self.app(scope, receive, masking_send)
