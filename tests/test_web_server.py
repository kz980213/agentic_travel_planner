import asyncio
import io
import time

import pytest
from fastapi.testclient import TestClient

import web_server


@pytest.fixture
def client():
    return TestClient(web_server.app)


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_empty_message_rejected(client):
    r = client.post("/api/chat", data={"message": "   "})
    assert r.status_code == 400


def test_upload_too_large_rejected(client, monkeypatch):
    from travel_agent.config import Config
    monkeypatch.setattr(Config, "MAX_UPLOAD_MB", 1)
    big = b"X" * (1 * 1024 * 1024 + 100)
    r = client.post(
        "/api/chat",
        data={"message": "hi"},
        files={"file": ("big.pdf", big, "application/pdf")},
    )
    assert r.status_code == 413


def test_upload_bad_magic_rejected(client):
    r = client.post(
        "/api/chat",
        data={"message": "hi"},
        files={"file": ("fake.pdf", b"NOT_A_PDF", "application/pdf")},
    )
    assert r.status_code == 415


def test_upload_disallowed_mime(client):
    r = client.post(
        "/api/chat",
        data={"message": "hi"},
        files={"file": ("evil.exe", b"MZ\x90\x00", "application/x-msdownload")},
    )
    assert r.status_code == 415


async def test_session_isolation():
    # Build a SessionManager directly with a stub LLM so the test doesn't depend
    # on whether an LLM key is set in the environment. CI has no LLM key, which
    # would otherwise make the module-level `web_server.sessions` a
    # MockSessionManager (new agent per call, no per-session memory) and fail.
    from travel_agent.mcp.mcp_server import MCPServer

    mgr = web_server.SessionManager(
        object(), MCPServer(), max_sessions=10, ttl_seconds=3600
    )
    a = await mgr.get_or_create("sess-A")
    b = await mgr.get_or_create("sess-B")
    a2 = await mgr.get_or_create("sess-A")
    assert a is a2                    # same session id -> same orchestrator
    assert a is not b                 # different session -> different orchestrator
    assert a.memory is not b.memory   # independent memory per session


async def test_session_lru_eviction():
    from travel_agent.mcp.mcp_server import MCPServer

    mgr = web_server.SessionManager(
        object(), MCPServer(), max_sessions=1, ttl_seconds=3600
    )
    first = await mgr.get_or_create("one")
    await mgr.get_or_create("two")        # over capacity -> evicts "one"
    first_again = await mgr.get_or_create("one")
    assert first_again is not first      # "one" was evicted and rebuilt


def test_webhook_rejects_bad_payload(client):
    r = client.post("/webhooks/stripe", content=b"not json", headers={"stripe-signature": "x"})
    assert r.status_code == 400
