"""The foreman: the architect's second entry point -- it DIAGNOSES and INSTRUCTS.

After a gate fails and the runner rule has classified the failure as REWORK
(``RoleRunner.decide``), and after every hard stop has been checked (same
failure twice, the gate's budget, the build ceiling), the foreman reads the
typed findings, the dispatched brief, what changed in the writer's tree since
the last green phase and the ledger tail, and writes a typed
:class:`ReworkInstruction`. The WRITER's work list is then the foreman's
instructions instead of the raw findings.

It never decides a gate, never edits tests, gates, the floor file or Factory
code, and never extends a budget: it runs only after the budgets have already
allowed the round, and a ``stop`` recommendation (honoured only at
``confidence >= STOP_CONFIDENCE``) can only END the build earlier.

Code validates every instruction before it reaches the writer:

* schema -- every field typed, ``confidence`` in [0, 1], ``recommend`` one of
  ``continue`` / ``stop``, a ``stop`` names its reason;
* the ratchet -- ``affected_capability_ids`` are blueprint capabilities and,
  when the typed findings localised the failure, a subset of the capabilities
  they named; every instruction's ``capability_id`` is one of the affected
  ids. An instruction can narrow the round, never widen it;
* the lane -- every instruction's ``file`` is a path the WRITER may write
  (authority.py), and never a Factory-rendered or stamped file (the harness,
  the self-check, the stamped product suites, CI workflows);
* the text -- the architect lint (shape only: session ids, another product's
  literals, unresolved block ids, template slots, ``[check:]`` tags, length),
  plus: a probe or stage id (``probe_set.json``, data) or a path in the
  TESTER's lane (``tests/**``) -- an instruction aimed at one probe or one test
  is a per-case branch.

A refused instruction is regenerated ONCE with the refusal; a second refusal
falls back to the raw typed findings (the behaviour before the foreman) and
is recorded. ``FACTORY_FOREMAN`` = ``off`` (default) | ``shadow`` | ``on``:
shadow calls, validates and records, and leaves the work list untouched.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from app.factory.build import architect
from app.factory.build.findings import Finding

FLAG_ENV = "FACTORY_FOREMAN"
MODES = ("off", "shadow", "on")
ENTRY = "foreman"

#: A ``stop`` recommendation is honoured only at or above this confidence.
STOP_CONFIDENCE = 0.8
RECOMMEND_CONTINUE = "continue"
RECOMMEND_STOP = "stop"
RECOMMENDS = (RECOMMEND_CONTINUE, RECOMMEND_STOP)

#: Inputs the model reads are bounded so one call stays one call.
LEDGER_TAIL = 25
CHANGED_FILES_MAX = 60
BRIEF_CHARS_MAX = 12000
#: Each instruction field and the mechanism are capped (shape, not content).
FIELD_CHARS_MAX = 2000

#: Where proposed gates are written for a human to review (F4 uses the same
#: sink). Relative to the Factory's outputs root.
PROPOSED_GATES_DIR = ("artifacts", "proposed_gates")

LlmCall = architect.LlmCall


def foreman_mode() -> str:
    raw = str(os.getenv(FLAG_ENV, "") or "").strip().lower()
    return raw if raw in MODES else "off"


# -- the typed output -----------------------------------------------------------


@dataclass(frozen=True)
class Instruction:
    file: str
    what_to_build: str
    why: str
    capability_id: Optional[str] = None


@dataclass(frozen=True)
class ProposedGate:
    name: str
    structural_rule: str
    evidence: str


@dataclass(frozen=True)
class ReworkInstruction:
    mechanism: str
    affected_capability_ids: Tuple[str, ...]
    instructions: Tuple[Instruction, ...]
    confidence: float
    recommend: str
    stop_reason: Optional[str] = None
    proposed_gates: Tuple[ProposedGate, ...] = ()

    def to_json(self) -> Dict[str, Any]:
        return {
            "mechanism": self.mechanism,
            "affected_capability_ids": list(self.affected_capability_ids),
            "instructions": [asdict(i) for i in self.instructions],
            "confidence": self.confidence,
            "recommend": self.recommend,
            "stop_reason": self.stop_reason,
            "proposed_gates": [asdict(g) for g in self.proposed_gates],
        }


@dataclass
class ForemanResult:
    """What one foreman review decided. ``work`` is set only when it is used."""

    mode: str
    instruction: Optional[ReworkInstruction] = None
    work: Tuple[Any, ...] = ()
    used: bool = False
    fallback: bool = False
    stop: bool = False
    stop_reason: str = ""
    attempts: int = 0
    errors: List[str] = field(default_factory=list)


def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def parse_instruction(raw: Any) -> Tuple[Optional[ReworkInstruction], List[str]]:
    """The model's JSON as a :class:`ReworkInstruction`, or the schema errors."""
    errors: List[str] = []
    if not isinstance(raw, Mapping):
        return None, ["output is not an object"]
    mechanism = _text(raw.get("mechanism"))
    if not mechanism:
        errors.append("mechanism missing")
    affected_raw = raw.get("affected_capability_ids")
    if not isinstance(affected_raw, list) or not all(isinstance(c, str) and c for c in affected_raw):
        errors.append("affected_capability_ids must be a list of capability ids")
        affected_raw = []
    instructions: List[Instruction] = []
    items = raw.get("instructions")
    if not isinstance(items, list) or not items:
        errors.append("instructions must be a non-empty list")
        items = []
    for n, item in enumerate(items):
        if not isinstance(item, Mapping):
            errors.append(f"instruction {n} is not an object")
            continue
        file_, what, why = _text(item.get("file")), _text(item.get("what_to_build")), _text(item.get("why"))
        if not (file_ and what and why):
            errors.append(f"instruction {n} needs file, what_to_build and why")
            continue
        cap = item.get("capability_id")
        if cap is not None and not (isinstance(cap, str) and cap):
            errors.append(f"instruction {n} capability_id must be a capability id or null")
            continue
        instructions.append(Instruction(file_, what, why, cap or None))
    confidence = raw.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        errors.append("confidence must be a number in [0, 1]")
        confidence = 0.0
    recommend = _text(raw.get("recommend"))
    if recommend not in RECOMMENDS:
        errors.append("recommend must be continue or stop")
    stop_reason = raw.get("stop_reason")
    stop_reason = _text(stop_reason) or None
    if recommend == RECOMMEND_STOP and not stop_reason:
        errors.append("a stop recommendation must name its stop_reason")
    gates: List[ProposedGate] = []
    gates_raw = raw.get("proposed_gates") or []
    if not isinstance(gates_raw, list):
        errors.append("proposed_gates must be a list")
        gates_raw = []
    for n, gate in enumerate(gates_raw):
        if not isinstance(gate, Mapping):
            errors.append(f"proposed gate {n} is not an object")
            continue
        name, rule, evidence = _text(gate.get("name")), _text(gate.get("structural_rule")), _text(gate.get("evidence"))
        if not (name and rule and evidence):
            errors.append(f"proposed gate {n} needs name, structural_rule and evidence")
            continue
        gates.append(ProposedGate(name, rule, evidence))
    if errors:
        return None, errors
    return (
        ReworkInstruction(
            mechanism=mechanism,
            affected_capability_ids=tuple(str(c) for c in affected_raw),
            instructions=tuple(instructions),
            confidence=float(confidence),
            recommend=recommend,
            stop_reason=stop_reason,
            proposed_gates=tuple(gates),
        ),
        [],
    )


