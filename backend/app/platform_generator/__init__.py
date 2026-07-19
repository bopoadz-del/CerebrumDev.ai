"""Fork-derived platform template generator.

This package turns a pinned The_Fork baseline into a domain-branded,
deployable platform package by applying manifest-driven transformations.
It is intentionally separate from ``app.core.platform_packager``, which
vendors Cerebrum-Blocks engines; this generator produces the full
application runtime package.
"""

from .generator import PlatformGenerator, GenerationInputsHash
from .fork_baseline import FORK_BASELINE_COMMIT

__all__ = ["PlatformGenerator", "GenerationInputsHash", "FORK_BASELINE_COMMIT"]
