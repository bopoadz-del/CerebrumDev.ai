#!/usr/bin/env python3
"""Post-deploy smoke: prove the factory is wired, not parked.

Runs against the LIVE deployment after every deploy. Asserts the full
factory loop end-to-end with the real LLM — deterministic/fallback
success is NEVER accepted as evidence (deploy gate, AGENTS.md).

Usage:
    python3 scripts/post_deploy_smoke.py [base_url]

Canonical live host: https://api.cerebrum-dev.com

Verified-principal options (public email verification stays fail-closed):
    SMOKE_GATE_TOKEN          — calls POST /v1/auth/smoke-login
    SMOKE_EMAIL + SMOKE_PASSWORD
    SMOKE_EMAIL_2 + SMOKE_PASSWORD_2  — optional isolation peer

If no verified-principal secret is set, the script waits for /health,
/ready, and (when GITHUB_SHA is set) a matching /version git_sha, then
PASSES with a GitHub notice that gated factory checks were skipped. Do
not invent or commit a token. A missing GitHub Actions secret must not
fail the whole master pipeline.

The script waits up to SMOKE_READY_WAIT_S (default 300) after a Render
bounce before any unauthenticated or gated check. Ready means /health
HTTP 200 and status=="ok", /ready HTTP 200 and status=="ready", and —
when GITHUB_SHA is set — /version git_sha matching that SHA (full or
unique prefix). Local runs with GITHUB_SHA unset keep the health/ready
wait and do not SHA-gate.

Exit code 0 = all checks that ran pass. Prints a LIVE/DEAD line per kernel.
"""
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile

DEFAULT_BASE = "https://api.cerebrum-dev.com"
FAILURES = []
TRANSIENT = {502, 503, 504}
SKIP_ANNOTATION = (
    "Gated factory checks skipped — SMOKE_GATE_TOKEN (and "
    "SMOKE_EMAIL+SMOKE_PASSWORD) unset. Unauthenticated health/ready/version only."
)

# Set in main() so `import` under pytest does not treat test paths as a host.
BASE = DEFAULT_BASE


def resolve_base(argv=None):
    args = sys.argv if argv is None else argv
    if len(args) > 1:
        candidate = str(args[1]).rstrip("/")
        # Only an explicit URL is a host. pytest argv[1] is a test path.
        if candidate.startswith(("http://", "https://")):
            return candidate
    return os.environ.get("SMOKE_BASE_URL", DEFAULT_BASE).rstrip("/")


def ready_wait_seconds():
    raw = os.environ.get("SMOKE_READY_WAIT_S", "300").strip() or "300"
    try:
        return max(0, int(raw))
    except ValueError:
        return 300


def ready_interval_seconds():
    raw = os.environ.get("SMOKE_READY_INTERVAL_S", "5").strip() or "5"
    try:
        return max(0.05, float(raw))
    except ValueError:
        return 5.0


def has_gated_credentials():
    """True when a verified-principal secret is present.

    An empty GitHub ``secrets.SMOKE_GATE_TOKEN`` is unset, not a token.
    """
    if os.environ.get("SMOKE_GATE_TOKEN", "").strip():
        return True
    email = os.environ.get("SMOKE_EMAIL", "").strip()
    password = os.environ.get("SMOKE_PASSWORD", "").strip()
    return bool(email and password)


def emit_gated_skip_annotation():
    """Human line plus a GitHub Actions notice. Never fails the job."""
    print(f"SMOKE SKIP: {SKIP_ANNOTATION}")
    print(f"::notice title=Post-deploy smoke::{SKIP_ANNOTATION}")