# -- validation (code) ------------------------------------------------------------


def protected_paths() -> FrozenSet[str]:
    """Files the Factory renders or stamps: never an instruction's target."""
    from app.factory.build.product_suites import PRODUCT_SUITES
    from app.factory.build.store_acceptance import factory_rendered_paths
    from app.factory.build.writer_behaviour import SELF_CHECK_REL

    return frozenset(
        str(p).replace("\\", "/") for p in (*factory_rendered_paths(), SELF_CHECK_REL, *PRODUCT_SUITES)
    )


def _in_lanes(rel: str, lanes: Iterable[Tuple[Any, str]]) -> bool:
    from pathlib import PurePosixPath

    from app.factory.build.authority import LaneRoot, _matches_lane

    path = PurePosixPath(rel)
    return any(root is LaneRoot.WORKSPACE and _matches_lane(path, glob) for root, glob in lanes)


def file_refusal(rel: str) -> Optional[str]:
    """Why an instruction may not target ``rel``, or None when it may."""
    from app.factory.build.authority import FORBIDDEN_SEGMENTS, BuildRole, role_contract

    norm = str(rel or "").replace("\\", "/").strip()
    parts = [p for p in norm.split("/") if p]
    if not parts or norm.startswith("/") or ".." in parts or (":" in parts[0]):
        return f"{rel!r} is not a path inside the workspace"
    if FORBIDDEN_SEGMENTS.intersection(parts):
        return f"{rel!r} is inside a version-control directory"
    clean = "/".join(parts)
    if clean in protected_paths():
        return f"{clean} is Factory-rendered (the harness, a stamped suite or a workflow)"
    if _in_lanes(clean, role_contract(BuildRole.TESTER).write_lanes):
        return f"{clean} is a test file (the TESTER's lane)"
    if not _in_lanes(clean, role_contract(BuildRole.WRITER).write_lanes):
        return f"{clean} is outside the WRITER's lane"
    return None


