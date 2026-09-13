"""Post-CLONER domain handoff: any domain → MR.FINANCE; never SendToAgent."""

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
    FINANCE_SPEC,
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


def _custom_ws(
    tmp_path: Path,
    product_id: str,
    *,
    session_id: str = "sess_custom",
    vertical: str | None = None,
) -> Path:
    out = tmp_path / "sessions" / session_id / product_id
    out.mkdir(parents=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id=product_id, inputs_hash="h")
    (out / "docs").mkdir(parents=True, exist_ok=True)
    payload = {"product_id": product_id}
    if vertical is not None:
        payload["vertical"] = vertical
    (out / "docs" / "product_blueprint.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    (out / BRIEF_REL).write_text(
        f"# C-BRIEF\n\nExecute {product_id} handlers.\n", encoding="utf-8"
    )
    return out


def _lettings_ws(tmp_path: Path) -> Path:
    return _custom_ws(
        tmp_path,
        "residential-lettings",
        session_id="sess_lettings",
        vertical="lettings",
    )


def _automotive_ws(tmp_path: Path, product_id: str = "car-dealership") -> Path:
    return _custom_ws(
        tmp_path,
        product_id,
        session_id="sess_auto_demo",
        vertical="automotive",
    )


def test_is_finance_domain_detects_finance_ops(tmp_path):
    out = _finance_ws(tmp_path)
    assert is_finance_domain(out) is True
    assert is_finance_domain(out, product_id="finance_ops") is True
    assert is_finance_domain(out, vertical="finance-ops") is True
    spec = detect_domain(out)
    assert spec.domain == "finance"
    assert spec.labels == FINANCE_SPEC.labels


def test_is_finance_domain_rejects_lettings_but_detects_derived(tmp_path):
    out = _lettings_ws(tmp_path)
    assert is_finance_domain(out) is False
    spec = detect_domain(out)
    assert spec.domain == "lettings"
    assert "domain:lettings" in spec.labels
    assert "handoff" in spec.labels


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
    assert "COLLECTOR+CLONER are done. The frozen command is" in marker["instruction"]
    assert "for finance" not in marker["instruction"]
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


def test_handoff_fires_on_unrelated_custom_domain(tmp_path):
    """Lettings / any custom product must fire — no allowlist skip."""
    out = _lettings_ws(tmp_path)
    opener = HandoffOpener()
    result = notify_domain_handoff(
        out,
        session_id="sess_lettings",
        product_id="residential-lettings",
        vertical="lettings",
        env=ENV,
        opener=opener,
    )
    assert result.fired is True
    assert result.skipped is False
    assert result.domain == "lettings"
    assert "domain:lettings" in opener.labels_created
    assert "handoff" in opener.labels_created
    marker = json.loads((out / HANDOFF_REL).read_text(encoding="utf-8"))
    assert marker["domain"] == "lettings"
    assert marker["vertical"] == "lettings"
    assert marker["product_id"] == "residential-lettings"
    assert "for lettings" in marker["instruction"]
    issue_posts = [b for m, u, b in opener.calls if m == "POST" and u.endswith("/issues")]
    assert issue_posts
    assert "domain:lettings" in issue_posts[0]["labels"]
    assert "handoff" in issue_posts[0]["labels"]


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


def test_webhook_required_failure_is_not_masked_by_the_issue(tmp_path):
    """Reach bug: when DOMAIN_HANDOFF_WEBHOOK_URL is set the webhook IS
    MR.FINANCE's reach channel. If it fails, a succeeding GitHub issue must NOT
    report the handoff as reached, and idempotency must NOT be stamped — else
    every retry skips and MR.FINANCE is reached exactly never (live symptom:
    reach-check works, the real CLONER→Writer handoff does not).
    """
    out = _finance_ws(tmp_path)

    class WebhookFails(HandoffOpener):
        def __call__(self, req: Request, timeout=None):
            if "hooks.example.test" in req.full_url:
                self.webhook_posts += 1
                return _Resp(500, {"error": "down"})
            return super().__call__(req, timeout=timeout)

    env = dict(ENV)
    env[DOMAIN_HANDOFF_WEBHOOK_ENV] = "https://hooks.example.test/domain-handoff"

    opener = WebhookFails()
    result = notify_domain_handoff(out, session_id="s", env=env, opener=opener)
    assert result.webhook_required is True
    assert result.webhook_posted is False
    assert result.issue_url, "the GitHub issue channel did succeed"
    assert result.fired is False, "a succeeding issue must not mask a dead webhook"
    assert "required reach channel" in result.reason

    # A dead required webhook must not stamp idempotency: the retry re-attempts
    # the webhook rather than skipping as 'already fired'.
    opener2 = WebhookFails()
    retry = notify_domain_handoff(out, session_id="s", env=env, opener=opener2)
    assert retry.already is False
    assert opener2.webhook_posts == 1
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


