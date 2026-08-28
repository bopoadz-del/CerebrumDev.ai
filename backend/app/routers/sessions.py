import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends

from ..core import accounts_store
from ..core.auth import Principal, require_api_key
from ..core.billing import require_entitled
from ..core.session_guard import owned_session_or_404
from ..core.session_store import _session_store, create_session, get_session
from ..models.session import SessionState

router = APIRouter()


@router.post("/", response_model=SessionState)
async def create_new_session(
    body: Optional[Dict[str, Any]] = Body(default=None),
    principal: Principal = Depends(require_entitled),
):
    # Creating a session is a paid action: require_entitled blocks expired
    # trials / canceled subscriptions with 402 when BILLING_ENFORCEMENT is on.
    owner = principal.account_id or "dev-local"
    session_id = f"sess_{uuid.uuid4().hex[:16]}"
    state = create_session(session_id=session_id, user_id=owner)
    # Honor a requested domain instead of silently defaulting: a caller who
    # asks for a retail session must not get a construction-flavored one.
    domain = (body or {}).get("domain")
    if isinstance(domain, str) and domain.strip():
        state.config.domain = domain.strip()
        from ..core.session_store import update_session

        update_session(session_id, state)
    return state


@router.get("/")
async def list_sessions(principal: Principal = Depends(require_api_key)) -> List[Dict[str, Any]]:
    """List sessions owned by the caller (admin/dev see all recorded sessions)."""
    if principal.kind == "user":
        ids = accounts_store.sessions_for_owner(principal.account_id or "")
    else:
        ids = set(_session_store.keys())
        try:
            ids.update(accounts_store.all_session_ids())
        except Exception:  # noqa: BLE001 — ownership index is advisory for admin listing
            pass
        ids = sorted(ids)
    out: List[Dict[str, Any]] = []
    for sid in ids:
        state = get_session(sid)
        if state is None:
            continue
        out.append(
            {
                "session_id": state.session_id,
                "user_id": state.user_id,
                "phase": getattr(state, "phase", None),
                "phase_status": getattr(state, "phase_status", None),
            }
        )
    return out


@router.get("/{session_id}", response_model=SessionState)
async def get_session_state(
    session_id: str, principal: Principal = Depends(require_api_key)
):
    return owned_session_or_404(session_id, principal)
