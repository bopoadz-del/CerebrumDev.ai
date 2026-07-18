# Regeneration proof

Procedure:

1. `python -m app.factory.cli generate --blueprint blueprints/steward/steward.v1.yaml --out /tmp/steward_regen/a`
2. Generate again to `/tmp/steward_regen/b`
3. Compare `hash_tree` excluding `provenance.json` timestamps

**Result:** trees match (deterministic code/content generation).

Covered in CI-style unit test: `tests/factory/test_generate_regenerate.py`.
