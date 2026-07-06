# Decision Brief: Default Deploy Target

## Context

The deploy router currently defaults to `target="cloud"`, which routes through `package_session` (the original cloud/edge packager). Two newer options exist:

- `package_platform_session` — a production-hardened "Fork-class" platform package.
- Edge packaging — a self-contained zip built from the `package_session` output.

This brief compares three options for the default target without making an implementation change. The final decision is deferred to the human maintainers.

## Options

### (a) Keep `package_session` (cloud/edge) as the default

- **Image size**: Smaller because it clones only `Cerebrum-Blocks` at build time and layers session files on top.
- **Build time**: Faster; single-stage Dockerfile, no frontend build, no Postgres/alembic bootstrap.
- **Operational complexity**: Lower for a simple cloud Render deploy; higher for operators who expect a production-ready stack.
- **Self-containment limitation**: The package is *not* fully self-contained. The generated Dockerfile runs `git clone` against the public `Cerebrum-Blocks` repo, so the runtime source is fetched at build time rather than vendored. If the upstream repo moves, changes, or becomes unreachable, historical packages may not rebuild reproducibly.
- **Smoke-test finding**: The edge package built this way is **not runnable standalone**; it still expects the Cerebrum-Blocks runtime to be present.

### (b) Make `package_platform_session` the default

- **Image size**: Larger because it is multi-stage, copies `site-packages` and `/usr/local/bin` from the builder, and may include a built frontend.
- **Build time**: Longer; multi-stage Docker build, optional `npm ci` + `npm run build`, Postgres/pgvector via docker-compose, alembic migrations on boot.
- **Operational complexity**: Higher locally (needs Postgres), but lower for production because secrets, migrations, healthchecks, and a non-root user are built in.
- **Self-containment**: Better. The package contains the session artifacts, Dockerfile, docker-compose, Render blueprint, and entrypoint. It still clones `Cerebrum-Blocks` inside the builder stage via `CEREBRUM_BLOCKS_REPO`, but that is a controlled, configurable source.
- **Smoke-test friction**: The platform packager currently requires a local `Cerebrum-Blocks` checkout on the *packaging* host (for kit resolution and CLI sourcing). On the Render backend this checkout does not exist, so packaging fails unless `CEREBRUM_BLOCKS_ROOT` is set to a valid engine path. Making platform the default would force us to resolve this dependency — e.g., by vendoring a minimal kit/CLI snapshot into the backend container or by making the packager fetch the engine at packaging time.
- **How (b) resolves the edge limitation**: Platform packages are explicitly production-oriented; users receive a docker-compose stack rather than a "drop this into an engine checkout" zip, so the "not runnable standalone" confusion goes away.

### (c) Refactor the edge package to embed a minimal engine runtime

- **Image size**: Largest option. Embedding a full or stripped `Cerebrum-Blocks` runtime (plus Python dependencies) inside every zip would make each package hundreds of megabytes.
- **Build time**: Slowest; the backend would need to clone, install dependencies, and snapshot the runtime for every session package.
- **Operational complexity**: Highest for the packager/backend; lowest for the operator, because the zip would truly be standalone.
- **Trade-off**: True offline/standalone capability at the cost of package size, build time, and backend complexity.
- **Smoke-test finding**: This is the only option that would make the edge package runnable without any external clone step.

## Recommendation

**Recommended: Option (b) — make `package_platform_session` the default**, but only after solving the local-engine-checkout dependency.

Reasoning:

1. The smoke test showed that the current cloud/edge package confuses users because it is not actually standalone.
2. The platform package already includes the production hardening (Postgres, migrations, secrets, healthchecks, unprivileged user) that cloud deploys will eventually need.
3. The operational complexity of (c) — embedding a runtime per package — is disproportionate to the current user base and would bloat every package.
4. The blocker for (b) is packaging-time engine discovery, not runtime behavior. Vendoring or fetching the engine at package build time is a solvable, bounded problem.

DECISION PENDING
