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


class TestHealthCheckPathPointsAtSomethingThatCanFail:
    def test_render_health_check_uses_the_endpoint_with_real_probes(self):
        import yaml

        render = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
        web = [s for s in render["services"] if s.get("name") == "cerebrumdev-backend"]
        assert web, "cerebrumdev-backend missing from render.yaml"
        assert web[0].get("healthCheckPath") == "/ready", (
            "health check must point at /ready; /health returns 200 even when degraded"
        )

    def test_a_backup_job_is_scheduled(self):
        import yaml

        render = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
        crons = [s for s in render["services"] if s.get("type") == "cron"]
        backup = [c for c in crons if "backup" in c.get("name", "")]
        assert backup, "no backup cron job is defined; user data has no copy"
        job = backup[0]
        assert job.get("schedule"), "backup job has no schedule"
        assert "backup_cli" in job.get("dockerCommand", ""), (
            "backup job does not invoke the backup CLI"
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
