# Factory store pin (`blocks.lock.json`)

The Factory consumes Cerebrum-Blocks through a lockfile. Store drift cannot
silently change a build: every consumed block is resolved through
`blocks.lock.json`, and an unlocked or hash-mismatched block is a hard
failure that names the block and both hashes.

The lock is generated from the store tree. Do not hand-write entries.

## What is pinned

- File: [`blocks.lock.json`](../../blocks.lock.json) at the Factory repo root
- Schema: `factory.blocks.lock.v1`
- Per consumed block: `id`, `version`, `content_hash` (`sha256:` of the
  block directory: sorted relative paths + bytes, skipping `__pycache__`)
- `store.sha` is the Cerebrum-Blocks commit the lock was generated from
- Estate-only blocks that are not yet in the store are hashed from
  `backend/app/factory/vendor_blocks_mirror` and recorded with
  `source: factory-vendor-mirror`

## Resolve at build time

`python -m app.factory.cli generate` and `build` (and the role CLONER)
resolve a store-sourced consumed block through the lock:

- missing lock entry → `BLOCKS_LOCK: unlocked block '<id>' …`
- hash mismatch → `BLOCKS_LOCK: hash mismatch for block '<id>': lock=… store=…`

There is no warning path and no fall-through to latest.

Vendor-mirror fallback (no Store checkout) is not a store pin; it is the
in-repo stub. The lock applies when the source is a Store checkout.

## Refresh the pin

Move the pin only on purpose, after reviewing the store delta:

```bash
# Dedicated command (preferred)
cd backend
PYTHONPATH=. python3 -m app.factory.cli update-lock \
  --blocks-root "$CEREBRUM_BLOCKS_ROOT"

# Equivalent flag on generate / build
PYTHONPATH=. python3 -m app.factory.cli generate \
  --blueprint ../blueprints/steward/steward.v1.yaml \
  --out ../factory_outputs/Cerebrum-Steward \
  --blocks-root "$CEREBRUM_BLOCKS_ROOT" \
  --update-lock
```

`--output` overrides the default path (`<factory-repo>/blocks.lock.json`).

Commit the regenerated lock in the same change that intends to accept the
new store bytes.
