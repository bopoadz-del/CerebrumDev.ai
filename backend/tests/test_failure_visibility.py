"""Tests that failures are actually visible to the platform.

The defects these guard against all shared one shape: the system was broken and
said it was fine. A health endpoint that reports "degraded" with a 200, and a
migration that fails into a running service, are both invisible to Render --
which reads exit codes and status codes, not JSON bodies.

So these assert on the machine-readable signal, not the human-readable one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("ALLOW_ANONYMOUS_DEV", "1")

import pytest  # noqa: E402

import app.main as main  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestReadyReturnsAnActionableStatusCode:
    @pytest.mark.asyncio
    async def test_ready_is_200_when_dependencies_are_healthy(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
        monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "present")

        resp = await main.ready()

        assert resp.status_code == 200
        assert json.loads(resp.body)["status"] == "ready"

    @pytest.mark.asyncio
    async def test_ready_is_503_when_storage_is_broken(self, monkeypatch):
        """The load-bearing case: a broken disk must not read as healthy.

        Asserting the body says "not_ready" is not enough -- that was already
        true, and Render still treated the service as up because the status
        code was 200.
        """
        monkeypatch.setattr(
            main, "_probe_storage", lambda: {"ok": False, "error": "read-only file system"}
        )
        monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "present")

        resp = await main.ready()

        assert resp.status_code == 503, "a broken disk still reports healthy to the platform"
        assert json.loads(resp.body)["status"] == "not_ready"


class TestMigrationFailureStopsTheBoot:
    def test_dockerfile_does_not_swallow_a_failed_migration(self):
        """A failed migration must not leave a service running on old schema.

        This is a text assertion on the Dockerfile because that is where the
        behaviour lives -- there is no Python seam to test. The failure mode it
        catches is a `|| echo` (or `|| true`) creeping back in, which turns a
        migration failure into a silently wrong-schema service that answers
        health checks normally.
        """
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        cmd_lines = [ln for ln in dockerfile.splitlines() if "alembic upgrade" in ln]
        assert cmd_lines, "expected the boot command to run migrations"
        for line in cmd_lines:
            assert "||" not in line, (
                "migration failure is being swallowed; the service would boot "
                f"on the wrong schema: {line.strip()}"
            )
            assert "&&" in line, (
                "uvicorn must be chained with && so it only starts after a "
                f"successful migration: {line.strip()}"
            )

    def test_production_image_does_not_probe_root_client_cert(self):
        """Non-root + HOME=/root made libpq open /root/.postgresql/postgresql.crt.

        That path is EACCES after setpriv drops to uid 10001, and Alembic
        aborted the Neon boot. sslmode=require is enough; no client cert.
        """
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        entry = (REPO_ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
        assert "ENV HOME=/app" in dockerfile
        assert "--home-dir /app" in dockerfile
        assert "PGSSLCERT" not in dockerfile or "ENV PGSSLCERT" not in dockerfile
        assert "export HOME=/app" in entry
        assert "unset PGSSLCERT" in entry
        assert "unset PGSSLKEY" in entry
        assert "/root/*" in entry


class TestHealthCheckPathPointsAtSomethingThatCanFail:
    def test_render_health_check_uses_the_endpoint_with_real_probes(self):
        import yaml

        render = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
        web = [s for s in render["services"] if s.get("name") == "cerebrumdev-backend"]
        assert web, "cerebrumdev-backend missing from render.yaml"
        assert web[0].get("healthCheckPath") == "/ready", (
            "health check must point at /ready; /health returns 200 even when degraded"
        )

    def test_production_image_includes_pg_dump(self):
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        assert "postgresql-client-18" in dockerfile, (
            "nightly Postgres backups shell out to pg_dump; Neon is PG18 and "
            "Debian slim's default client is older (version mismatch, exit 1)"
        )
        assert "pgdg.asc" in dockerfile and "signed-by=" in dockerfile, (
            "python slim often has no gpg binary; write the PGDG ASCII key and "
            "let apt signed-by consume it"
        )
        assert "VERSION_CODENAME" in dockerfile, (
            "PGDG suite must match the image (bookworm vs trixie), not a hardcoded distro"
        )

    def test_smoke_and_docs_point_at_the_live_api_host(self):
        """The AGENTS.md deploy gate must not advertise a 404 hostname."""
        smoke = (REPO_ROOT / "scripts/post_deploy_smoke.py").read_text(encoding="utf-8")
        agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        dead = "cerebrumdev-backend.onrender.com"
        live = "https://api.cerebrum-dev.com"
        assert live in smoke and dead not in smoke
        assert "TRANSIENT" in smoke and "502" in smoke, (
            "live GET /product 502s immediately after LLM draft; smoke must retry"
        )
        assert live in agents and dead not in agents
        assert "https://cerebrum-dev.com" in readme
        assert dead not in readme

    def test_backups_are_scheduled_in_process_not_as_a_cron(self):
        """The backup vehicle must be one that can actually reach the data.

        The previous shape asserted a `type: cron` backup existed in
        render.yaml — but that design can never run on Render: cron jobs
        cannot mount persistent disks, and a disk is readable by exactly one
        service. A green test was certifying a backup that was impossible to
        take. The correct shape is the inverse invariant: no cron in the
        blueprint may claim a disk, and the web service (the only process
        that can see /app/storage) must arm the in-process scheduler.
        """
        import yaml

        render = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
        for service in render["services"]:
            if service.get("type") == "cron":
                assert "disk" not in service, (
                    f"cron '{service.get('name')}' declares a disk; Render cron "
                    "jobs cannot mount disks, so this job can never do its work"
                )

        from app.main import _lifespan, app as fastapi_app

        # FastAPI lifespan (not the deprecated on_startup list) arms backup.
        import inspect

        lifespan_src = inspect.getsource(_lifespan)
        assert "backup_scheduler.start" in lifespan_src, (
            "the web service does not arm the in-process backup scheduler in "
            f"lifespan; seen: {lifespan_src[:400]}"
        )
        assert fastapi_app.router.lifespan_context is not None

        web = [s for s in render["services"] if s.get("name") == "cerebrumdev-backend"]
        keys = {e.get("key") for e in web[0].get("envVars", [])}
        assert "BACKUP_SCHEDULE_ENABLED" in keys, (
            "blueprint does not pin BACKUP_SCHEDULE_ENABLED on the web service"
        )

    def test_every_scheduled_entry_point_is_actually_in_the_image(self):
        """Scheduled jobs must be able to import what they run.

        `backend/scripts/` was not copied into the image while render.yaml
        scheduled `python -m scripts.backup_cli`. The job would have failed at
        import every single night -- and `notifyOnFail: default` only covers
        deploy failures, so nothing would have said so. The backup would have
        looked configured and produced nothing.

        This checks the general property rather than that one path: whatever
        module a cron dockerCommand runs, the directory holding it is COPYed.
        """
        import re

        import yaml

        render = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        copied = {
            line.split()[1].strip() for line in dockerfile.splitlines()
            if line.strip().startswith("COPY") and len(line.split()) >= 3
        }

        for service in render["services"]:
            if service.get("type") != "cron":
                continue
            command = service.get("dockerCommand", "")
            match = re.search(r"-m\s+([A-Za-z0-9_]+)[.\s]", command)
            if not match:
                continue
            package = match.group(1)
            assert any(c.endswith(f"backend/{package}") or c.endswith(package) for c in copied), (
                f"cron '{service.get('name')}' runs `python -m {package}...` but no "
                f"COPY puts {package}/ in the image; the job cannot import it. "
                f"COPY targets seen: {sorted(copied)}"
            )
