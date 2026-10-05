"""The product-gate suites TESTER stamps -- one list, two readers.

TESTER writes these files (roles_handlers.run_tester renders them from the
product's own declared models, declared_specs); the writer's self-check
(scripts/factory_checks.py) runs whichever of them are on disk. They stay
in the workspace between rework rounds, Factory-owned in TESTER's lane, so
in every rework round the writer can run the exact suites that failed it
before it declares done -- and TESTER re-stamps them from the models the
writer left, so what the writer ran and what judges it read one source.
"""

from __future__ import annotations

from app.factory.build.placeholder_connectors import CONTRACT_TEST

SMOKE_SUITE = "tests/test_smoke.py"
ROUTES_SUITE = "tests/test_routes.py"
DOMAIN_SUITE = "tests/test_domain_acceptance.py"

#: In the order the self-check runs them.
PRODUCT_SUITES = (SMOKE_SUITE, ROUTES_SUITE, DOMAIN_SUITE, CONTRACT_TEST)