def _tokens(text: str) -> Set[str]:
    """Identifier/path-shaped tokens, split on characters (no pattern)."""
    out: Set[str] = set()
    word: List[str] = []
    for ch in str(text or "") + " ":
        if ch.isalnum() or ch in "_-./":
            word.append(ch)
            continue
        if word:
            out.add("".join(word).strip("./-"))
            word = []
    return {t for t in out if t}


def _probe_ids() -> FrozenSet[str]:
    from app.factory.build import probe_set

    try:
        return frozenset(set(probe_set.defects()) | set(probe_set.stage_ids()))
    except Exception:  # noqa: BLE001 -- no probe registry, nothing to refuse by it
        return frozenset()


@dataclass(frozen=True)
class ForemanContext:
    """What validation needs to know about this build (all data)."""

    capabilities: FrozenSet[str]
    finding_capabilities: FrozenSet[str]
    resolved_blocks: FrozenSet[str]
    store_blocks: FrozenSet[str]
    own_names: FrozenSet[str]
    known_literals: Optional[FrozenSet[str]] = None


def validate(ri: ReworkInstruction, ctx: ForemanContext) -> List[str]:
    """Every reason this instruction may not reach the writer (empty = ok)."""
    errors: List[str] = []
    affected = set(ri.affected_capability_ids)
    outside = sorted(affected - set(ctx.capabilities))
    if outside:
        errors.append("affected capability outside the blueprint: " + ", ".join(outside))
    if ctx.finding_capabilities:
        widened = sorted(affected - set(ctx.finding_capabilities))
        if widened:
            errors.append(
                "widens the ratchet beyond the capabilities the findings named: " + ", ".join(widened)
            )
    probes = _probe_ids()
    texts = [ri.mechanism] + [f"{i.what_to_build}\n{i.why}" for i in ri.instructions]
    for n, ins in enumerate(ri.instructions):
        refused = file_refusal(ins.file)
        if refused:
            errors.append(f"instruction {n}: {refused}")
        if ins.capability_id and ins.capability_id not in affected:
            errors.append(f"instruction {n}: capability {ins.capability_id} is not an affected capability")
        if ctx.finding_capabilities and not ins.capability_id:
            errors.append(f"instruction {n}: names no capability while the findings were localised")
        for value in (ins.what_to_build, ins.why):
            if len(value) > FIELD_CHARS_MAX:
                errors.append(f"instruction {n}: a field is over the {FIELD_CHARS_MAX}-character cap")
    if len(ri.mechanism) > FIELD_CHARS_MAX:
        errors.append(f"mechanism is over the {FIELD_CHARS_MAX}-character cap")
    tester_lanes = None
    for text in texts:
        toks = _tokens(text)
        named_probes = sorted(toks & probes)
        if named_probes:
            errors.append("names a probe or stage id (a per-case branch): " + ", ".join(named_probes[:4]))
        if tester_lanes is None:
            from app.factory.build.authority import BuildRole, role_contract

            tester_lanes = role_contract(BuildRole.TESTER).write_lanes
        tests = sorted(t for t in toks if "/" in t and _in_lanes(t, tester_lanes))
        if tests:
            errors.append("points at a test file (code to the product, not the test): " + ", ".join(tests[:4]))
    lint = architect.lint_architect_text(
        "\n".join(texts),
        resolved_blocks=set(ctx.resolved_blocks),
        store_blocks=set(ctx.store_blocks),
        capabilities=set(ctx.capabilities),
        own_names=set(ctx.own_names),
        declared_capabilities=list(affected),
        known_literals=ctx.known_literals,
        max_chars=FIELD_CHARS_MAX * (1 + 2 * max(1, len(ri.instructions))),
    )
    errors.extend(lint.errors)
    return list(dict.fromkeys(errors))