def req(method, path, body=None, token=None, raw=False, extra_headers=None, retries=4, base=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    last_status, last_body = None, None
    root = (base or BASE).rstrip("/")
    for attempt in range(retries + 1):
        r = urllib.request.Request(
            root + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(r, timeout=300) as resp:
                data = resp.read()
                return resp.status, (data if raw else json.loads(data or b"null"))
        except urllib.error.HTTPError as e:
            b = e.read()
            try:
                parsed = json.loads(b or b"null")
            except Exception:
                parsed = {"raw": b[:300].decode(errors="replace")}
            last_status, last_body = e.code, (b if raw else parsed)
            if e.code in TRANSIENT and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return last_status, last_body
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_status, last_body = 0, {"raw": str(e)[:300]}
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return last_status, last_body
    return last_status, last_body


def _mapping(body):
    return body if isinstance(body, dict) else {}


def expected_git_sha(explicit=None):
    """GITHUB_SHA when set (Actions), else empty. Explicit overrides env."""
    if explicit is not None:
        return str(explicit).strip()
    return os.environ.get("GITHUB_SHA", "").strip()


def git_sha_matches(live_sha, expected_sha):
    """True when live /version git_sha matches expected (full or unique prefix).

    Empty expected_sha is not a gate (local run). Prefix match requires the
    shorter side to be at least 7 hex chars — git's default short SHA.
    """
    expected = (expected_sha or "").strip().lower()
    if not expected:
        return True
    live = (live_sha or "").strip().lower()
    if not live:
        return False
    if live == expected:
        return True
    shorter, longer = (live, expected) if len(live) <= len(expected) else (expected, live)
    return len(shorter) >= 7 and longer.startswith(shorter)


def surface_is_ready(
    health_status,
    health_body,
    ready_status,
    ready_body,
    version_status=None,
    version_body=None,
    expected_sha="",
):
    """True when /health, /ready, and (when SHA-gated) /version are live.

    Health must be HTTP 200 with status==\"ok\". Ready must be HTTP 200 with
    status==\"ready\". When expected_sha is non-empty, /version must be HTTP
    200 and git_sha must match (full or unique prefix). Empty expected_sha
    skips the SHA gate so 4-arg callers and local runs stay health/ready only.
    """
    health_ok = health_status == 200 and _mapping(health_body).get("status") == "ok"
    ready_ok = ready_status == 200 and _mapping(ready_body).get("status") == "ready"
    if not (health_ok and ready_ok):
        return False
    want = (expected_sha or "").strip()
    if not want:
        return True
    if version_status != 200:
        return False
    live = _mapping(version_body).get("git_sha") or ""
    return git_sha_matches(live, want)


def wait_for_ready(
    timeout_s=None, interval_s=None, req_fn=None, sleeper=None, expected_sha=None
):
    """Poll /health, /ready, and (when SHA-gated) /version until ready.

    Returns True if the surface became ready. Records a DEAD check on timeout.
    ``req_fn`` / ``sleeper`` / ``expected_sha`` are injectable for tests.
    When ``expected_sha`` is None, GITHUB_SHA is read from the environment.
    """
    timeout = ready_wait_seconds() if timeout_s is None else timeout_s
    interval = ready_interval_seconds() if interval_s is None else interval_s
    probe = req if req_fn is None else req_fn
    pause = time.sleep if sleeper is None else sleeper
    want_sha = expected_git_sha(expected_sha)
    deadline = time.monotonic() + timeout
    attempt = 0
    last_h, last_r, last_v = (0, {}), (0, {}), (0, {})
    while True:
        attempt += 1
        last_h = probe("GET", "/health", retries=0)
        last_r = probe("GET", "/ready", retries=0)
        last_v = (0, {})
        if want_sha:
            last_v = probe("GET", "/version", retries=0)
        hs, hb = last_h
        rs, rb = last_r
        vs, vb = last_v
        if surface_is_ready(hs, hb, rs, rb, vs, vb, want_sha):
            extra = ""
            if want_sha:
                sha = _mapping(vb).get("git_sha") or ""
                extra = f" sha={sha[:12] if sha else None}"
            print(
                f"surface ready after {attempt} probe(s) "
                f"(health={hs} ready={rs}{extra})"
            )
            return True
        remaining = deadline - time.monotonic()
        health_status = (
            _mapping(hb).get("status") if not isinstance(hb, (bytes, bytearray)) else None
        )
        ready_status = (
            _mapping(rb).get("status") if not isinstance(rb, (bytes, bytearray)) else None
        )
        live_sha = _mapping(vb).get("git_sha") if want_sha else None
        sha_bit = ""
        if want_sha:
            sha_bit = f" sha={str(live_sha)[:12] if live_sha else None}"
        paths = "/health+/ready" + ("+/version" if want_sha else "")
        print(
            f"  waiting for {paths}: health={hs} health_status={health_status} "
            f"ready={rs} status={ready_status}{sha_bit} "
            f"remaining={max(0, int(remaining))}s"
        )
        if remaining <= 0:
            break
        pause(min(interval, remaining))
        if time.monotonic() >= deadline:
            break
    check(
        "health/ready wait",
        False,
        f"timeout {timeout}s last health={last_h[0]} ready={last_r[0]}"
        + (f" sha={(_mapping(last_v[1]).get('git_sha') or '')[:12] or None}" if want_sha else ""),
    )
    return False


def check(name, ok, evidence=""):
    tag = "LIVE" if ok else "DEAD"
    print(f"[{tag}] {name}" + (f" — {evidence}" if evidence else ""))
    if not ok:
        FAILURES.append(name)


def chat(sid, tok, msg, retries=4):
    last_err = None
    for attempt in range(retries + 1):
        rq = urllib.request.Request(
            BASE + f"/v1/sessions/{sid}/chat", method="POST",
            data=json.dumps({"message": msg}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"},
        )
        try:
            return urllib.request.urlopen(rq, timeout=300).read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in TRANSIENT and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return f"event: error\ndata: {e.code} {e.reason}\n\n"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return f"event: error\ndata: {type(e).__name__}\n\n"
    return f"event: error\ndata: {last_err}\n\n"


def verified_tokens():
    """Return (token, token_b) for verified smoke principals, or (None, None)."""
    gate = os.environ.get("SMOKE_GATE_TOKEN", "").strip()
    if gate:
        s, body = req(
            "POST", "/v1/auth/smoke-login", {},
            extra_headers={"X-Smoke-Gate": gate},
        )
        tok = (body or {}).get("login_token")
        tok_b = (body or {}).get("login_token_b")
        if s == 200 and tok:
            return tok, tok_b
        print(f"smoke-login http={s} (need SMOKE_GATE_TOKEN matching Render secret)")
        return None, None

    email = os.environ.get("SMOKE_EMAIL", "").strip()
    password = os.environ.get("SMOKE_PASSWORD", "").strip()
    if email and password:
        s, body = req("POST", "/v1/auth/login", {"email": email, "password": password})
        tok = (body or {}).get("login_token") if s == 200 else None
        if not tok or not (body or {}).get("email_verified"):
            print(f"SMOKE_EMAIL login http={s} verified={(body or {}).get('email_verified')}")
            return None, None
        tok_b = None
        email2 = os.environ.get("SMOKE_EMAIL_2", "").strip()
        password2 = os.environ.get("SMOKE_PASSWORD_2", "").strip()
        if email2 and password2:
            s2, b2 = req("POST", "/v1/auth/login", {"email": email2, "password": password2})
            if s2 == 200 and (b2 or {}).get("email_verified"):
                tok_b = (b2 or {}).get("login_token")
        return tok, tok_b

    return None, None


def record_unauthenticated_surface(health=None, ready=None, version=None):
    """LIVE/DEAD lines for the public ops surface (no principal)."""
    if health is None:
        health = req("GET", "/health")
    if ready is None:
        ready = req("GET", "/ready")
    if version is None:
        version = req("GET", "/version")
    hs, h = health
    h = _mapping(h)
    check("health endpoint", hs == 200 and h.get("status") == "ok", f"status={h.get('status')}")
    rs, r = ready
    r = _mapping(r)
    check(
        "ready endpoint",
        rs == 200 and r.get("status") == "ready",
        f"http={rs} status={r.get('status')}",
    )
    vs, v = version
    v = _mapping(v)
    sha = v.get("git_sha") or ""
    check("version endpoint", vs == 200 and bool(sha), f"http={vs} sha={sha[:12] if sha else None}")
    return hs, h, rs, r, vs, v


def main():
    global BASE
    BASE = resolve_base(sys.argv)
    print(f"post-deploy smoke against {BASE}\n")
    FAILURES.clear()

    if not wait_for_ready():
        return finish()

    hs, h, _rs, _r, _vs, _v = record_unauthenticated_surface()

    if not has_gated_credentials():
        emit_gated_skip_annotation()
        return finish()

    redis = h.get("redis") or {}
    check("redis rate limiting configured", bool(redis.get("configured") and redis.get("ok")), f"redis={redis}")

    email = f"smoke-{uuid.uuid4().hex[:8]}@factory.dev"
    s, r = req("POST", "/v1/auth/register", {"email": email, "password": "Smoke!23456"})
    check("auth register", s in (200, 201) and r.get("login_token"), f"http={s}")
    unverified = r.get("login_token")
    if not unverified:
        return finish()

    s_denied, denied = req("POST", "/v1/sessions/", {}, token=unverified)
    check(
        "unverified session denied",
        s_denied == 403 and (denied or {}).get("detail") == "email_not_verified",
        f"http={s_denied}",
    )

    tok, tok_b = verified_tokens()
    if not tok:
        check(
            "session create",
            False,
            "no verified principal (set SMOKE_GATE_TOKEN or SMOKE_EMAIL+SMOKE_PASSWORD)",
        )
        return finish()

    s, r = req("POST", "/v1/sessions/", {}, token=tok)
    check("session create", s in (200, 201) and r.get("session_id"), f"http={s}")
    sid = r.get("session_id")
    if not sid:
        return finish()

    # LLM drafting: off-table brief; fallback fingerprint = 2 caps, empty block_ids
    raw = chat(sid, tok, "Build me a vineyard management platform for a family winery: "
                         "track fermentation tanks, barrel inventory across two cellars, "
                         "harvest scheduling by sugar readings, and club member shipments.")
    check("chat blueprint event", "blueprint" in raw, f"sse_bytes={len(raw)}")
    s, d = req("GET", f"/v1/sessions/{sid}/product", token=tok)
    if not isinstance(d, dict):
        d = {}
    bp = d.get("blueprint") or {}
    caps = bp.get("capabilities") or []
    populated = [c for c in caps if c.get("block_ids")]
    drafting_ok = (
        s == 200
        and (bp.get("drafting_mode") == "architect_llm")
        and len(caps) >= 3
        and len(populated) >= 2
    )
    check("LLM drafting (not fallback)", drafting_ok,
          f"http={s} mode={bp.get('drafting_mode')} caps={len(caps)} populated={len(populated)} "
          f"(fallback fingerprint: caps=2 populated=0)")
    if not drafting_ok:
        return finish()

    raw2 = chat(sid, tok, "approve")
    check("approve -> generation event", "generation" in raw2)

    s, d = req("GET", f"/v1/sessions/{sid}/product", token=tok)
    gen = d.get("generation") or {}
    check("generation recorded", bool(gen.get("product_id")), f"product_id={gen.get('product_id')}")

    # The coding-agent runner builds in the background. A 409 here means
    # "still writing", not a dead kernel — poll until the ledger is terminal.
    # build-status nests the ledger under "build".
    s, blob = 0, b""
    build = {}
    deadline = time.time() + 900
    last_print = 0.0
    while time.time() < deadline:
        s, blob = req("GET", f"/v1/sessions/{sid}/product/package", token=tok, raw=True)
        st, status_body = req(
            "GET", f"/v1/sessions/{sid}/product/build-status", token=tok
        )
        payload = status_body if isinstance(status_body, dict) else {}
        nested = payload.get("build")
        build = nested if isinstance(nested, dict) else payload
        state = build.get("state")
        if s == 200:
            break
        if state in {"failed", "stalled"}:
            break
        if s != 409:
            break
        now = time.time()
        if now - last_print >= 30:
            print(
                f"  waiting for zip: http={s} build={state} "
                f"{build.get('phases_done')}/{build.get('phases_total')} "
                f"{(build.get('activity') or '')[:80]}"
            )
            last_print = now
        time.sleep(5)
    ok = s == 200 and isinstance(blob, (bytes, bytearray)) and blob[:2] == b"PK"
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist() if ok else []
    evidence = f"http={s} files={len(names)}"
    if build.get("state"):
        evidence += (
            f" build={build.get('state')} "
            f"{build.get('phases_done')}/{build.get('phases_total')}"
        )
    if s == 409:
        err = blob
        if isinstance(err, (bytes, bytearray)):
            try:
                err = json.loads(err)
            except Exception:
                err = {"raw": err[:200].decode(errors="replace")}
        if isinstance(err, dict) and err.get("detail"):
            evidence += f" detail={str(err.get('detail'))[:180]}"
    check("export zip", ok and len(names) > 5, evidence)

    raw3 = chat(sid, tok, "did you deploy my platform already? give me the URL")
    grounded = (("onrender.com" not in raw3 and "http" not in raw3.lower())
                or "don't have" in raw3 or "not" in raw3.lower())
    check("grounding (no invented deploy/URL)", grounded, raw3[:120].replace("\n", " "))

    # cross-account isolation — second verified principal, never the unverified register token
    if tok_b:
        s2, _ = req("GET", f"/v1/sessions/{sid}/product", token=tok_b)
        check("cross-account isolation", s2 == 404, f"http={s2} (expect 404)")
    else:
        check(
            "cross-account isolation",
            False,
            "no second verified principal (SMOKE_GATE_TOKEN or SMOKE_EMAIL_2)",
        )

    s, b = req("GET", "/v1/billing/status", token=tok)
    check("billing status structured", s == 200 and isinstance(b, dict), f"http={s}")
    s, c = req("POST", "/v1/billing/checkout", {}, token=tok)
    honest = s == 503 or (s == 200 and (c.get("url") or c.get("checkout_url")))
    check("billing checkout honest", honest, f"http={s} (503 stripe_not_configured or real url)")

    finish()


def finish():
    print()
    if FAILURES:
        print(f"SMOKE FAIL: {len(FAILURES)} dead kernel(s): {', '.join(FAILURES)}")
        sys.exit(1)
    if not has_gated_credentials():
        print("SMOKE PASS: unauthenticated surface only; gated checks skipped.")
        sys.exit(0)
    print("SMOKE PASS: every kernel live.")
    sys.exit(0)


if __name__ == "__main__":
    main()
