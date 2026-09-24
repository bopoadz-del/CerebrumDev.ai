"""The universal reasoning socket the Factory emits into every platform it builds.

One kernel, byte-identical in every product. The KIT is data the kernel loads, so
a hotel build and an airport build get the same socket and differ only in the
vocabulary vendored beside it. Nothing in the kernel knows what a runway or a
slab is -- that is the whole point, and it is why there is one file here rather
than one per vertical.

Emitted into a product:

    app/reasoning/kernel.py        the socket. Identical everywhere.
    app/reasoning/host.py          the four functions the PRODUCT fills in
    app/reasoning/kit/manifest.yaml     vendored from the Store
    app/reasoning/kit/invariants.yaml   vendored from the Store
    app/reasoning/pending.py       the unanswered interview questions

Five hook points, from the portable spec's routing map:

    H0 pre-retrieval   refuse without searching, and assert nothing was searched
    H1 ranking         lift the governing source class
    H2 tool-time       catch impossible values where they are born
    H3 answer-time     grounding, qualifier, provenance, currency, derivation
    H4 export-time     re-run H3 on the file before it leaves

FAIL CLOSED, twice over:

  * a kit that does not load disables itself and REFUSES every statement. It
    never degrades to "no invariants", because a kit with a typo becoming a kit
    with no gates is the failure this whole layer exists to prevent.
  * a host function the product has not implemented REFUSES. The emitted
    ``host.py`` raises ``HostNotWired`` rather than returning something empty,
    so a platform that forgot to wire retrieval cannot quietly answer ungated.

UNANSWERED FIGURES ARE NOT STUBS. A kit ships with every figure value null and
each null carrying the interview question that fills it. When the build interview
does not get an answer, the figure stays null and the platform REFUSES any
question that needs it, naming the interview question the operator must go and
answer. A stub would be a fake value that can ship silently and be mistaken for
real; a refusal cannot. An answer is stored with who gave it, when, and from
which document -- otherwise the interview has only moved the invented figure from
the model to the operator.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

#: Where the socket lives inside a built product.
KERNEL_PATH = "app/reasoning/kernel.py"
HOST_PATH = "app/reasoning/host.py"
PENDING_PATH = "app/reasoning/pending.py"
ROUTES_PATH = "app/reasoning/routes.py"
INIT_PATH = "app/reasoning/__init__.py"
KIT_MANIFEST = "app/reasoning/kit/manifest.yaml"
KIT_INVARIANTS = "app/reasoning/kit/invariants.yaml"


def render_init() -> str:
    return '''"""The reasoning layer: a universal kernel plus one vendored kit.

The kernel is identical in every platform the Factory builds. The kit beside it
is the vocabulary for this platform's vertical. See kernel.py.
"""

from app.reasoning.kernel import (  # noqa: F401
    Figure,
    Finding,
    Outcome,
    ReasoningKernel,
    kernel,
)
'''


def render_kernel() -> str:
    """The socket. Identical in every product -- do not specialise it per build."""
    return '''"""The reasoning kernel. Identical in every platform; the kit is the difference.

A platform answers with FIGURES. A figure is usable only if it is grounded,
complete, dimensioned, authorised, its own, current, in scope and
self-consistent. Each of those is one invariant kind, declared in the kit beside
this file and evaluated here.

FAIL CLOSED. A kit that does not load refuses every statement. A host function
the product has not wired refuses. Neither ever returns "fine".
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

logger = logging.getLogger(__name__)

KIT_DIR = pathlib.Path(__file__).resolve().parent / "kit"

H0_PRE_RETRIEVAL = "H0"
H1_RANKING = "H1"
H2_TOOL_TIME = "H2"
H3_ANSWER_TIME = "H3"
H4_EXPORT_TIME = "H4"
ALL_HOOKS = (H0_PRE_RETRIEVAL, H1_RANKING, H2_TOOL_TIME, H3_ANSWER_TIME, H4_EXPORT_TIME)

HOOKS_BY_KIND = {
    "scope": (H0_PRE_RETRIEVAL,),
    "authority": (H1_RANKING,),
    "unit_discipline": (H2_TOOL_TIME, H3_ANSWER_TIME),
    "band": (H2_TOOL_TIME,),
    "grounding": (H3_ANSWER_TIME,),
    "qualifier": (H3_ANSWER_TIME,),
    "provenance": (H3_ANSWER_TIME,),
    "currency": (H3_ANSWER_TIME,),
    "derivation": (H3_ANSWER_TIME,),
}

#: Hard cap on figure x invariant evaluations per hook. An unbounded set in
#: exactly this position consumed 2 GB and killed a live instance twice in one
#: day. A skipped check is NOT a pass and Outcome.incomplete says so.
BUDGET = 2000


