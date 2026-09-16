"""F5: sanitisation for anything rendered into logs or build-status.

Hostnames and short opaque tokens (deploy ids, session ids, receipt ids)
are allowed through — an operator needs them to name what they are looking
at. API keys and auth headers are never allowed: they are redacted to a
stable token so the failure text stays readable without leaking credentials
into the ledger, the service log, or the Floor.
"""

import re

from typing import Optional

#: Header lines that must never survive into status/log text.
_HEADER_RE = re.compile(
    r"(?im)^\s*(?:authorization|proxy-authorization|x-api-key|api-key|"
    r"cookie|set-cookie)\s*:\s*.*$"
)

#: key=value / key: value assignments whose value is a credential.
#: (?<![A-Za-z0-9]) rather than \b: env names like DEEPSEEK_API_KEY have
#: no word boundary between the underscore and the key name.
_KEY_ASSIGN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:api[_-]?key|apikey|secret|password|passwd|access[_-]?key|"
    r"private[_-]?key)\s*[=:]\s*(\S+)"
)

#: Provider-style keys: sk-..., AKIA..., ghp_..., etc.
_KEY_PATTERN_RE = re.compile(
    r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}\b"
    r"|\bxox[baprs]-[A-Za-z0-9-]{20,}\b"
)

#: Bare long blobs (JWT-ish, base64 secrets) — short tokens survive.
_LONG_BLOB_RE = re.compile(r"\b[A-Za-z0-9+/_=-]{40,}\b")

_REDACTED = "[redacted]"


def sanitize_for_status(text: Optional[str]) -> str:
    """Return *text* with keys and auth headers redacted, tokens kept.

    A no-op for non-string input so callers can pass ``None`` through
    without a guard.
    """
    if not isinstance(text, str) or not text:
        return text or ""
    out = _HEADER_RE.sub(_REDACTED, text)

    def _redact_assign(m: "re.Match[str]") -> str:
        # Keep the key name so "DEEPSEEK_API_KEY=[redacted]" is still
        # readable; drop the value and anything after it on the line.
        return m.group(0)[: m.start(1) - m.start(0)] + _REDACTED

    out = _KEY_ASSIGN_RE.sub(_redact_assign, out)
    out = _KEY_PATTERN_RE.sub(_REDACTED, out)
    out = _LONG_BLOB_RE.sub(_REDACTED, out)
    return out
