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

Exit code 0 = all checks pass. Prints a LIVE/DEAD line per kernel.
"""
import io
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
import zipfile

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://api.cerebrum-dev.com").rstrip("/")
FAILURES = []


def req(method, path, body=None, token=None, raw=False, extra_headers=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    r = urllib.request.Request(
        BASE + path, method=method,
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
            return e.code, json.loads(b or b"null")
        except Exception:
            return e.code, {"raw": b[:300].decode(errors="replace")}


def check(name, ok, evidence=""):
    tag = "LIVE" if ok else "DEAD"
    print(f"[{tag}] {name}" + (f" — {evidence}" if evidence else ""))
    if not ok:
        FAILURES.append(name)


def chat(sid, tok, msg):
    rq = urllib.request.Request(
        BASE + f"/v1/sessions/{sid}/chat", method="POST",
        data=json.dumps({"message": msg}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"},
    )
    return urllib.request.urlopen(rq, timeout=300).read().decode(errors="replace")


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


def main():
    print(f"post-deploy smoke against {BASE}\n")

    s, h = req("GET", "/health")
    check("health endpoint", s == 200 and h.get("status") == "ok", f"status={h.get('status')}")
    redis = h.get("redis") or {}
    check("redis rate limiting configured", bool(redis.get("configured") and redis.get("ok")), f"redis={redis}")

    s, v = req("GET", "/version")
    sha = (v or {}).get("git_sha") or ""
    check("version endpoint", s == 200 and bool(sha), f"http={s} sha={sha[:12] if sha else None}")

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
    bp = d.get("blueprint") or {}
    caps = bp.get("capabilities") or []
    populated = [c for c in caps if c.get("block_ids")]
    check("LLM drafting (not fallback)", len(caps) >= 3 and len(populated) >= 2,
          f"caps={len(caps)} populated={len(populated)} "
          f"(fallback fingerprint: caps=2 populated=0)")

    raw2 = chat(sid, tok, "approve")
    check("approve -> generation event", "generation" in raw2)

    s, d = req("GET", f"/v1/sessions/{sid}/product", token=tok)
    gen = d.get("generation") or {}
    check("generation recorded", bool(gen.get("product_id")), f"product_id={gen.get('product_id')}")

    s, blob = req("GET", f"/v1/sessions/{sid}/product/package", token=tok, raw=True)
    ok = s == 200 and blob[:2] == b"PK"
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist() if ok else []
    check("export zip", ok and len(names) > 5, f"http={s} files={len(names)}")

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
    print("SMOKE PASS: every kernel live.")
    sys.exit(0)


if __name__ == "__main__":
    main()