class KitDisabled(RuntimeError):
    """The kit did not load. Every statement is refused."""


class HostNotWired(RuntimeError):
    """A host function this platform must implement is missing."""


@dataclass
class Figure:
    quantity: str
    value: Any = None
    unit: Optional[str] = None
    origin: str = "model"          # tool | document | model | operator
    source_id: Optional[str] = None
    source_class: Optional[str] = None
    revision: Optional[str] = None
    qualifiers: Dict[str, Any] = field(default_factory=dict)
    claim_class: Optional[str] = None
    asked_about: Dict[str, Any] = field(default_factory=dict)
    derivations: List[str] = field(default_factory=list)
    conditions: List[str] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)
    bounds: Dict[str, Any] = field(default_factory=dict)
    span: Optional[Dict[str, int]] = None
    text: str = ""

    def known(self, name: str) -> bool:
        """A key present with None counts as NOT known: having a field for it is
        not knowing it."""
        return self.qualifiers.get(name) is not None

    def missing(self, names: Sequence[str]) -> List[str]:
        return [n for n in names if not self.known(n)]

    @property
    def is_operators_own(self) -> bool:
        return self.origin == "operator"


@dataclass
class Finding:
    invariant: str
    kind: str
    severity: str
    message: str
    quantity: str = ""
    missing: List[str] = field(default_factory=list)
    span: Optional[Dict[str, int]] = None
    softened_from: Optional[str] = None

    @property
    def stops(self) -> bool:
        return self.severity == "refuse"

    def as_dict(self) -> Dict[str, Any]:
        out = {"invariant": self.invariant, "kind": self.kind,
               "severity": self.severity, "message": self.message}
        if self.quantity:
            out["quantity"] = self.quantity
        if self.missing:
            out["missing"] = list(self.missing)
        if self.span:
            out["span"] = dict(self.span)
        if self.softened_from:
            out["softened_from"] = self.softened_from
        return out


@dataclass
class Outcome:
    verdict: str
    hook: str
    findings: List[Finding] = field(default_factory=list)
    incomplete: bool = False
    skipped: int = 0
    retrieval_permitted: bool = True

    @property
    def blocked_reason(self) -> str:
        return "; ".join(f.message for f in self.findings if f.stops)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "hook": self.hook,
            "findings": [f.as_dict() for f in self.findings],
            "blocked_reason": self.blocked_reason,
            "retrieval_permitted": self.retrieval_permitted,
            "incomplete": self.incomplete,
            "skipped_checks": self.skipped,
        }


def _verdict(findings: Sequence[Finding]) -> str:
    if any(f.stops for f in findings):
        return "refused"
    if any(f.severity == "flag" for f in findings):
        return "flagged"
    return "annotated" if findings else "pass"


