# Known Issues

This file tracks smoke-test friction items and other actionable issues that are not yet fixed. Each entry should contain enough detail to be picked up independently.

## 1. `cerebrum init` cannot select deployed mode from a pipe

**Source:** Smoke test — CLI onboarding.

`cerebrum init` currently requires an interactive TTY to select the deployed mode. When the command is run from a pipe or non-interactive environment (e.g., CI, heredoc, or redirected stdin), the mode-selection prompt cannot be answered and the flow blocks or fails.

**Fix direction:** Add a `--mode` flag to `cerebrum init` and handle non-TTY stdin gracefully so the mode can be provided non-interactively.

**Scope note:** This fix lands in the `Cerebrum-Blocks` CLI, not in this repository.

## 2. CLI crashes on Windows cp1252 without `PYTHONIOENCODING=utf-8`

**Source:** Smoke test — Windows CLI usage.

On Windows systems using the cp1252 code page, the `cerebrum` CLI can crash when printing UTF-8 characters (e.g., emoji, non-ASCII block names, or document content) unless the user has explicitly set `PYTHONIOENCODING=utf-8`.

**Fix direction:** The CLI should force UTF-8 output itself instead of relying on the environment. This can be done by configuring `sys.stdout`/`sys.stderr` to use UTF-8 with `errors="replace"` or equivalent at CLI startup.

**Scope note:** This fix lands in the `Cerebrum-Blocks` CLI, not in this repository.

## 3. Edge/package internal auth mismatch — RESOLVED

**Source:** Smoke test — edge package runtime.

The edge packager generates a random `CEREBRUM_MASTER_KEY` for the deployed package, but the Cerebrum-Blocks engine expects `cb_dev_key` (or another configured dev key) for internal self-calls. This mismatch causes authenticated endpoints inside the edge package to fail with 401/403 errors.

**Resolution:** Both packagers now wire the generated key as the single source of truth:
- `CEREBRUM_MASTER_KEY` is set to the minted key so the engine's `APIKeyAuth` validates it.
- `CB_DEV_KEY` is set to the same key for legacy container paths.
- `CEREBRUM_API_KEY_CDEV` / `CEREBRUM_API_KEY_PLATFORM` are set to the same key.
- The dropped CLI `config.toml` uses the same key.
- A boot test verifies the generated package accepts its own key and rejects a wrong key.

**Commit reference:** `feat/platform-default-pinned-engine`.

## 4. No semantic embedding backend in bare requirements → RAG unavailable on default install

**Source:** Smoke test — default install RAG path.

The bare requirements (`requirements.txt`) do not include a semantic embedding backend. As a result, RAG is unavailable on a default install even when vectors have been uploaded. The deployed router falls back to a degraded note, and retrieval cannot run.

**Fix direction:** Add a default semantic embedding backend (e.g., `sentence-transformers` or the configured `zvec` block dependency) to the base requirements, or clearly document that RAG requires an extra dependency group.