# -- the inputs ---------------------------------------------------------------------


def _last_green_at(ledger: Any) -> Optional[str]:
    from app.factory.build.ledger import EventKind

    last = None
    try:
        for event in ledger.events():
            if event.kind is EventKind.GATE_PASSED:
                last = getattr(event, "ts", None)
    except Exception:  # noqa: BLE001 -- an unreadable ledger has no green phase
        return None
    return last


def changed_files(workspace: Path, since_iso: Optional[str]) -> List[str]:
    """Writer-lane files modified since the last green phase (names only)."""
    from datetime import datetime

    from app.factory.build.authority import BuildRole, role_contract

    lanes = role_contract(BuildRole.WRITER).write_lanes
    cutoff = None
    if since_iso:
        try:
            cutoff = datetime.fromisoformat(str(since_iso).replace("Z", "+00:00")).timestamp()
        except ValueError:
            cutoff = None
    out: List[str] = []
    root = Path(workspace)
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if not _in_lanes(rel, lanes) or rel in protected_paths():
            continue
        try:
            if cutoff is not None and path.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        out.append(rel)
        if len(out) >= CHANGED_FILES_MAX:
            break
    return out


def ledger_tail(ledger: Any, n: int = LEDGER_TAIL) -> List[Dict[str, Any]]:
    try:
        events = list(ledger.events())[-n:]
    except Exception:  # noqa: BLE001
        return []
    return [
        {
            "kind": getattr(getattr(e, "kind", None), "value", str(getattr(e, "kind", ""))),
            "role": getattr(getattr(e, "role", None), "value", None),
            "detail": str(getattr(e, "detail", "") or "")[:300],
        }
        for e in events
    ]


def foreman_inputs(
    findings: Sequence[Any],
    *,
    brief_text: str,
    changed: Sequence[str],
    tail: Sequence[Mapping[str, Any]],
    capabilities: Sequence[str],
    gate: str,
    check: str,
) -> Dict[str, Any]:
    return {
        "gate": gate,
        "check": check,
        "findings": [f.to_json() if isinstance(f, Finding) else {"detail": str(f)} for f in findings],
        "capabilities": sorted(capabilities),
        "brief": str(brief_text or "")[:BRIEF_CHARS_MAX],
        "changed_since_last_green": list(changed),
        "ledger_tail": list(tail),
    }


def _messages(inputs: Mapping[str, Any], refusals: Sequence[str]) -> List[Dict[str, str]]:
    system = (
        "You are the foreman of a coding build. A gate failed; the findings below are "
        "typed. Diagnose the MECHANISM behind them and instruct the coder what to build. "
        "You never change a gate, a test, the acceptance floor or Factory files, and you "
        "never ask for a special case: describe the general behaviour the product must "
        "have so every input of this shape works. Each instruction names one product "
        "file the coder owns, what to build there and why, and the capability it serves "
        "(only capabilities the findings named, when they named any). Reply as JSON: "
        '{"mechanism": "...", "affected_capability_ids": [...], "instructions": '
        '[{"file": "...", "what_to_build": "...", "why": "...", "capability_id": "..."}], '
        '"confidence": 0.0-1.0, "recommend": "continue" | "stop", "stop_reason": null | "...", '
        '"proposed_gates": [{"name": "...", "structural_rule": "...", "evidence": "..."}]}. '
        "Recommend stop only when another round cannot fix it, and say why."
    )
    msgs = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(inputs, sort_keys=True, default=str)},
    ]
    if refusals:
        msgs.append(
            {
                "role": "user",
                "content": "Your previous instruction was refused: "
                + "; ".join(refusals)
                + ". Write it again without those defects.",
            }
        )
    return msgs