class ReasoningKernel:
    """Loads the kit beside this file and runs the routing map over figures."""

    def __init__(self, kit_dir: Optional[pathlib.Path] = None) -> None:
        self.kit_dir = pathlib.Path(kit_dir or KIT_DIR)
        self.disabled_reason: Optional[str] = None
        self.manifest: Dict[str, Any] = {}
        self.invariants: List[Dict[str, Any]] = []
        self._load()

    # -- loading -----------------------------------------------------------

    def _load(self) -> None:
        name = self.kit_dir.name
        switch = os.getenv(f"REASONING_KIT_OFF", "").strip().lower()
        if switch in ("1", "true", "yes", "on"):
            self.disabled_reason = "the reasoning kit is switched off by REASONING_KIT_OFF"
            logger.error("REASONING KIT DISABLED: %s", self.disabled_reason)
            return
        try:
            self.manifest = yaml.safe_load(
                (self.kit_dir / "manifest.yaml").read_text(encoding="utf-8")) or {}
            records = yaml.safe_load(
                (self.kit_dir / "invariants.yaml").read_text(encoding="utf-8")) or {}
            self.invariants = list(records.get("invariants") or [])
        except Exception as exc:  # noqa: BLE001 -- any failure disables the kit
            self.disabled_reason = f"kit did not load: {type(exc).__name__}: {exc}"
            logger.error("REASONING KIT DISABLED: %s", self.disabled_reason)
            return
        if not self.invariants:
            self.disabled_reason = (
                "the kit declares no invariants; it gates nothing and is disabled "
                "rather than trusted")
            logger.error("REASONING KIT DISABLED: %s", self.disabled_reason)

    @property
    def enabled(self) -> bool:
        return self.disabled_reason is None

    def _disabled_outcome(self, hook: str) -> Outcome:
        finding = Finding(
            "KIT_DISABLED", "scope", "refuse",
            f"the reasoning kit is DISABLED and cannot gate anything: "
            f"{self.disabled_reason}. A kit that does not load refuses; it does not "
            f"pass statements through with no invariants")
        return Outcome("refused", hook, [finding], retrieval_permitted=False)

    # -- the answer store --------------------------------------------------
    #
    # Answers are THIS PLATFORM'S data and live in this platform's storage. They
    # are never written into the kit, because the kit is a signed Store block
    # shared by every customer: one client's declared distances reaching the next
    # platform built from the same kit would be the provenance failure this whole
    # layer exists to prevent, arriving signed.

    def _answers_path(self) -> pathlib.Path:
        root = os.getenv("STORAGE_PATH") or "."
        return pathlib.Path(root) / "reasoning_answers.json"

    def answers(self) -> Dict[str, Dict[str, Any]]:
        """What this platform has been told, read fresh.

        Read from disk every time rather than cached: an answer posted by one
        worker must be visible to the next request on another, and a cache here
        would make a recorded answer look unrecorded.
        """
        path = self._answers_path()
        if not path.is_file():
            return {}
        try:
            import json

            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.error("reasoning answers unreadable at %s; treating as none", path)
            return {}
        return {str(k): v for k, v in (data or {}).items() if isinstance(v, dict)}

    def record_answer(self, figure: str, value: Any, *, answered_by: str,
                      answered_at: str, source: str) -> Dict[str, Any]:
        """Persist one answer, then READ IT BACK before reporting success.

        The read-back is the point. An earlier version validated the provenance,
        returned the dict and stored nothing, so the route answered ok:true for a
        no-op -- a success report over an operation that did nothing, which is the
        defect class this layer exists to catch.
        """
        if not (answered_by and answered_at and source):
            raise ValueError(
                "an interview answer needs answered_by, answered_at and source: an "
                "unattributed figure is not evidence, whoever supplied it")
        if figure not in (self.manifest.get("figures") or {}):
            raise KeyError(f"{figure} is not a figure this kit asks about")

        record = {
            "value": value,
            "answered_by": answered_by,
            "answered_at": answered_at,
            "source": source,
        }
        import json
        import os as _os
        import tempfile as _tempfile

        path = self._answers_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        current = self.answers()
        current[figure] = record
        # Atomic: a crash mid-write must not leave a half-written answer file that
        # reads as "no answers at all".
        handle, temporary = _tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with _os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(current, stream, indent=2, sort_keys=True)
            _os.replace(temporary, path)
        except BaseException:
            _os.unlink(temporary)
            raise

        stored = self.answers().get(figure)
        if not stored or stored.get("value") != value:
            raise RuntimeError(
                f"{figure} did not persist: the answer is not readable back, so it "
                f"has not been recorded and must not be reported as recorded")
        return dict(stored, figure=figure)

    # -- the unanswered interview -----------------------------------------

    def pending_questions(self) -> Dict[str, str]:
        """Figures the build interview has not answered, and the question for each.

        These are not stubs. A figure with no answer has no value, and any
        statement needing it is REFUSED naming the question -- so the gap is
        visible to the operator instead of shipping as a plausible number.
        """
        out: Dict[str, str] = {}
        answered = self.answers()
        for name, entry in (self.manifest.get("figures") or {}).items():
            if str(name) in answered:
                continue
            if not isinstance(entry, dict) or entry.get("value") is None:
                question = ""
                if isinstance(entry, dict):
                    question = str(entry.get("question") or entry.get("interview_id") or "")
                out[str(name)] = question or "unanswered: no interview question recorded"
        return out

    def figure_value(self, name: str) -> Tuple[Any, Optional[str]]:
        """``(value, refusal)``. A null figure yields a refusal, never a default.

        An answer this platform has been given wins over the kit's null, which is
        how one answer takes effect the moment it lands instead of waiting for
        some whole-kit swap.
        """
        answered = self.answers().get(name)
        if answered and answered.get("value") is not None:
            return answered["value"], None
        entry = (self.manifest.get("figures") or {}).get(name)
        if not isinstance(entry, dict) or entry.get("value") is None:
            question = self.pending_questions().get(name, "")
            return None, (
                f"{name} has no value on this platform: {question}. The build "
                f"interview did not answer it, so nothing can be stated about it")
        return entry["value"], None

    # -- the hooks ---------------------------------------------------------

    def pre_retrieval(self, question: str) -> Outcome:
        """H0. Runs in the router BEFORE anything is retrieved.

        No document makes an operational-authority question answerable, so
        retrieving at all is wrong: it produces citations that read as though
        they authorised the decision.
        """
        if not self.enabled:
            return self._disabled_outcome(H0_PRE_RETRIEVAL)
        for refusal in (self.manifest.get("scope_refusals") or []):
            pattern = str(refusal.get("pattern") or "")
            if pattern and re.search(pattern, question or "", re.IGNORECASE):
                label = str(refusal.get("label") or "operational authority")
                who = str(refusal.get("authority") or "the named duty holder")
                finding = Finding(
                    f"SCOPE:{label}", "scope", "refuse",
                    f"refused before retrieval ({label}): this is {who}'s decision. "
                    f"No document makes it answerable, so nothing was retrieved")
                return Outcome("refused", H0_PRE_RETRIEVAL, [finding],
                               retrieval_permitted=False)
        return Outcome("pass", H0_PRE_RETRIEVAL, [], retrieval_permitted=True)

    def ranking(self, figures: Sequence[Figure]) -> Outcome:
        return self._sweep(H1_RANKING, figures)

    def tool_time(self, figures: Sequence[Figure]) -> Outcome:
        return self._sweep(H2_TOOL_TIME, figures)

    def answer_time(self, figures, state=None, events=()) -> Outcome:
        return self._sweep(H3_ANSWER_TIME, figures, state, events)

    def export_time(self, figures, state=None, events=()) -> Outcome:
        outcome = self._sweep(H3_ANSWER_TIME, figures, state, events)
        outcome.hook = H4_EXPORT_TIME
        return outcome

    # -- the sweep ---------------------------------------------------------

    def _governs(self, record: Dict[str, Any], figure: Figure) -> bool:
        raw = (record.get("applies_to") or {}).get("quantity")
        if raw is None or raw == "any":
            matched = True
        else:
            names = [raw] if isinstance(raw, str) else list(raw)
            classes = self._classes()
            matched = any(
                n == figure.quantity
                or (str(n).startswith("any_") and figure.quantity in classes.get(n, ()))
                for n in names)
        if not matched:
            return False
        wanted = (record.get("applies_to") or {}).get("claim_class")
        return not wanted or figure.claim_class == wanted

    def _classes(self) -> Dict[str, Tuple[str, ...]]:
        out: Dict[str, List[str]] = {}
        for name, spec in (self.manifest.get("quantities") or {}).items():
            for cls in ((spec or {}).get("classes") or ()):
                out.setdefault(str(cls), []).append(str(name))
        return {k: tuple(v) for k, v in out.items()}

    def _hooks_of(self, record: Dict[str, Any]) -> Tuple[str, ...]:
        declared = record.get("hook")
        if declared:
            return (str(declared),)
        return HOOKS_BY_KIND.get(record.get("kind", ""), ())

    def _sweep(self, hook, figures, state=None, events=()) -> Outcome:
        if not self.enabled:
            return self._disabled_outcome(hook)
        records = [r for r in self.invariants if hook in self._hooks_of(r)]
        findings: List[Finding] = []
        spent = skipped = 0
        for figure in figures:
            for record in records:
                if spent >= BUDGET:
                    skipped += 1
                    continue
                spent += 1
                if not self._governs(record, figure):
                    continue
                finding = self._one(record, figure, figures, state, events)
                if finding is not None:
                    findings.append(finding)
        if skipped:
            logger.error(
                "REASONING BUDGET EXCEEDED at %s: %d checks skipped (cap %d). "
                "A skipped check is not a pass", hook, skipped, BUDGET)
        outcome = Outcome(_verdict(findings), hook, findings)
        outcome.incomplete = bool(skipped)
        outcome.skipped = skipped
        return outcome

    def _finding(self, record, figure, message, missing=()) -> Finding:
        """Never refuse the operator's own figure: if they typed it, it is
        authoritative input rather than a claim to check."""
        headline = str(record.get("message") or "")
        listed = ", ".join(missing) if missing else ""
        text = (f"{headline} [{message}]" if headline and message else (headline or message))
        text = text.replace("{missing}", listed or "its qualifiers")
        text = text.replace("{value}", str(figure.value) if figure.value is not None
                            else (listed or "the figure"))
        severity = str(record.get("severity") or "refuse")
        softened = None
        if figure.is_operators_own and severity == "refuse":
            severity, softened = "flag", "refuse"
            text += (" -- flagged, not refused: this is the operator's own figure, "
                     "which is authoritative input rather than a claim to check")
        return Finding(str(record.get("id") or "INV"), str(record.get("kind") or ""),
                       severity, text, figure.quantity, list(missing),
                       dict(figure.span) if figure.span else None, softened)

    def _one(self, record, figure, siblings, state, events) -> Optional[Finding]:
        kind = record.get("kind")
        state = state or {}

        needed = [str(f) for f in (record.get("requires_state") or ())]
        if needed and record.get("block_if_missing_state"):
            absent = [n for n in needed if state.get(n) is None]
            if absent:
                return self._finding(record, figure,
                    "state UNKNOWN -- " + ", ".join(absent) + " unavailable. Live "
                    "state has no design-basis fallback", absent)

        if kind == "grounding":
            if figure.origin == "operator":
                return None
            if figure.origin in ("document", "tool") and figure.source_id:
                return None
            return self._finding(record, figure,
                f"{figure.quantity} is not grounded: origin is {figure.origin} with "
                f"no source_id", ["source_id"])

        if kind == "qualifier":
            missing = figure.missing([str(f) for f in (record.get("requires") or ())])
            any_of = [str(f) for f in (record.get("requires_any_of") or ())]
            if any_of and not any(figure.known(f) for f in any_of):
                missing = missing + [f"one of {', '.join(any_of)}"]
            if missing:
                return self._finding(record, figure,
                    f"a {figure.quantity} figure without {', '.join(missing)} cannot "
                    f"be acted on", missing)
            return None

        if kind == "authority":
            governing = record.get("governing") or record.get("governing_class")
            accepted = ([governing] if isinstance(governing, str) else list(governing or []))
            if record.get("fallback_class"):
                accepted.append(str(record["fallback_class"]))
            if figure.source_class is None:
                return self._finding(record, figure,
                    f"{figure.quantity} carries no source class, so it cannot be "
                    f"ranked against {', '.join(accepted)}", ["source_class"])
            if figure.source_class in accepted:
                return None
            demoted = [str(c) for c in (record.get("demote") or ())]
            if figure.source_class in demoted:
                return self._finding(record, figure,
                    f"a {figure.source_class} is demoted for {figure.quantity}: "
                    f"{' or '.join(accepted)} governs")
            ladder = self.manifest.get("source_classes") or {}
            mine = ladder.get(figure.source_class) or {}
            for name in accepted:
                theirs = ladder.get(name) or {}
                if figure.source_class in (theirs.get("reject_as_proof") or ()):
                    return self._finding(record, figure,
                        f"a {figure.source_class} is not proof of {figure.quantity}: "
                        f"{name} governs and it is explicitly rejected as proof")
                if mine.get("rank") and theirs.get("rank") and mine["rank"] > theirs["rank"]:
                    return self._finding(record, figure,
                        f"{figure.source_class} (rank {mine['rank']}) is outranked by "
                        f"{name} (rank {theirs['rank']})")
            return None

        if kind == "currency":
            provider = str((record.get("window") or {}).get("provider") or "")
            if provider and state.get(provider) is None:
                return self._finding(record, figure,
                    f"state UNKNOWN -- {provider} unavailable. Live state has no "
                    f"design-basis fallback", [provider])
            trigger = record.get("trigger_source")
            if trigger and figure.qualifiers.get(str(trigger)) is True:
                return self._finding(record, figure,
                    f"{trigger} is true -- the record predates a disturbance", [str(trigger)])
            declared = set((self.manifest.get("staleness_triggers") or {}).get(
                figure.quantity, ()) or ())
            happened = [e for e in (events or ()) if e in declared]
            if happened:
                return self._finding(record, figure,
                    f"{figure.quantity} is stale: " + ", ".join(happened)
                    + " has happened since it was recorded", happened)
            return None

        if kind == "provenance":
            forbidden = [str(f) for f in (record.get("forbid") or ())]
            named = [d for d in figure.derivations if d in forbidden]
            if named:
                return self._finding(record, figure,
                    f"forbidden derivation: {', '.join(named)}", named)
            for dimension in [str(a) for a in (record.get("across") or ())]:
                mine = figure.qualifiers.get(dimension)
                if dimension == "revision" and figure.revision is not None:
                    mine = figure.revision
                theirs = figure.asked_about.get(dimension)
                if mine is not None and theirs is not None and str(mine) != str(theirs):
                    return self._finding(record, figure,
                        f"this figure belongs to {dimension}={mine}; the question is "
                        f"about {dimension}={theirs}", [dimension])
            return None

        if kind == "derivation":
            fired = [c for c in (record.get("block_if") or ()) if c in figure.conditions]
            if fired:
                return self._finding(record, figure,
                    f"blocking condition: {', '.join(fired)}", list(fired))
            forbidden = [str(f) for f in (record.get("forbid") or ())]
            named = [d for d in figure.derivations if d in forbidden]
            if named:
                return self._finding(record, figure,
                    f"forbidden derivation: {', '.join(named)}", named)
            steps = [str(s) for s in (record.get("requires_steps") or ())]
            absent = [s for s in steps if s not in figure.steps]
            if steps and absent:
                return self._finding(record, figure,
                    "calculation shown without " + ", ".join(absent)
                    + " -- a bare answer cannot be checked", absent)
            wrong = check_arithmetic(figure.text)
            if wrong:
                shown, expected, stated = wrong
                return self._finding(record, figure,
                    f"stated working and stated result disagree: '{shown}' -- "
                    f"{expected:g}, not {stated:g}")
            for other in siblings:
                if other is figure or other.quantity != figure.quantity:
                    continue
                if (other.value is not None and figure.value is not None
                        and other.unit == figure.unit and other.value != figure.value):
                    return self._finding(record, figure,
                        f"the answer contradicts itself on {figure.quantity}: "
                        f"{figure.value} and {other.value}")
            return None

        if kind == "unit_discipline":
            legal = ((self.manifest.get("quantities") or {}).get(figure.quantity) or {})
            units = [str(u) for u in (legal.get("units") or ())]
            if units and figure.unit and figure.unit not in units:
                return self._finding(record, figure,
                    f"unit {figure.unit!r} is not legal for {figure.quantity} "
                    f"(expected one of {', '.join(units)})", ["unit"])
            one_of = [str(t) for t in (record.get("requires_one_of") or ())]
            if one_of:
                haystack = (figure.text or "").lower()
                values = {str(v).lower() for v in figure.qualifiers.values() if v is not None}
                if figure.unit:
                    values.add(str(figure.unit).lower())
                if not any(t.lower() in haystack or t.lower() in values for t in one_of):
                    return self._finding(record, figure,
                        "none of " + ", ".join(one_of) + " stated", one_of)
            forbid = [str(f) for f in (record.get("forbid") or ())]
            if ("bare_number" in forbid or "unspecified" in forbid) and not figure.unit:
                if re.search(r"(?<![\\w.])-?\\d[\\d,]*(?:\\.\\d+)?(?![\\w.%])", figure.text or ""):
                    if not re.search(r"\\d\\s*[A-Za-z%]", figure.text or ""):
                        return self._finding(record, figure,
                            "bare number -- every figure carries its unit", ["unit"])
            missing = figure.missing([str(f) for f in (record.get("requires") or ())])
            if missing:
                return self._finding(record, figure,
                    f"{figure.quantity} without {', '.join(missing)}", missing)
            return None

        if kind == "band":
            band = record.get("band") or {}
            low, high = band.get("min"), band.get("max")
            if str(low) == "present" or str(high) == "present":
                absent = [n for n, want in (("min", low), ("max", high))
                          if str(want) == "present" and (figure.bounds or {}).get(n) is None]
                if absent:
                    return self._finding(record, figure,
                        f"{figure.quantity} is a band -- {', '.join(absent)} missing; a "
                        f"single-sided figure is not a conservative answer, it is an "
                        f"unusable one", absent)
                if band.get("same_source") and (
                        (figure.bounds or {}).get("source_id_min")
                        != (figure.bounds or {}).get("source_id_max")):
                    return self._finding(record, figure,
                        f"the two bounds of {figure.quantity} come from different "
                        f"sources", ["same_source"])
                return None
            if figure.value is None or isinstance(figure.value, bool):
                return None
            try:
                value = float(figure.value)
            except (TypeError, ValueError):
                return None
            if low is not None and value < float(low):
                return self._finding(record, figure,
                    f"{figure.quantity} = {figure.value} is below the possible minimum "
                    f"{low} -- check the unit it was computed in")
            if high is not None and value > float(high):
                return self._finding(record, figure,
                    f"{figure.quantity} = {figure.value} is above the possible maximum "
                    f"{high} -- check the unit it was computed in")
            return None

        return None


