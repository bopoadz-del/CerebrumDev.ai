"""Domain Intelligence Compiler — scout a donor repository.

Discovers candidate formulas, rules, workflows and approval policies with
file/line evidence. Discovery is AST-based (real code, not docstrings or
comments). The scout NEVER certifies: every discovered artifact is marked
``candidate`` with its provenance, and the discovery report separates
VERIFIED (test evidence found) from UNVERIFIED.

This is a discovery tool, not a certifier — certification states move
only through the STORE_MANAGER / domain-review gates.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_CALC_HINT = re.compile(
    r"(calculate|compute|score|rate|formula|amount|variance|tolerance|ratio|quantize|duration|price|cost)",
    re.IGNORECASE,
)
_DECIMAL_HINT = re.compile(r"Decimal\(")
_ROUND_HINT = re.compile(r"round\(")
_RULE_HINTS = re.compile(
    r"rule_id|violation_message|procedure|PRC-|approval|require_approved|refus",
    re.IGNORECASE,
)
_WORKFLOW_HINTS = re.compile(
    r"next_.*status|VALID_.*STATUS|states\s*=|transition",
    re.IGNORECASE,
)
_APPROVAL_HINTS = re.compile(
    r"require_approved|approve_request|minimum_approvals|self_approval|approver",
    re.IGNORECASE,
)
_WORKFLOW_NAME = re.compile(r"^(next_|transition|advance|activate)")


@dataclass
class Discovery:
    """One discovered candidate artifact with exact provenance."""

    kind: str  # formula | rule | workflow | approval
    symbol: str
    path: str
    line: int
    donor_commit: str
    test_evidence: List[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "symbol": self.symbol,
            "path": self.path,
            "line": self.line,
            "donor_commit": self.donor_commit,
            "test_evidence": list(self.test_evidence),
            "classification": "VERIFIED" if self.test_evidence else "EXECUTABLE_UNVERIFIED",
            "notes": self.notes,
        }


@dataclass
class ScoutReport:
    repo: str
    commit: str
    discoveries: List[Discovery] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo": self.repo,
            "commit": self.commit,
            "discoveries": [d.to_dict() for d in self.discoveries],
            "totals": {
                kind: sum(1 for d in self.discoveries if d.kind == kind)
                for kind in ("formula", "rule", "workflow", "approval")
            },
        }


class DonorScout:
    """Scan a donor checkout for reasoning artifacts. Read-only."""

    def __init__(self, repo_root: Path, commit: str = "") -> None:
        self.root = Path(repo_root)
        self.commit = commit
        self._test_index: Dict[str, List[str]] = {}

    def _build_test_index(self) -> None:
        tests_dir = self.root / "tests"
        if not tests_dir.is_dir():
            tests_dir = self.root / "test"
        if not tests_dir.is_dir():
            return
        for test_file in sorted(tests_dir.rglob("*.py")):
            try:
                text = test_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for symbol in re.findall(r"\b([a-zA-Z_]\w*)\s*\(", text):
                self._test_index.setdefault(symbol, []).append(
                    str(test_file.relative_to(self.root)).replace("\\", "/")
                )

    def scout(self, paths: Optional[List[str]] = None) -> ScoutReport:
        self._build_test_index()
        report = ScoutReport(repo=str(self.root), commit=self.commit)
        candidates = paths or ["backend/app", "app", "src"]
        for base in candidates:
            root = self.root / base
            if not root.is_dir():
                continue
            for py_file in sorted(root.rglob("*.py")):
                if "__pycache__" in py_file.parts:
                    continue
                try:
                    text = py_file.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                rel = str(py_file.relative_to(self.root)).replace("\\", "/")
                report.discoveries.extend(self._scan_file(text, rel))
        return report

    def _scan_file(self, text: str, rel: str) -> List[Discovery]:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []
        out: List[Discovery] = []
        lines = text.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.extend(self._classify_function(node, lines, rel))
            elif isinstance(node, ast.Assign):
                if len(node.targets) != 1 or not isinstance(
                    node.targets[0], ast.Name
                ):
                    continue
                name = node.targets[0].id
                if not re.match(r"^[A-Z_][A-Z0-9_]*$", name):
                    continue
                if not isinstance(node.value, (ast.Dict, ast.List)):
                    continue
                segment = ast.get_source_segment(text, node.value) or ""
                if _RULE_HINTS.search(segment):
                    out.append(
                        Discovery(
                            kind="rule",
                            symbol=name,
                            path=rel,
                            line=node.lineno,
                            donor_commit=self.commit,
                            test_evidence=self._test_index.get(name, []),
                            notes="rule table constant — candidate, review before extraction",
                        )
                    )
        return out

    def _classify_function(
        self, node: Any, lines: List[str], rel: str
    ) -> List[Discovery]:
        name = node.name
        if name.startswith("_"):
            return []
        end = min(node.end_lineno or node.lineno, len(lines))
        body = "\n".join(lines[node.lineno : end])
        header = "\n".join(lines[node.lineno - 1 : min(node.lineno + 8, len(lines))])
        out: List[Discovery] = []
        evidence = self._test_index.get(name, [])
        if _CALC_HINT.search(name) or _DECIMAL_HINT.search(body) or _ROUND_HINT.search(body):
            out.append(
                Discovery(
                    kind="formula",
                    symbol=name,
                    path=rel,
                    line=node.lineno,
                    donor_commit=self.commit,
                    test_evidence=evidence,
                )
            )
        if _APPROVAL_HINTS.search(name) or _APPROVAL_HINTS.search(header):
            out.append(
                Discovery(
                    kind="approval",
                    symbol=name,
                    path=rel,
                    line=node.lineno,
                    donor_commit=self.commit,
                    test_evidence=evidence,
                )
            )
        if _WORKFLOW_NAME.match(name) and _WORKFLOW_HINTS.search(body):
            out.append(
                Discovery(
                    kind="workflow",
                    symbol=name,
                    path=rel,
                    line=node.lineno,
                    donor_commit=self.commit,
                    test_evidence=evidence,
                )
            )
        return out
