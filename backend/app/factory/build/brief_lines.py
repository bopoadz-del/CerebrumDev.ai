"""The one definition of a brief's content lines.

The compiler records the lines it wrote; the lint compares a brief's lines to
that record. Both must cut and normalise lines identically, so the rule lives
here and nowhere else.
"""

from __future__ import annotations

import re
from typing import FrozenSet, List

HEADING_RE = re.compile(r"^(=+|CUT \d|TARGET|STEP 0|DO\b|ACCEPTANCE|FORBIDDEN|PHASE \d|# )")


def content_lines(text: str) -> List[str]:
    """Non-blank, non-rule, non-heading lines, stripped."""
    lines: List[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or set(line) <= {"=", "-", " "} or HEADING_RE.match(line):
            continue
        lines.append(line)
    return lines


def line_key(line: str) -> str:
    return " ".join(str(line).split()).lower()


def emitted_line_set(text: str) -> FrozenSet[str]:
    """What a compiler that wrote ``text`` records as its own lines."""
    return frozenset(line_key(line) for line in content_lines(text))