_EQUATION = re.compile(
    r"(-?\\d[\\d,]*(?:\\.\\d+)?)\\s*([/*x+-])\\s*(-?\\d[\\d,]*(?:\\.\\d+)?)\\s*=\\s*"
    r"(-?\\d[\\d,]*(?:\\.\\d+)?)")


def check_arithmetic(text: str, tolerance: float = 0.01):
    """Find a stated ``a op b = c`` whose result is wrong.

    Live: "L/20 = 4800/20 = 200 mm" -- rule right, inputs right, result wrong.
    The hardest defect to see by reading and trivial to catch by evaluating.
    """
    for match in _EQUATION.finditer(text or ""):
        left, op, right, stated = match.groups()
        try:
            a = float(left.replace(",", ""))
            b = float(right.replace(",", ""))
            c = float(stated.replace(",", ""))
        except ValueError:
            continue
        if op == "/":
            if b == 0:
                continue
            expected = a / b
        elif op in ("*", "x"):
            expected = a * b
        elif op == "+":
            expected = a + b
        else:
            expected = a - b
        if expected == 0:
            if abs(c) > tolerance:
                return match.group(0), expected, c
            continue
        if abs(expected - c) / abs(expected) > tolerance:
            return match.group(0), expected, c
    return None


#: One kernel per process. The kit does not change while the platform runs.
kernel = ReasoningKernel()
'''


def render_host() -> str:
    """The four functions the PRODUCT fills in. Unwired means refuse, not pass."""
    return '''"""The host contract: the four functions this platform must implement.

The kernel cannot read this platform's corpus, its SCADA or its documents, so the
platform supplies them. Until it does, each raises HostNotWired and the caller
REFUSES -- an unwired host must never look like a clean pass, because a platform
that forgot to wire retrieval would then answer ungated and look fine doing it.

Fill these in and delete the raises. Nothing else in the reasoning layer changes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.reasoning.kernel import Figure, HostNotWired, Outcome, kernel


def extract_figures(answer: str, tool_results: Any, context: Any) -> List[Figure]:
    """Every figure this answer states, with what is KNOWN about each.

    Do not infer a qualifier, a claim class or a source class from the prose.
    Inferring them is the defect the layer exists to catch: an unqualified figure
    must arrive unqualified so the kernel can refuse it.
    """
    raise HostNotWired(
        "extract_figures is not implemented: this platform cannot gate its own "
        "answers until it is")


def resolve_source(source_id: str) -> Dict[str, Any]:
    """``{class, revision, effective_date}`` for a citation."""
    raise HostNotWired("resolve_source is not implemented")


def state(provider_name: str) -> Optional[Dict[str, Any]]:
    """The named live-state record, or None when it cannot be read.

    None means UNKNOWN. Never substitute a design figure for a live state.
    """
    raise HostNotWired("state is not implemented")


def apply(outcome: Outcome) -> None:
    """Annotate, flag or refuse. Able to replace ONE figure (using the finding's
    span) or the whole answer."""
    raise HostNotWired("apply is not implemented")


def gate_question(question: str) -> Outcome:
    """H0, for the router to call BEFORE retrieval.

    Call this first and return its refusal without retrieving anything. A test
    should assert the retrieval function was never called.
    """
    return kernel.pre_retrieval(question)
'''


def render_pending(manifest: Optional[Dict[str, Any]] = None) -> str:
    """The unanswered interview questions, as the platform's own record."""
    return '''"""Figures the build interview did not answer, and the question for each.

These are NOT stubs. A stub is a fake value that ships silently and gets mistaken
for real. Here the figure has no value at all, and any statement needing it is
REFUSED naming the question the operator must go and answer.

When an answer arrives it is stored with who gave it, when, and from which
document. Without that the interview has only moved the invented figure from the
model to the operator.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from app.reasoning.kernel import kernel


def unanswered() -> Dict[str, str]:
    """``{figure: the interview question}`` for every figure with no value."""
    return kernel.pending_questions()


def value_of(figure: str) -> Tuple[Any, Optional[str]]:
    """``(value, refusal)``. A figure with no answer returns a refusal naming the
    question -- never a default, never a zero, never a plausible number."""
    return kernel.figure_value(figure)


def answer(figure: str, value: Any, *, answered_by: str, answered_at: str,
           source: str) -> Dict[str, Any]:
    """Record an interview answer WITH its provenance, and PERSIST it.

    All three of answered_by, answered_at and source are required. An answer
    without them is an unattributed figure, which is the thing this layer refuses
    from a model and must equally refuse from a person.

    The kernel writes it to this platform's storage and reads it back before
    returning; if it did not persist, this raises instead of reporting success.
    An earlier version validated the provenance, returned the dict and stored
    nothing, so a caller got ok:true for a no-op.
    """
    return kernel.record_answer(
        figure, value, answered_by=answered_by, answered_at=answered_at,
        source=source)


def answered() -> Dict[str, Dict[str, Any]]:
    """Every answer this platform holds, with who gave it, when and from where."""
    return kernel.answers()
'''