def test_handoff_after_cloner_fires_non_finance(tmp_path, monkeypatch):
    out = _lettings_ws(tmp_path)
    opener = HandoffOpener()
    bp = SimpleNamespace(product_id="residential-lettings", vertical="lettings")
    ws = SimpleNamespace(destination=out, workspace=out)
    ctx = SimpleNamespace(
        workspace=ws,
        blueprint=bp,
        plan=None,
        blocks_root=None,
        state={"session_id": "sess_lettings", "product_id": "residential-lettings"},
        note=None,
    )
    import app.factory.build.domain_handoff as mod

    real = mod.notify_domain_handoff

    def wrapped(output_dir, **kwargs):
        kwargs.setdefault("env", ENV)
        kwargs.setdefault("opener", opener)
        return real(output_dir, **kwargs)

    monkeypatch.setattr(mod, "notify_domain_handoff", wrapped)
    result = handoff_after_cloner(ctx, env=ENV)
    assert result.fired is True
    assert result.domain == "lettings"
    assert (out / HANDOFF_REL).is_file()


def test_handoff_after_cloner_skips_without_workspace():
    ctx = SimpleNamespace(
        workspace=None,
        blueprint=None,
        plan=None,
        blocks_root=None,
        state={},
        note=None,
    )
    result = handoff_after_cloner(ctx, env=ENV)
    assert result.skipped is True
    assert result.fired is False
    assert result.reason == "no workspace"


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
    assert spec.domain == "finance"
    assert spec.vertical == "finance_ops"
    assert spec.labels == FINANCE_SPEC.labels
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
    assert spec.domain == "automotive"
    assert spec.vertical == "automotive"
    assert spec.labels == AUTOMOTIVE_SPEC.labels
    assert is_finance_domain(out, product_id=product_id) is False


@pytest.mark.parametrize(
    ("token", "expected_domain"),
    [
        ("airline", "airline"),
        ("airline_delivery", "airline"),
        ("airline-delivery", "airline"),
        ("airline-delivery-management", "airline"),
        ("aviation", "aviation"),
        ("air_ops", "air-ops"),
        ("air-ops", "air-ops"),
        ("airops", "airops"),
        ("air_ops_portfolio", "air-ops"),
        ("riyadh_air", "riyadh"),
        ("hotelops", "hotelops"),
        ("retail", "retail"),
    ],
)
def test_detect_domain_derives_sane_slug_for_any_product(tmp_path, token, expected_domain):
    out = tmp_path / "neutral-workspace"
    out.mkdir()
    spec = detect_domain(out, product_id=token)
    assert spec.domain == expected_domain
    assert f"domain:{expected_domain}" in spec.labels
    assert "handoff" in spec.labels
    assert is_finance_domain(out, product_id=token) is False


def test_handoff_fires_on_car_dealership_post_cloner(tmp_path):
    out = _automotive_ws(tmp_path, product_id="car-dealership")
    opener = HandoffOpener()
    first = notify_domain_handoff(
        out,
        session_id="sess_auto_demo",
        product_id="car-dealership",
        env=ENV,
        opener=opener,
    )
    assert first.fired is True
    assert first.domain == "automotive"
    assert first.issue_url.endswith("/issues/42")
    assert "domain:automotive" in opener.labels_created
    assert "handoff" in opener.labels_created
    marker = json.loads((out / HANDOFF_REL).read_text(encoding="utf-8"))
    assert marker["stage"] == "post_cloner"
    assert marker["domain"] == "automotive"
    assert marker["vertical"] == "automotive"
    assert marker["product_id"] == "car-dealership"
    assert "for automotive" in marker["instruction"]
    assert "MR.FINANCE" in marker["instruction"]
    issue_posts = [b for m, u, b in opener.calls if m == "POST" and u.endswith("/issues")]
    assert issue_posts
    assert issue_posts[0]["title"].startswith("[domain-handoff] automotive post-CLONER")
    assert "domain:automotive" in issue_posts[0]["labels"]
    search = [u for m, u, _ in opener.calls if m == "GET" and "/search/issues" in u]
    assert search
    assert "label:domain:automotive" in search[0] or "domain%3Aautomotive" in search[0]

    posts_before = sum(1 for m, u, _ in opener.calls if m == "POST" and u.endswith("/issues"))
    second = notify_domain_handoff(
        out,
        session_id="sess_auto_demo",
        product_id="car-dealership",
        env=ENV,
        opener=opener,
    )
    assert second.already is True
    assert second.fired is False
    assert second.domain == "automotive"
    posts_after = sum(1 for m, u, _ in opener.calls if m == "POST" and u.endswith("/issues"))
    assert posts_after == posts_before


