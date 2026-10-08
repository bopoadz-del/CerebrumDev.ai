"""A coding agent's shell never shares the Factory server's process group.

Live 2026-10-07 22:03:48 UTC and 2026-10-08 03:19:21 UTC (cerebrumdev-backend,
no deploy, no OOM): the server logged a clean ``Shutting down`` while /ready
was answering 200 every 30 s, the ALB never counted the target unhealthy
(UnHealthyHostCount 0 throughout) and CPU was 8-20 % at the moment. ECS
recorded ``EssentialContainerExited`` (exit 0) -- the container stopped
itself; ECS did not stop it. Both times a WRITER CLI was mid-pass. The CLI
is a child of the uvicorn process (PID 1) and inherited its process group,
so any group-wide signal the agent's shell sends -- ``kill 0``, a
``trap 'kill 0' EXIT`` cleanup around a probe server, ``kill -- -$PGID`` --
reaches the Factory server too, and every in-flight build dies with it.
(The writer prompt's v4 PROCESSES rule recorded the same signature on
FleetOps; a prompt is advice, not isolation.)

Every coding-agent subprocess therefore starts in its own session, and the
Factory stops an agent by signalling that session's whole group, so the
probe servers it started die with it instead of outliving the pass.
"""

from __future__ import annotations

import os
import signal
from typing import Any, Dict

#: Popen keyword that puts the child in a new session (POSIX setsid): its own
#: process group, no controlling terminal shared with the server.
_NEW_SESSION = "start_new_session"


def _posix() -> bool:
    return os.name == "posix"


def agent_popen_kwargs() -> Dict[str, Any]:
    """Popen keywords for any subprocess that runs a coding agent's shell."""
    if _posix():
        return {_NEW_SESSION: True}
    return {}


def _signal_group(proc: Any, sig: int) -> bool:
    """Signal the agent's whole process group; False when that is not
    possible (no real pid, not POSIX, group already gone)."""
    pid = getattr(proc, "pid", None)
    killpg = getattr(os, "killpg", None)
    if not _posix() or not isinstance(pid, int) or pid <= 0 or killpg is None:
        return False
    # The agent leads its own session, so its group id is its pid. Never
    # signal a group that is not the agent's own (it would be ours).
    try:
        if os.getpgid(pid) != pid:
            return False
    except ProcessLookupError:
        # The leader already exited; its group outlives it while anything
        # it started is still running, and still carries the leader's pid.
        pass
    except OSError:
        return False
    try:
        killpg(pid, sig)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def kill_agent_tree(proc: Any) -> None:
    """Hard-stop the agent and everything it started."""
    sig = getattr(signal, "SIGKILL", signal.SIGTERM)
    if not _signal_group(proc, sig):
        proc.kill()


def terminate_agent_tree(proc: Any) -> None:
    """Ask the agent and everything it started to stop."""
    if not _signal_group(proc, signal.SIGTERM):
        proc.terminate()