def emit(ctx: Any) -> list:
    """Write the socket into the product under build. Returns the paths written.

    The kit itself is vendored by the CLONER from the Store, exactly as a block
    is. When no kit has been resolved for this vertical the socket is still
    emitted with an EMPTY kit directory -- and the kernel then refuses every
    statement, naming the missing kit. That is deliberate: a platform with a
    reasoning socket and no kit must refuse, not run ungated, because "no kit
    found" silently becoming "no invariants" is the failure the whole layer
    exists to prevent.
    """
    import pathlib

    written = []
    for relative, body in (
        (INIT_PATH, render_init()),
        (KERNEL_PATH, render_kernel()),
        (HOST_PATH, render_host()),
        (PENDING_PATH, render_pending()),
        (ROUTES_PATH, render_routes()),
    ):
        ctx.workspace.write_text(pathlib.Path(relative), body)
        written.append(relative)
    return written


def kit_for_vertical(blueprint: Any, resolved: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Which kit this build gets, from the blueprint's own vertical.

    Nothing is guessed from a product name: the blueprint states its vertical, and
    a vertical with no kit gets None -- which the socket reports as a refusal
    rather than as a pass.
    """
    vertical = ""
    for attribute in ("vertical", "domain", "industry"):
        value = getattr(blueprint, attribute, None) or (
            (resolved or {}).get(attribute) if resolved else None)
        if value:
            vertical = str(value).strip().lower().replace(" ", "_").replace("-", "_")
            break
    if not vertical:
        return None
    return VERTICAL_TO_KIT.get(vertical)


#: Vertical -> kit. One entry per kit the Store publishes; a vertical absent here
#: has no reasoning kit yet, and the socket says so instead of inventing one.
VERTICAL_TO_KIT: Dict[str, str] = {
    "datacentre": "datacentre",
    "data_centre": "datacentre",
    "data_center": "datacentre",
    "offshore_marine": "offshore_marine",
    "offshore": "offshore_marine",
    "fitout": "fitout",
    "fit_out": "fitout",
    "interior_design": "fitout",
    "facility_management": "fm",
    "fm": "fm",
    "fire_protection": "fire_protection",
    "heritage": "heritage",
    "heritage_restoration": "heritage",
    "og_construction": "og_construction",
    "oil_gas_construction": "og_construction",
    "rail": "rail",
    "railway": "rail",
    "metro": "rail",
    "pump_station": "pump_station",
    "water_pump_station": "pump_station",
    "water_treatment": "water_treatment",
    "og_operations": "og_operations",
    "oil_gas_operations": "og_operations",
    "dental": "dental",
    "aesthetic": "aesthetic",
    "cosmetic": "aesthetic",
    "aviation_ops": "aviation_ops",
    "aviation_operations": "aviation_ops",
    "architecture": "architecture",
    "architecture_office": "architecture",
    "ports_marine": "ports_marine",
    "ports": "ports_marine",
    "airport_construction": "airport_construction",
    "airport": "airport_construction",
}


def render_routes() -> str:
    """The operator's way to answer the kit's questions after the build.

    The build interview cannot ask everything -- the Floor's question rounds are
    bounded, and most of these figures are operational rather than design-time.
    So the platform ships with the remainder OPEN: the kernel refuses anything
    that needs them, and these routes are where the answer arrives later, from
    the person who actually knows it.
    """
    return '''"""Reasoning-layer figures: what is still unanswered, and how to answer it.

GET  /v1/reasoning/pending   the questions this platform cannot answer yet
POST /v1/reasoning/answer    record one answer, with its provenance

Until a figure is answered the kernel REFUSES every statement needing it and
names this question. That is not a stub: a stub is a fake value that ships
silently and gets mistaken for real, and there is none here to mistake.

An answer requires answered_by, answered_at and source. An unattributed figure is
not evidence whoever supplied it -- refusing it from a model and accepting it from
a person would just move the invented figure one seat along.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from app import auth
from app.reasoning import pending
from app.reasoning.kernel import kernel

router = APIRouter(tags=["reasoning"])


@router.get("/v1/reasoning/pending")
async def reasoning_pending(request: Request) -> Dict[str, Any]:
    """Every figure with no answer, and the question that fills it."""
    tenant = await _principal(request)
    unanswered = pending.unanswered()
    return {
        "kit_enabled": kernel.enabled,
        "kit_disabled_reason": kernel.disabled_reason,
        "unanswered_count": len(unanswered),
        "questions": [
            {"figure": name, "question": question}
            for name, question in sorted(unanswered.items())
        ],
        "note": (
            "Any answer needing one of these is refused, naming the question. "
            "Nothing is defaulted and nothing is estimated."
        ),
    }


@router.post("/v1/reasoning/answer")
async def reasoning_answer(request: Request) -> Dict[str, Any]:
    """Record one answer WITH its provenance. All three fields are required."""
    tenant = await _principal(request)
    auth.require_permission(tenant, "write")
    body = await _json(request)
    figure = str(body.get("figure") or "").strip()
    if not figure:
        raise HTTPException(status_code=422, detail="figure is required")
    if figure not in pending.unanswered():
        raise HTTPException(
            status_code=409,
            detail=f"{figure} is not an open question on this platform",
        )
    try:
        recorded = pending.answer(
            figure,
            body.get("value"),
            answered_by=str(body.get("answered_by") or ""),
            answered_at=str(body.get("answered_at") or ""),
            source=str(body.get("source") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        # It did not persist. Reporting ok here would be a success over an
        # operation that did nothing, which is what this layer refuses from
        # everyone else.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    recorded["tenant_id"] = getattr(tenant, "tenant_id", None)
    return {
        "ok": True,
        "recorded": recorded,
        "still_unanswered": len(pending.unanswered()),
    }


async def _principal(request: Request):
    from app.tenancy import resolve_tenant

    return await resolve_tenant(request)


async def _json(request: Request) -> Dict[str, Any]:
    from app.auth import json_object

    return await json_object(request)
'''
