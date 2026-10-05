"""The machine-readable shape of a validation rejection.

A Factory-rendered product refuses an invalid payload in two places: the
route guard ``app.auth.reject_invalid_payload`` (HTTP 422) and the handler
constraint guard (HTTP 200, ``ok: false``). Both used to say what was wrong
only in prose ("status must be one of: open, closed"), and the emitted route
test learned the accepted values by running a vocabulary regex over the raw
response text. Over a JSON body that regex matched inside keys -- live
2026-10-05: ``"allowed_scopes":`` yielded the value ``_scopes":``, which the
test wrote into an unrelated field and oscillated on until
SAME_FAILURE_TWICE.

Now the rejection carries its own structure, rendered from these constants
by BOTH emitters, so the product and the test agree by construction and
nothing parses prose:

* 422 path -- response headers ``REJECTED_FIELD_HEADER`` (the field),
  ``REJECTION_REASON_HEADER`` (one of ``REASONS``) and, for a closed
  vocabulary, ``ALLOWED_VALUES_HEADER`` (a JSON list).
* ``ok: false`` path -- the same three facts as body keys.

The prose message stays for humans; no machine reads it.
"""

from __future__ import annotations

REJECTED_FIELD_HEADER = "X-Rejected-Field"
REJECTION_REASON_HEADER = "X-Rejection-Reason"
ALLOWED_VALUES_HEADER = "X-Allowed-Values"

REJECTED_FIELD_KEY = "rejected_field"
REJECTION_REASON_KEY = "rejection_reason"
ALLOWED_VALUES_KEY = "allowed_values"

MISSING_REQUIRED = "missing_required"
NOT_ALLOWED = "not_allowed"
REASONS = (MISSING_REQUIRED, NOT_ALLOWED)
