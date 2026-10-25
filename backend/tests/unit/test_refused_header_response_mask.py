"""A refused header's value never leaves the API in a JSON response.

An HTTP client refusing a header (a stored token with a stray space or line
break) quotes the value in its error, and that text reaches responses from many
places: stored provider and backup errors (including rows written before this
was masked anywhere), restore and connection-test results, status endpoints.
``RefusedHeaderResponseMask`` masks it in every JSON body on the way out.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from httpx import ASGITransport, AsyncClient

from backend.app.core.response_redaction import RefusedHeaderResponseMask, redact_refused_headers_json

# Made up. Never paste a value from a real log here.
SECRET = "resp-value-5"
QUOTE = f"Illegal header value b'Bearer {SECRET} '"


def _app(manifest=None) -> FastAPI:
    app = FastAPI()

    @app.api_route("/manifest.json", methods=["GET", "HEAD"])
    async def manifest_file():
        return FileResponse(manifest, media_type="application/manifest+json")

    @app.get("/model")
    async def model():
        return {"last_error": QUOTE, "other": "kept", "n": 3}

    @app.get("/nested")
    async def nested():
        return [{"message": f"LocalProtocolError: {QUOTE}"}, {"message": f'with "quotes" {QUOTE}'}]

    @app.get("/raised")
    async def raised():
        raise HTTPException(status_code=502, detail=f"Could not reach it: {QUOTE}")

    @app.get("/explicit")
    async def explicit():
        return JSONResponse({"error": QUOTE}, status_code=400)

    @app.get("/text")
    async def text():
        return PlainTextResponse(QUOTE)

    @app.get("/stream")
    async def stream():
        async def body():
            yield b'{"error": "'
            yield QUOTE.encode()
            yield b'"}'

        return StreamingResponse(body(), media_type="application/json")

    app.add_middleware(RefusedHeaderResponseMask)
    return app


async def _get(path: str):
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_a_head_request_keeps_the_length_of_the_body_it_omits(tmp_path):
    """HEAD sends no body; the length still describes the GET body."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"name": "Bambuddy"}')
    async with AsyncClient(transport=ASGITransport(app=_app(manifest)), base_url="http://test") as client:
        head = await client.head("/manifest.json")
        get = await client.get("/manifest.json")

    assert head.headers["content-length"] == get.headers["content-length"] == str(len('{"name": "Bambuddy"}'))


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/model", "/nested", "/raised", "/explicit"])
async def test_json_responses_carry_no_header_value(path):
    response = await _get(path)

    assert SECRET not in response.text
    assert "Illegal header value [REDACTED]" in response.text
    json.loads(response.text)  # still valid JSON
    assert int(response.headers["content-length"]) == len(response.content)


@pytest.mark.asyncio
async def test_the_rest_of_the_body_is_kept():
    body = (await _get("/model")).json()

    assert body == {"last_error": "Illegal header value [REDACTED]", "other": "kept", "n": 3}


@pytest.mark.asyncio
async def test_a_streamed_json_body_is_masked_too():
    response = await _get("/stream")

    assert SECRET not in response.text


@pytest.mark.asyncio
async def test_other_content_types_pass_untouched():
    """Only JSON is the API's; a text body is left as its route wrote it."""
    assert (await _get("/text")).text == QUOTE


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b'{"a": "x Illegal header name b\'X-1\' y", "b": 1}', b'{"a": "x Illegal header name [REDACTED]", "b": 1}'),
        (b'{"a": "Illegal header value b\'\\"q\\" s\'"}', b'{"a": "Illegal header value [REDACTED]"}'),
        (b'{"a": "nothing here"}', b'{"a": "nothing here"}'),
    ],
)
def test_the_mask_ends_with_the_json_string(body, expected):
    assert redact_refused_headers_json(body) == expected


def test_main_installs_the_mask_next_to_the_routes():
    """Innermost: outer middleware re-stream the body in chunks."""
    from backend.app.main import app

    assert app.user_middleware[-1].cls is RefusedHeaderResponseMask
