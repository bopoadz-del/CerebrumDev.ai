"""Post-CLONER domain handoff: finance + automotive → MR.FINANCE; never SendToAgent."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request

import pytest

from app.factory.build.domain_handoff import (
    AUTOMOTIVE_SPEC,
    BRIEF_REL,
    DOMAIN_HANDOFF_FIRED,
    DOMAIN_HANDOFF_WEBHOOK_AUTHORIZATION_ENV,
    DOMAIN_HANDOFF_WEBHOOK_ENV,
    DOMAIN_HANDOFF_WEBHOOK_KEY_ENV,
    HANDOFF_REL,
    detect_domain,
    handoff_after_cloner,
    is_finance_domain,
    notify_domain_handoff,
    post_webhook,
    webhook_authorization_header,
)
from app.factory.build.ledger import BuildLedger


ENV = {
    "CEREBRUM_BUILDS_GITHUB_TOKEN": "builds-test-token",
    "CEREBRUM_BUILDS_REPO": "bopoadz-del/cerebrum-builds",
    "FACTORY_PUBLIC_URL": "https://cerebrumdev.ai",
}


class _Resp:
    def __init__(self, status: int, body):
        self.status = status
        raw = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
        self._body = raw

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _req_authorization(req: Request) -> str | None:
    """Authorization as urllib would send it (header names are title-cased)."""
    value = req.get_header("Authorization")
    if value is not None:
        return value
    for key, val in req.header_items():
        if key.lower() == "authorization":
            return val
    return None


class HandoffOpener:
    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.issue_number = 42
        self.labels_created: list[str] = []
        self.webhook_posts = 0
        self.webhook_authorizations: list[str | None] = []
        self.search_items: list[dict] = []

    def __call__(self, req: Request, timeout=None):
        method = req.get_method()
        url = req.full_url
        body = None
        if req.data:
            body = json.loads(req.data.decode("utf-8"))
        self.calls.append((method, url, body))
        if "hooks.example.test" in url:
            self.webhook_posts += 1
            self.webhook_authorizations.append(_req_authorization(req))
            return _Resp(200, {"ok": True})
        if "/search/issues" in url:
            return _Resp(200, {"items": list(self.search_items)})
        if "/labels/" in url and method == "GET":
            return _Resp(404, {"message": "Not Found"})
        if url.endswith("/labels") and method == "POST":
            self.labels_created.append(str((body or {}).get("name") or ""))
            return _Resp(201, body or {})
        if url.endswith("/issues") and method == "POST":
            return _Resp(
                201,
                {
                    "number": self.issue_number,
                    "html_url": (
                        "https://github.com/bopoadz-del/cerebrum-builds"
                        f"/issues/{self.issue_number}"
                    ),
                },
            )
        if "/issues/" in url and method == "PATCH":
            num = int(url.rstrip("/").split("/")[-1])
            return _Resp(
                200,
                {
                    "number": num,
                    "html_url": (
                        f"https://github.com/bopoadz-del/cerebrum-builds/issues/{num}"
                    ),
                },
            )
        return _Resp(200, {})


def _finance_ws(tmp_path: Path) -> Path:
    out = tmp_path / "sessions" / "sess_finance_demo" / "finance-ops"
    out.mkdir(parents=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="finance-ops", inputs_hash="h")
    (out / "docs").mkdir(parents=True, exist_ok=True)
    (out / "docs" / "product_blueprint.json").write_text(
        json.dumps({"product_id": "finance-ops", "vertical": "finance_ops"}),
        encoding="utf-8",
    )
    (out / BRIEF_REL).write_text(
        "# C-BRIEF\n\nExecute finance ops handlers.\n", encoding="utf-8"
    )
    return out


def _lettings_ws(tmp_path: Path) -> Path:
    out = tmp_path / "sessions" / "sess_lettings" / "residential-lettings"
    out.mkdir(parents=True)
    (out / "docs").mkdir(parents=True, exist_ok=True)
    (out / "docs" / "product_blueprint.json").write_text(
        json.dumps({"product_id": "residential-lettings", "vertical": "lettings"}),
        encoding="utf-8",
    )
    return out


def _automotive_ws(tmp_path: Path, product_id: str = "car-dealership") -> Path:
    out = tmp_path / "sessions" / "sess_auto_demo" / product_id
    out.mkdir(parents=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id=product_id, inputs_hash="h")
    (out / "docs").mkdir(parents=True, exist_ok=True)
    (out / "docs" / "product_blueprint.json").write_text(
        json.dumps({"product_id": product_id, "vertical": "automotive"}),
        encoding="utf-8",
    )
    (out / BRIEF_REL).write_text(
        "# C-BRIEF\n\nExecute automotive dealership handlers.\n", encoding="utf-8"
    )
    return out


def test_is_finance_domain_detects_finance_ops(tmp_path):
    out = _finance_ws(tmp_path)
    assert is_finance_domain(out) is True
    assert is_finance_domain(out, product_id="finance_ops") is True
    assert is_finance_domain(out, vertical="finance-ops") is True
    spec = detect_domain(out)
    assert spec is not None
    assert spec.domain == "finance"
    assert spec.labels == FINANCE_SPEC.labels


def test_is_finance_domain_rejects_lettings(tmp_path):
    out = _lettings_ws(tmp_path)
    assert is_finance_domain(out) is False
    assert detect_domain(out) is None


@pytest.mark.parametrize(
    "product_id",
    [
        "finance",
        "finance_ops",
        "finance-ops",
        "financeops",
    ],
)
def test_detect_domain_finance_product_ids(tmp_path, product_id):
    out = tmp_path / "neutral-workspace"
    out.mkdir()
    spec = detect_domain(out, product_id=product_id)
    assert spec is not None
    assert spec.domain == "finance"
    assert spec.vertical == "finance_ops"
    assert "domain:finance" in spec.labels
    assert is_finance_domain(out, product_id=product_id) is True


@pytest.mark.parametrize(
    "product_id",
    [
        "car_dealership",
        "car-dealership",
        "cardealership",
        "automotive",
        "auto_dealership",
        "auto-dealership",
        "dealership",
    ],
)
def test_detect_domain_automotive_product_ids(tmp_path, product_id):
    out = tmp_path / "neutral-workspace"
    out.mkdir()
    spec = detect_domain(out, product_id=product_id)
    assert spec is not None
    assert spec.domain == "automotive"
    assert spec.vertical == "automotive"
    assert spec.labels == AUTOMOTIVE_SPEC.labels
    assert "domain:automotive" in spec.labels
    assert is_finance_domain(out, product_id=product_id) is False


def test_handoff_fires_once_on_finance_post_cloner(tmp_path):
    out = _finance_ws(tmp_path)
    opener = HandoffOpener()
    first = notify_domain_handoff(
        out,
        session_id="sess_finance_demo",
        product_id="finance-ops",
        env=ENV,
        opener=opener,
    )
    assert first.fired is True
    assert first.issue_url.endswith("/issues/42")
    assert "domain:finance" in opener.labels_created or any(
        m == "POST" and u.endswith("/issues") for m, u, _ in opener.calls
    )
    marker = json.loads((out / HANDOFF_REL).read_text(encoding="utf-8"))
    assert marker["stage"] == "post_cloner"
    assert marker["domain"] == "finance"
    assert marker["session_id"] == "sess_finance_demo"
    assert marker["coder_brief_path"] == "docs/coder_brief.md"
    assert "floor?session=" in marker["floor_url"]
    assert "MR.FINANCE" in marker["instruction"]
    # Issue body carried the frozen brief
    issue_posts = [b for m, u, b in opener.calls if m == "POST" and u.endswith("/issues")]
    assert issue_posts
    assert "C-BRIEF" in issue_posts[0]["body"]
    assert "docs/coder_brief.md" in issue_posts[0]["body"]

    posts_before = sum(1 for m, u, _ in opener.calls if m == "POST" and u.endswith("/issues"))
    second = notify_domain_handoff(
        out,
        session_id="sess_finance_demo",
        product_id="finance-ops",
        env=ENV,
        opener=opener,
    )
    assert second.already is True
    assert second.fired is False
    posts_after = sum(1 for m, u, _ in opener.calls if m == "POST" and u.endswith("/issues"))
    assert posts_after == posts_before

    honesty = [
        (e.payload or {}).get("honesty")
        for e in BuildLedger(out / "build_ledger.jsonl").events()
    ]
    assert DOMAIN_HANDOFF_FIRED in honesty


def test_handoff_never_on_non_finance(tmp_path):
    out = _lettings_ws(tmp_path)
    opener = HandoffOpener()
    result = notify_domain_handoff(
        out,
        product_id="residential-lettings",
        vertical="lettings",
        env=ENV,
        opener=opener,
    )
    assert result.skipped is True
    assert result.fired is False
    assert opener.calls == []
    assert not (out / HANDOFF_REL).exists()


def test_handoff_never_calls_cursor_or_agent_apis(tmp_path):
    out = _finance_ws(tmp_path)
    opener = HandoffOpener()
    notify_domain_handoff(
        out,
        session_id="sess_finance_demo",
        env=ENV,
        opener=opener,
    )
    for _m, url, _b in opener.calls:
        assert "api.cursor.com" not in url
        assert "SendToAgent" not in url
        assert "cloud-agent" not in url


def test_webhook_authorization_header_normalizes_token_shapes():
    assert webhook_authorization_header({}) is None
    assert webhook_authorization_header({DOMAIN_HANDOFF_WEBHOOK_AUTHORIZATION_ENV: ""}) is None
    assert (
        webhook_authorization_header(
            {DOMAIN_HANDOFF_WEBHOOK_AUTHORIZATION_ENV: "crsr_test_token_not_a_secret"}
        )
        == "Bearer crsr_test_token_not_a_secret"
    )
    assert (
        webhook_authorization_header(
            {DOMAIN_HANDOFF_WEBHOOK_AUTHORIZATION_ENV: "Bearer crsr_already"}
        )
        == "Bearer crsr_already"
    )
    assert (
        webhook_authorization_header(
            {
                DOMAIN_HANDOFF_WEBHOOK_AUTHORIZATION_ENV: (
                    "Authorization: Bearer crsr_from_header_line"
                )
            }
        )
        == "Bearer crsr_from_header_line"
    )
    assert (
        webhook_authorization_header(
            {DOMAIN_HANDOFF_WEBHOOK_AUTHORIZATION_ENV: "Authorization: crsr_bare_after_label"}
        )
        == "Bearer crsr_bare_after_label"
    )
    assert (
        webhook_authorization_header(
            {DOMAIN_HANDOFF_WEBHOOK_KEY_ENV: "crsr_from_key_alias"}
        )
        == "Bearer crsr_from_key_alias"
    )
    # AUTHORIZATION wins over KEY when both are set.
    assert (
        webhook_authorization_header(
            {
                DOMAIN_HANDOFF_WEBHOOK_AUTHORIZATION_ENV: "crsr_primary",
                DOMAIN_HANDOFF_WEBHOOK_KEY_ENV: "crsr_alias",
            }
        )
        == "Bearer crsr_primary"
    )


def test_post_webhook_attaches_authorization_when_env_set():
    captured: dict = {}

    def opener(req: Request, timeout=None):
        captured["url"] = req.full_url
        captured["authorization"] = _req_authorization(req)
        captured["has_authorization_header"] = any(
            k.lower() == "authorization" for k, _ in req.header_items()
        ) or req.has_header("Authorization")
        return _Resp(200, {"ok": True})

    env = {
        DOMAIN_HANDOFF_WEBHOOK_ENV: "https://hooks.example.test/domain-handoff",
        DOMAIN_HANDOFF_WEBHOOK_AUTHORIZATION_ENV: "crsr_test_token_not_a_secret",
    }
    assert post_webhook({"schema_version": "domain_handoff.v1"}, env=env, opener=opener) is True
    assert captured["authorization"] == "Bearer crsr_test_token_not_a_secret"
    assert captured["has_authorization_header"] is True
    assert "api.cursor.com" not in captured["url"]
    assert "SendToAgent" not in captured["url"]
    assert "cloud-agent" not in captured["url"]


def test_post_webhook_omits_authorization_when_unset():
    captured: dict = {}

    def opener(req: Request, timeout=None):
        captured["url"] = req.full_url
        captured["authorization"] = _req_authorization(req)
        captured["header_names"] = [k.lower() for k, _ in req.header_items()]
        return _Resp(200, {"ok": True})

    env = {DOMAIN_HANDOFF_WEBHOOK_ENV: "https://hooks.example.test/domain-handoff"}
    assert post_webhook({"schema_version": "domain_handoff.v1"}, env=env, opener=opener) is True
    assert captured["authorization"] is None
    assert "authorization" not in captured["header_names"]
    assert "api.cursor.com" not in captured["url"]
    assert "SendToAgent" not in captured["url"]
    assert "cloud-agent" not in captured["url"]


def test_webhook_posts_when_env_set(tmp_path):
    out = _finance_ws(tmp_path)
    opener = HandoffOpener()
    env = dict(ENV)
    env[DOMAIN_HANDOFF_WEBHOOK_ENV] = "https://hooks.example.test/domain-handoff"
    result = notify_domain_handoff(
        out,
        session_id="sess_finance_demo",
        env=env,
        opener=opener,
    )
    assert result.webhook_posted is True
    assert opener.webhook_posts == 1
    assert opener.webhook_authorizations == [None]


def test_webhook_posts_authorization_when_authorization_env_set(tmp_path):
    out = _finance_ws(tmp_path)
    opener = HandoffOpener()
    env = dict(ENV)
    env[DOMAIN_HANDOFF_WEBHOOK_ENV] = "https://hooks.example.test/domain-handoff"
    env[DOMAIN_HANDOFF_WEBHOOK_AUTHORIZATION_ENV] = "Bearer crsr_notify_path"
    result = notify_domain_handoff(
        out,
        session_id="sess_finance_demo",
        env=env,
        opener=opener,
    )
    assert result.webhook_posted is True
    assert opener.webhook_posts == 1
    assert opener.webhook_authorizations == ["Bearer crsr_notify_path"]
    for _m, url, _b in opener.calls:
        assert "api.cursor.com" not in url
        assert "SendToAgent" not in url
        assert "cloud-agent" not in url


def test_handoff_after_cloner_ctx_finance(tmp_path, monkeypatch):
    out = _finance_ws(tmp_path)
    opener = HandoffOpener()
    bp = SimpleNamespace(product_id="finance-ops", vertical="finance_ops")
    ws = SimpleNamespace(destination=out, workspace=out)
    ctx = SimpleNamespace(
        workspace=ws,
        blueprint=bp,
        plan=None,
        blocks_root=None,
        state={"session_id": "sess_finance_demo", "product_id": "finance-ops"},
        note=None,
    )

    import app.factory.build.domain_handoff as mod

    monkeypatch.setattr(mod, "urlopen", opener)
    # notify uses opener kw — patch notify to inject opener/env
    real = mod.notify_domain_handoff

    def wrapped(output_dir, **kwargs):
        kwargs.setdefault("env", ENV)
        kwargs.setdefault("opener", opener)
        return real(output_dir, **kwargs)

    monkeypatch.setattr(mod, "notify_domain_handoff", wrapped)
    result = handoff_after_cloner(ctx, env=ENV)
    assert result.fired is True
    assert (out / HANDOFF_REL).is_file()


def test_handoff_after_cloner_skips_non_finance(tmp_path):
    out = _lettings_ws(tmp_path)
    bp = SimpleNamespace(product_id="residential-lettings", vertical="lettings")
    ws = SimpleNamespace(destination=out, workspace=out)
    ctx = SimpleNamespace(
        workspace=ws,
        blueprint=bp,
        plan=None,
        blocks_root=None,
        state={"session_id": "sess_lettings"},
        note=None,
    )
    result = handoff_after_cloner(ctx, env=ENV)
    assert result.skipped is True
    assert result.fired is False
    assert not (out / HANDOFF_REL).exists()


def test_handoff_updates_existing_issue(tmp_path):
    out = _finance_ws(tmp_path)
    opener = HandoffOpener()
    opener.search_items = [
        {
            "number": 7,
            "html_url": "https://github.com/bopoadz-del/cerebrum-builds/issues/7",
            "title": "[domain-handoff] finance post-CLONER sess_finance_demo",
        }
    ]
    result = notify_domain_handoff(
        out,
        session_id="sess_finance_demo",
        env=ENV,
        opener=opener,
    )
    assert result.issue_number == 7
    assert any(m == "PATCH" and "/issues/7" in u for m, u, _ in opener.calls)
    assert not any(m == "POST" and u.endswith("/issues") for m, u, _ in opener.calls)


def test_render_yaml_declares_webhook_secrets_without_values():
    """Dashboard-only: sync false, no committed URL or Bearer token."""
    import yaml

    repo = Path(__file__).resolve().parents[3]
    render = yaml.safe_load((repo / "render.yaml").read_text(encoding="utf-8"))
    web = [s for s in render["services"] if s.get("name") == "cerebrumdev-backend"]
    assert web, "cerebrumdev-backend missing from render.yaml"
    by_key = {e["key"]: e for e in web[0].get("envVars", []) if e.get("key")}
    for name in (
        DOMAIN_HANDOFF_WEBHOOK_ENV,
        DOMAIN_HANDOFF_WEBHOOK_AUTHORIZATION_ENV,
    ):
        assert name in by_key, f"{name} must be declared in render.yaml"
        entry = by_key[name]
        assert entry.get("sync") is False, f"{name} must be dashboard-only (sync: false)"
        assert "value" not in entry, f"{name} must not commit a value"
        assert "generateValue" not in entry, f"{name} is an operator secret, not generated"