def test_handoff_fires_webhook_and_labels_for_airline_delivery(tmp_path):
    out = _custom_ws(
        tmp_path,
        "airline-delivery-management",
        session_id="sess_airline",
    )
    opener = HandoffOpener()
    env = dict(ENV)
    env[DOMAIN_HANDOFF_WEBHOOK_ENV] = "https://hooks.example.test/domain-handoff"
    env[DOMAIN_HANDOFF_WEBHOOK_AUTHORIZATION_ENV] = "Bearer crsr_airline"
    first = notify_domain_handoff(
        out,
        session_id="sess_airline",
        product_id="airline-delivery-management",
        env=env,
        opener=opener,
    )
    assert first.fired is True
    assert first.domain == "airline"
    assert first.webhook_posted is True
    assert opener.webhook_posts == 1
    assert opener.webhook_authorizations == ["Bearer crsr_airline"]
    assert "domain:airline" in opener.labels_created
    assert "handoff" in opener.labels_created
    marker = json.loads((out / HANDOFF_REL).read_text(encoding="utf-8"))
    assert marker["domain"] == "airline"
    assert marker["product_id"] == "airline-delivery-management"
    assert "for airline" in marker["instruction"]
    issue_posts = [b for m, u, b in opener.calls if m == "POST" and u.endswith("/issues")]
    assert issue_posts
    assert "domain:airline" in issue_posts[0]["labels"]
    assert "handoff" in issue_posts[0]["labels"]
    webhook_bodies = [
        b
        for m, u, b in opener.calls
        if m == "POST" and "hooks.example.test" in u
    ]
    assert webhook_bodies
    assert webhook_bodies[0]["domain"] == "airline"
    for _m, url, _b in opener.calls:
        assert "api.cursor.com" not in url
        assert "SendToAgent" not in url
        assert "cloud-agent" not in url

    posts_before = sum(1 for m, u, _ in opener.calls if m == "POST" and u.endswith("/issues"))
    second = notify_domain_handoff(
        out,
        session_id="sess_airline",
        product_id="airline-delivery-management",
        env=env,
        opener=opener,
    )
    assert second.already is True
    assert second.fired is False
    assert second.domain == "airline"
    posts_after = sum(1 for m, u, _ in opener.calls if m == "POST" and u.endswith("/issues"))
    assert posts_after == posts_before


def test_handoff_fires_on_aviation_vertical(tmp_path):
    out = _custom_ws(
        tmp_path,
        "airline-delivery-management",
        session_id="sess_aviation",
        vertical="aviation",
    )
    opener = HandoffOpener()
    env = dict(ENV)
    env[DOMAIN_HANDOFF_WEBHOOK_ENV] = "https://hooks.example.test/domain-handoff"
    result = notify_domain_handoff(
        out,
        session_id="sess_aviation",
        product_id="airline-delivery-management",
        vertical="aviation",
        env=env,
        opener=opener,
    )
    assert result.fired is True
    assert result.webhook_posted is True
    assert result.domain == "aviation"
    assert "domain:aviation" in opener.labels_created
    assert "handoff" in opener.labels_created


def test_handoff_after_cloner_ctx_automotive(tmp_path, monkeypatch):
    out = _automotive_ws(tmp_path)
    opener = HandoffOpener()
    bp = SimpleNamespace(product_id="car-dealership", vertical="automotive")
    ws = SimpleNamespace(destination=out, workspace=out)
    ctx = SimpleNamespace(
        workspace=ws,
        blueprint=bp,
        plan=None,
        blocks_root=None,
        state={"session_id": "sess_auto_demo", "product_id": "car-dealership"},
        note=None,
    )

    import app.factory.build.domain_handoff as mod

    real = mod.notify_domain_handoff

    def wrapped(output_dir, **kwargs):
        kwargs.setdefault("env", ENV)
        kwargs.setdefault("opener", opener)
        return real(output_dir, **kwargs)

    monkeypatch.setattr(mod, "notify_domain_handoff", wrapped)
    result = handoff_after_cloner(ctx, env=ENV)
    assert result.fired is True
    assert result.domain == "automotive"
    assert (out / HANDOFF_REL).is_file()