# -- the work it hands the writer ------------------------------------------------------


def instruction_work(ri: ReworkInstruction, *, gate: str, check: str) -> Tuple[Finding, ...]:
    """Each instruction as a typed work item: the capability it serves is a
    field, so the writer's ratchet reads it exactly as it reads a finding."""
    return tuple(
        Finding(
            f"[{check}] {ins.file}: {ins.what_to_build} -- why: {ins.why}",
            gate=ENTRY,
            check_id=check,
            capability_id=ins.capability_id,
            file=ins.file,
            finding_shape="instruction",
        )
        for ins in ri.instructions
    )


def write_proposed_gates(session_id: str, ri: ReworkInstruction, *, root: Optional[Path] = None) -> Optional[Path]:
    """Append this call's proposed gates to artifacts/proposed_gates/<session>.json.
    A suggestion for a human to accept into the floor -- never a verdict."""
    if not ri.proposed_gates:
        return None
    if root is None:
        from app.factory.paths import factory_outputs_root

        root = factory_outputs_root()
    name = "".join(ch for ch in str(session_id or "unscoped") if ch.isalnum() or ch in "_-") or "unscoped"
    path = Path(root).joinpath(*PROPOSED_GATES_DIR, f"{name}.json")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        if not isinstance(existing, list):
            existing = []
        existing.extend(asdict(g) | {"source": ENTRY} for g in ri.proposed_gates)
        path.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
    except (OSError, ValueError):
        return None
    return path


# -- the entry point ---------------------------------------------------------------------


def review(
    findings: Sequence[Any],
    *,
    ctx: ForemanContext,
    gate: str,
    check: str,
    brief_text: str = "",
    changed: Sequence[str] = (),
    tail: Sequence[Mapping[str, Any]] = (),
    note: Optional[Callable[..., None]] = None,
    llm: Optional[LlmCall] = None,
    mode: Optional[str] = None,
    session_id: str = "",
    proposed_root: Optional[Path] = None,
) -> ForemanResult:
    """Call, validate (twice at most) and record the foreman's rework instruction."""
    active = mode if mode in MODES else foreman_mode()
    result = ForemanResult(mode=active)
    if active == "off":
        return result
    inputs = foreman_inputs(
        findings,
        brief_text=brief_text,
        changed=changed,
        tail=tail,
        capabilities=sorted(ctx.capabilities),
        gate=gate,
        check=check,
    )
    model = architect.architect_model()
    refusals: List[str] = []
    for attempt in (1, 2):
        result.attempts = attempt
        tokens = None
        output = ""
        try:
            raw = architect.architect_call(_messages(inputs, refusals), llm=llm)
            tokens = raw.get("_tokens") if isinstance(raw.get("_tokens"), int) else None
            output = json.dumps({k: v for k, v in raw.items() if k != "_tokens"}, sort_keys=True, default=str)
            ri, errors = parse_instruction({k: v for k, v in raw.items() if k != "_tokens"})
            if ri is not None:
                errors = validate(ri, ctx)
        except Exception as exc:  # noqa: BLE001 -- the model failing is a fallback, not a crash
            ri, errors = None, [f"foreman call failed: {type(exc).__name__}"]
        lint = architect.ArchitectLint(ok=not errors, errors=list(errors))
        last = attempt == 2 or lint.ok
        architect.record_architect_call(
            note,
            entry=ENTRY,
            inputs=inputs,
            output=output,
            lint=lint,
            fallback=bool(last and not lint.ok),
            mode=active,
            attempt=attempt,
            model=model,
            tokens=tokens,
            fallback_to="the raw typed findings",
        )
        if lint.ok and ri is not None:
            result.instruction = ri
            write_proposed_gates(session_id, ri, root=proposed_root)
            if ri.recommend == RECOMMEND_STOP and ri.confidence >= STOP_CONFIDENCE and active == "on":
                result.stop = True
                result.stop_reason = str(ri.stop_reason or "")
                return result
            if active == "on":
                result.work = instruction_work(ri, gate=gate, check=check)
                result.used = True
            return result
        refusals = list(errors)
        result.errors = list(errors)
    result.fallback = True
    return result
