"""SSE tracer for cerebrum_cli.

Targets the canonical envelope: ``data:`` lines with JSON ``type`` in
{route, start, token, tool_call, tool_result, sources, heartbeat, end, error}.

NOTE: CerebrumDev's configurator router currently uses a different named
event:/data: format and deployed instances may return a single JSON blob. The
CLI defensively handles a non-streaming single-JSON response: if the response is
not ``text/event-stream``, it prints the JSON body and notes that streaming
isn't enabled on this instance. A follow-up PR (Spec 2) standardizes the
servers on the canonical type-envelope.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

import httpx


HANG_THRESHOLD_SECONDS = 5.0


def _fmt_delta(t0: float) -> str:
    return f"{time.monotonic() - t0:7.2f}s"


def _is_sse(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "")
    return "text/event-stream" in content_type


def stream_turn(
    client: httpx.Client,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    *,
    raw: bool = False,
    events: bool = False,
) -> None:
    """Run one chat turn, print SSE events + timing, return nothing.

    If the response is not an event stream, prints the JSON body and a note.
    """
    t0 = time.monotonic()
    first_token_at: float | None = None
    tool_timings: list[tuple[str, float]] = []
    pending_tool: tuple[str, float] | None = None
    answer_parts: list[str] = []
    n_events = 0

    with client.stream(
        "POST", url, json=body, headers=headers, timeout=httpx.Timeout(10, read=600)
    ) as response:
        if response.status_code != 200:
            response.read()
            print(f"HTTP {response.status_code}: {response.text[:500]}", file=sys.stderr)
            return

        if not _is_sse(response):
            response.read()
            try:
                payload = response.json()
            except json.JSONDecodeError:
                print(response.text[:2000])
                return
            print(json.dumps(payload, indent=2, default=str)[:8000])
            print(
                "\n[note] response was not text/event-stream; "
                "streaming is not enabled on this instance.",
                file=sys.stderr,
            )
            return

        for line in response.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            n_events += 1
            payload = line[5:].strip()
            if raw:
                print(f"[{_fmt_delta(t0)}] {payload}")
                continue
            try:
                evt = json.loads(payload)
            except json.JSONDecodeError:
                print(f"[{_fmt_delta(t0)}] ?? unparseable: {payload[:200]}")
                continue

            etype = evt.get("type", "?")

            if etype == "token":
                if first_token_at is None:
                    first_token_at = time.monotonic() - t0
                    if events:
                        print(f"[{_fmt_delta(t0)}] FIRST TOKEN")
                answer_parts.append(evt.get("content", ""))
                if not events:
                    print(evt.get("content", ""), end="", flush=True)
            elif etype == "tool_call":
                name = evt.get("name") or evt.get("tool") or "?"
                pending_tool = (name, time.monotonic())
                argstr = json.dumps(evt.get("arguments") or evt.get("args") or {})
                print(f"\n[{_fmt_delta(t0)}] TOOL_CALL   {name}  args={argstr[:300]}")
            elif etype == "tool_result":
                name = evt.get("name") or evt.get("tool") or "?"
                dur: float | None = None
                if pending_tool and pending_tool[0] == name:
                    dur = time.monotonic() - pending_tool[1]
                    tool_timings.append((name, dur))
                    pending_tool = None
                size = len(json.dumps(evt, default=str))
                status = evt.get("status", "?")
                dtxt = f" ({dur:.2f}s)" if dur is not None else ""
                print(f"[{_fmt_delta(t0)}] TOOL_RESULT {name}{dtxt}  status={status}  ~{size}B")
            elif etype == "heartbeat":
                if events:
                    print(f"[{_fmt_delta(t0)}] heartbeat")
            elif etype in ("route", "start", "sources", "end", "error"):
                keep = {k: v for k, v in evt.items() if k != "type"}
                brief = json.dumps(keep, default=str)
                print(f"\n[{_fmt_delta(t0)}] {etype.upper():11s} {brief[:600]}")
                if etype in ("end", "error"):
                    break
            else:
                print(f"[{_fmt_delta(t0)}] {etype}: {json.dumps(evt, default=str)[:300]}")

    total = time.monotonic() - t0
    print(f"\n{'─' * 60}")
    first_token_txt = "-" if first_token_at is None else f"{first_token_at:.2f}s"
    print(
        f"turn summary: total={total:.2f}s  first_token={first_token_txt}  "
        f"events={n_events}  answer_chars={sum(len(p) for p in answer_parts)}"
    )
    for name, dur in tool_timings:
        print(f"  tool {name}: {dur:.2f}s")
    if first_token_at is None and total > HANG_THRESHOLD_SECONDS:
        print("  !! no tokens streamed — this is the empty-bubble/hang shape")
