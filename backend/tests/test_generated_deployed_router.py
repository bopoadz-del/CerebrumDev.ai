"""Tests for the generated deployed router template in packager.py."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.packager import _write_deployed_router


def _load_deployed_module(tmp_path: Path, domain: str = "testdomain") -> Any:
    """Render the template into tmp_path and load the generated module."""
    _write_deployed_router(tmp_path, domain)
    (tmp_path / "default_chain.json").write_text(
        json.dumps({"blocks": [], "connections": []})
    )
    module_name = f"deployed_{tmp_path.name}"
    spec = importlib.util.spec_from_file_location(
        module_name, tmp_path / "app" / "routers" / "deployed.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._RAG_STATUS = None  # fresh validation for every test
    return module


def _events(response) -> List[Dict[str, Any]]:
    """Parse canonical SSE lines from a StreamingResponse."""
    events: List[Dict[str, Any]] = []
    for raw in response.text.splitlines():
        line = raw.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


class _MockResponse:
    def __init__(self, payload: Dict[str, Any], status: int = 200):
        self._payload = payload
        self.status_code = status

    def json(self) -> Dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        pass


class _MockAsyncClient:
    def __init__(self, responses: Dict[str, Dict[str, Any]], **kwargs: Any):
        self._responses = responses
        self._calls: List[Dict[str, Any]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: Any):
        return False

    async def post(self, url: str, **kwargs: Any):
        self._calls.append({"url": url, **kwargs})
        for marker, payload in self._responses.items():
            if marker in url:
                return _MockResponse(payload)
        raise ValueError(f"unexpected POST {url} with {kwargs}")


def _make_client(responses: Dict[str, Dict[str, Any]]):
    return lambda **kw: _MockAsyncClient(responses, **kw)


@pytest.fixture
def valid_vectors() -> Dict[str, Any]:
    return {
        "session_id": "s1",
        "domain": "testdomain",
        "created_at": "2026-07-05T00:00:00+00:00",
        "chunks": ["chunk a", "chunk b", "chunk c"],
        "embeddings": [
            [0.1, 0.2, 0.3],
            [0.4, 0.5, 0.6],
            [0.7, 0.8, 0.9],
        ],
        "metadatas": [{"index": 0}, {"index": 1}, {"index": 2}],
        "embedding": {"provider": "zvec", "model": "minishlab/potion-base-8M", "dim": 3},
    }


def test_rendered_module_compiles(tmp_path: Path):
    """The generated file is valid Python."""
    module = _load_deployed_module(tmp_path)
    assert hasattr(module, "router")
    assert hasattr(module, "chat")
    assert hasattr(module, "health")


def test_health_reports_rag_available(valid_vectors, tmp_path: Path):
    """Valid embedding metadata makes RAG available."""
    (tmp_path / "vectors.json").write_text(json.dumps(valid_vectors))
    module = _load_deployed_module(tmp_path)
    app = FastAPI()
    app.include_router(module.router)

    response = TestClient(app).get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["rag"]["available"] is True
    assert "zvec" in data["rag"]["detail"]
    assert data["rag"]["dim"] == 3


def test_health_reports_legacy_index_degraded(tmp_path: Path):
    """vectors.json without an embedding stanza is treated as legacy."""
    legacy = {
        "chunks": ["chunk a"],
        "embeddings": [[0.1, 0.2, 0.3]],
    }
    (tmp_path / "vectors.json").write_text(json.dumps(legacy))
    module = _load_deployed_module(tmp_path)
    app = FastAPI()
    app.include_router(module.router)

    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["rag"]["available"] is False
    assert "predates" in response.json()["rag"]["detail"]


def test_health_reports_malformed_metadata_degraded(tmp_path: Path):
    """A non-dict or invalid embedding stanza degrades RAG."""
    bad = {
        "chunks": ["chunk a"],
        "embeddings": [[0.1, 0.2, 0.3]],
        "embedding": "not-a-dict",
    }
    (tmp_path / "vectors.json").write_text(json.dumps(bad))
    module = _load_deployed_module(tmp_path)
    app = FastAPI()
    app.include_router(module.router)

    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["rag"]["available"] is False


def test_health_reports_dimension_mismatch_degraded(valid_vectors, tmp_path: Path):
    """Declared dim must match the stored embeddings."""
    valid_vectors["embedding"]["dim"] = 999
    (tmp_path / "vectors.json").write_text(json.dumps(valid_vectors))
    module = _load_deployed_module(tmp_path)
    app = FastAPI()
    app.include_router(module.router)

    response = TestClient(app).get("/health")
    data = response.json()
    assert data["rag"]["available"] is False
    assert "mismatch" in data["rag"]["detail"]


def test_chat_with_rag_emits_sources_and_uses_zvec(
    valid_vectors, tmp_path: Path, monkeypatch
):
    """With valid metadata the chat uses zvec for RAG and emits sources."""
    monkeypatch.setenv("OLLAMA_URL", "http://ollama")
    (tmp_path / "vectors.json").write_text(json.dumps(valid_vectors))
    module = _load_deployed_module(tmp_path)
    app = FastAPI()
    app.include_router(module.router)

    responses = {
        "/v1/execute": {
            "status": "success",
            "result": {"vector": [0.1, 0.2, 0.3], "dimensions": 3},
        },
        "/api/chat": {"message": {"content": "The answer is 42."}},
    }

    with patch("httpx.AsyncClient", _make_client(responses)):
        response = TestClient(app).post("/chat", json={"message": "hello", "history": []})

    assert response.status_code == 200
    events = _events(response)
    types = [e["type"] for e in events]
    assert types[0] == "start"
    assert events[0]["rag_available"] is True
    assert events[0].get("note") == ""
    assert "sources" in types
    assert types[-1] == "end"


def test_chat_query_embedding_payload(valid_vectors, tmp_path: Path, monkeypatch):
    """The POST body to /v1/execute targets the zvec block with operation embed."""
    monkeypatch.setenv("OLLAMA_URL", "http://ollama")
    (tmp_path / "vectors.json").write_text(json.dumps(valid_vectors))
    module = _load_deployed_module(tmp_path)
    app = FastAPI()
    app.include_router(module.router)

    captured: List[Dict[str, Any]] = []

    class _CapturingClient(_MockAsyncClient):
        async def post(self, url, **kwargs):
            captured.append({"url": url, **kwargs})
            return await super().post(url, **kwargs)

    responses = {
        "/v1/execute": {
            "status": "success",
            "result": {"vector": [0.1, 0.2, 0.3], "dimensions": 3},
        },
        "/api/chat": {"message": {"content": "The answer is 42."}},
    }

    factory = lambda **kw: _CapturingClient(responses, **kw)
    with patch("httpx.AsyncClient", factory):
        TestClient(app).post("/chat", json={"message": "hello", "history": []})

    zvec_call = next(c for c in captured if "/v1/execute" in c["url"])
    payload = zvec_call["json"]
    assert payload["block"] == "zvec"
    assert payload["params"]["operation"] == "embed"


def test_chat_without_rag_replies_with_note(valid_vectors, tmp_path: Path, monkeypatch):
    """A degraded/legacy index still produces a chat response and notes RAG is off."""
    monkeypatch.setenv("OLLAMA_URL", "http://ollama")
    valid_vectors.pop("embedding")  # legacy
    (tmp_path / "vectors.json").write_text(json.dumps(valid_vectors))
    module = _load_deployed_module(tmp_path)
    app = FastAPI()
    app.include_router(module.router)

    responses = {
        "/v1/execute": {"status": "success", "result": {"vector": [0.1, 0.2, 0.3]}},
        "/api/chat": {"message": {"content": "The answer is 42."}},
    }

    with patch("httpx.AsyncClient", _make_client(responses)):
        response = TestClient(app).post("/chat", json={"message": "hello", "history": []})

    events = _events(response)
    assert events[0]["type"] == "start"
    assert events[0]["rag_available"] is False
    assert events[0].get("note")
    assert not any(e["type"] == "sources" for e in events)
    assert events[-1]["type"] == "end"


def test_chat_dimension_mismatch_skips_rag(valid_vectors, tmp_path: Path, monkeypatch):
    """A lazy dimension mismatch disables RAG for the chat turn."""
    monkeypatch.setenv("OLLAMA_URL", "http://ollama")
    valid_vectors["embedding"]["dim"] = 999
    (tmp_path / "vectors.json").write_text(json.dumps(valid_vectors))
    module = _load_deployed_module(tmp_path)
    app = FastAPI()
    app.include_router(module.router)

    responses = {
        "/v1/execute": {"status": "success", "result": {"vector": [0.1, 0.2, 0.3]}},
        "/api/chat": {"message": {"content": "The answer is 42."}},
    }

    with patch("httpx.AsyncClient", _make_client(responses)):
        response = TestClient(app).post("/chat", json={"message": "hello", "history": []})

    events = _events(response)
    assert events[0]["rag_available"] is False
    assert not any(e["type"] == "sources" for e in events)
    assert events[-1]["type"] == "end"


def test_sse_canonical_envelope_has_single_terminal(valid_vectors, tmp_path: Path, monkeypatch):
    """The stream follows start -> ... -> exactly one end."""
    monkeypatch.setenv("OLLAMA_URL", "http://ollama")
    (tmp_path / "vectors.json").write_text(json.dumps(valid_vectors))
    module = _load_deployed_module(tmp_path)
    app = FastAPI()
    app.include_router(module.router)

    responses = {
        "/v1/execute": {
            "status": "success",
            "result": {"vector": [0.1, 0.2, 0.3], "dimensions": 3},
        },
        "/api/chat": {"message": {"content": "The answer is 42."}},
    }

    with patch("httpx.AsyncClient", _make_client(responses)):
        response = TestClient(app).post("/chat", json={"message": "hello", "history": []})

    events = _events(response)
    assert events[0]["type"] == "start"
    terminal = [e for e in events if e["type"] in ("end", "error")]
    assert len(terminal) == 1
    assert terminal[0]["type"] == "end"
    assert all(e["type"] != "end" for e in events[:1])
    token_events = [e for e in events if e["type"] == "token"]
    assert token_events
    assert all("content" in e for e in token_events)


def test_missing_vectors_json_still_boots_and_answers(tmp_path: Path):
    """No vectors.json does not prevent boot or health/chat responses."""
    module = _load_deployed_module(tmp_path)
    # No vectors.json written intentionally.
    app = FastAPI()
    app.include_router(module.router)

    health = TestClient(app).get("/health")
    assert health.status_code == 200
    assert health.json()["rag"]["available"] is False

    response = TestClient(app).post("/chat", json={"message": "hello", "history": []})
    events = _events(response)
    assert events[0]["type"] == "start"
    assert events[0]["rag_available"] is False
    assert events[-1]["type"] == "end"


def test_master_key_is_sent_to_zvec(valid_vectors, tmp_path: Path, monkeypatch):
    """The self-call to /v1/execute carries the bearer token when configured."""
    monkeypatch.setenv("OLLAMA_URL", "http://ollama")
    monkeypatch.setenv("CEREBRUM_MASTER_KEY", "super-secret")
    (tmp_path / "vectors.json").write_text(json.dumps(valid_vectors))
    module = _load_deployed_module(tmp_path)
    app = FastAPI()
    app.include_router(module.router)

    captured: List[Dict[str, Any]] = []

    class _CapturingClient(_MockAsyncClient):
        async def post(self, url, **kwargs):
            captured.append({"url": url, **kwargs})
            return await super().post(url, **kwargs)

    responses = {
        "/v1/execute": {
            "status": "success",
            "result": {"vector": [0.1, 0.2, 0.3], "dimensions": 3},
        },
        "/api/chat": {"message": {"content": "The answer is 42."}},
    }

    factory = lambda **kw: _CapturingClient(responses, **kw)
    with patch("httpx.AsyncClient", factory):
        TestClient(app).post("/chat", json={"message": "hello", "history": []})

    zvec_call = next(c for c in captured if "/v1/execute" in c["url"])
    assert zvec_call["headers"]["Authorization"] == "Bearer super-secret"


def test_chat_response_content_type_is_event_stream(
    valid_vectors, tmp_path: Path, monkeypatch
):
    """/chat must declare a text/event-stream Content-Type."""
    monkeypatch.setenv("OLLAMA_URL", "http://ollama")
    (tmp_path / "vectors.json").write_text(json.dumps(valid_vectors))
    module = _load_deployed_module(tmp_path)
    app = FastAPI()
    app.include_router(module.router)

    responses = {
        "/v1/execute": {
            "status": "success",
            "result": {"vector": [0.1, 0.2, 0.3], "dimensions": 3},
        },
        "/api/chat": {"message": {"content": "The answer is 42."}},
    }

    with patch("httpx.AsyncClient", _make_client(responses)):
        response = TestClient(app).post("/chat", json={"message": "hello", "history": []})

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]


def test_invalid_vectors_json_still_boots_and_reports_degraded(tmp_path: Path):
    """Invalid JSON in vectors.json must not prevent boot or health response."""
    (tmp_path / "vectors.json").write_text("not valid json")
    module = _load_deployed_module(tmp_path)
    app = FastAPI()
    app.include_router(module.router)

    health = TestClient(app).get("/health")
    assert health.status_code == 200
    assert health.json()["rag"]["available"] is False

    response = TestClient(app).post("/chat", json={"message": "hello", "history": []})
    events = _events(response)
    assert events[0]["type"] == "start"
    assert events[0]["rag_available"] is False
    assert events[-1]["type"] == "end"


def test_non_dict_vectors_json_still_boots_and_reports_degraded(tmp_path: Path):
    """A top-level non-object in vectors.json must degrade RAG gracefully."""
    (tmp_path / "vectors.json").write_text(json.dumps([1, 2, 3]))
    module = _load_deployed_module(tmp_path)
    app = FastAPI()
    app.include_router(module.router)

    health = TestClient(app).get("/health")
    assert health.status_code == 200
    assert health.json()["rag"]["available"] is False
    assert "not an object" in health.json()["rag"]["detail"]

    response = TestClient(app).post("/chat", json={"message": "hello", "history": []})
    events = _events(response)
    assert events[0]["type"] == "start"
    assert events[0]["rag_available"] is False
    assert events[-1]["type"] == "end"
