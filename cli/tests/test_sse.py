import json
from unittest import mock

import httpx
import pytest

from cerebrum_cli import sse


def _make_sse_response(lines: list[str], status: int = 200) -> httpx.Response:
    body = "\n".join(lines) + "\n"
    return httpx.Response(
        status,
        content=body.encode(),
        headers={"content-type": "text/event-stream"},
    )


def test_stream_turn_assembles_tokens_and_summary(capsys):
    lines = [
        'data: {"type": "start"}',
        'data: {"type": "token", "content": "Hello"}',
        'data: {"type": "token", "content": " world"}',
        'data: {"type": "end"}',
    ]
    response = _make_sse_response(lines)

    with mock.patch("httpx.Client.stream", return_value=_iter_context(response)):
        with httpx.Client() as client:
            sse.stream_turn(client, "http://x", {"message": "hi"}, {})

    captured = capsys.readouterr()
    assert "Hello world" in captured.out or "Hello" in captured.out
    assert "turn summary" in captured.out
    assert "events=4" in captured.out


def test_stream_turn_pairs_tool_call_and_result(capsys):
    lines = [
        'data: {"type": "tool_call", "name": "search", "arguments": {"q": "x"}}',
        'data: {"type": "tool_result", "name": "search", "status": "ok"}',
        'data: {"type": "end"}',
    ]
    response = _make_sse_response(lines)

    with mock.patch("httpx.Client.stream", return_value=_iter_context(response)):
        with httpx.Client() as client:
            sse.stream_turn(client, "http://x", {"message": "hi"}, {})

    captured = capsys.readouterr()
    assert "TOOL_CALL" in captured.out
    assert "TOOL_RESULT" in captured.out
    assert "search" in captured.out


def test_stream_turn_events_mode_suppresses_tokens(capsys):
    lines = [
        'data: {"type": "token", "content": "secret"}',
        'data: {"type": "end"}',
    ]
    response = _make_sse_response(lines)

    with mock.patch("httpx.Client.stream", return_value=_iter_context(response)):
        with httpx.Client() as client:
            sse.stream_turn(client, "http://x", {"message": "hi"}, {}, events=True)

    captured = capsys.readouterr()
    assert "FIRST TOKEN" in captured.out
    assert "secret" not in captured.out


def test_stream_turn_raw_mode(capsys):
    lines = [
        'data: {"type": "token", "content": "hi"}',
        'data: {"type": "end"}',
    ]
    response = _make_sse_response(lines)

    with mock.patch("httpx.Client.stream", return_value=_iter_context(response)):
        with httpx.Client() as client:
            sse.stream_turn(client, "http://x", {"message": "hi"}, {}, raw=True)

    captured = capsys.readouterr()
    assert '{"type": "token", "content": "hi"}' in captured.out


def test_stream_turn_non_streaming_json_fallback(capsys):
    body = json.dumps({"answer": "plain json"})
    response = httpx.Response(
        200,
        content=body.encode(),
        headers={"content-type": "application/json"},
    )

    with mock.patch("httpx.Client.stream", return_value=_iter_context(response)):
        with httpx.Client() as client:
            sse.stream_turn(client, "http://x", {"message": "hi"}, {})

    captured = capsys.readouterr()
    assert "plain json" in captured.out
    assert "streaming is not enabled" in captured.err


def test_stream_turn_error_status(capsys):
    response = httpx.Response(500, content=b"boom")

    with mock.patch("httpx.Client.stream", return_value=_iter_context(response)):
        with httpx.Client() as client:
            sse.stream_turn(client, "http://x", {"message": "hi"}, {})

    captured = capsys.readouterr()
    assert "HTTP 500" in captured.err


# Helper to make httpx.Client.stream usable as a context manager.
class _iter_context:
    def __init__(self, response: httpx.Response):
        self._response = response

    def __enter__(self):
        return self._response

    def __exit__(self, *args):
        return False
