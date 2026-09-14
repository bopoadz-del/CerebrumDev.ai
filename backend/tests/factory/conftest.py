"""Factory-suite stub-coder re-export.

The stub machinery lives in the backend-wide ``tests/conftest.py`` so every
suite (factory, e2e, root) can give a green WRITER phase a deterministic
stubbed coding agent (Phase 0.5: zero agent-authored artifacts refuses with
``writer_no_output``). Kept here as a re-export because older factory tests
import these names from this module.
"""

from tests.conftest import (  # noqa: F401
    stub_coder,
    stub_coder_patches,
)
