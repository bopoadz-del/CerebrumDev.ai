"""Tenant store resolution — the Phase 1 isolation seam (option B).

Physical partition: one SQLite file per tenant and one Chroma collection per
tenant, resolved from the authenticated principal at connection time. The
resolver takes the PRINCIPAL (or an auth-layer-issued token), never a raw
``tenant_id`` string: any call site that can forward a string is a bypass.

Per-request open/close; no engine cache, so there is nothing to bound.
The shared control-plane tables (pilot tokens / principals) deliberately
stay on the auth engine — corpus, documents, chunks, embeddings, formulas
and procedures live only behind this seam.

Named refusals (Phase 0.5's R3: verdicts are reason strings, never booleans).
"""

from __future__ import annotations

import hashlib
import os
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

#: Refused: a caller tried to address a store by naming it.
CLIENT_SUPPLIED_STORE_NAME = "client_supplied_store_name"

#: Refused: no authenticated tenant is bound for this request.
NO_AUTHENTICATED_TENANT = "no_authenticated_tenant"

#: Refused: the store addressed is not this tenant's own.
TENANT_STORE_NOT_ADDRESSABLE = "tenant_store_not_addressable"


class TenantStoreError(ValueError):
    """A named refusal from the isolation seam."""


def _digest(tenant_id: str, estate_id: str) -> str:
    key = f"{tenant_id}:{estate_id}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class TenantStore:
    """One tenant's physical store: SQLite file + Chroma collection.

    Constructed only by :func:`resolve_tenant_store` from an authenticated
    principal — a caller can never name one directly.
    """

    tenant_id: str
    estate_id: str
    digest: str
    storage_root: Path
    sqlite_path: Path
    chroma_collection: str

    @property
    def sqlalchemy_url(self) -> str:
        return "sqlite:///" + self.sqlite_path.resolve().as_posix()


def resolve_tenant_store(principal: Any) -> TenantStore:
    """Map an authenticated principal to its tenant's physical store.

    Refuses anything that is not an authenticated principal: the input is
    identity, never a name. The principal's own ``tenant_id``/``estate_id``
    fields are the only source of the digest.
    """
    principal_class = _principal_class()
    if principal_class is None or not isinstance(principal, principal_class):
        raise TenantStoreError(
            f"{CLIENT_SUPPLIED_STORE_NAME}: store resolution requires the "
            "authenticated principal — raw tenant names are never an input"
        )
    tenant = str(getattr(principal, "tenant_id", "") or "").strip()
    estate = str(getattr(principal, "estate_id", "") or "").strip()
    if not tenant:
        raise TenantStoreError(
            f"{NO_AUTHENTICATED_TENANT}: principal has no tenant scope"
        )
    digest = _digest(tenant, estate or tenant)
    root = Path(os.getenv("STORAGE_PATH", "./data")) / "tenants" / digest
    return TenantStore(
        tenant_id=tenant,
        estate_id=estate,
        digest=digest,
        storage_root=root,
        sqlite_path=root / "platform.db",
        chroma_collection=f"tenant_{digest}",
    )


def _principal_class() -> Optional[type]:
    """The kit's AuthenticatedPrincipal, imported lazily (module aliasing)."""
    try:
        from app.steward.auth import AuthenticatedPrincipal

        return AuthenticatedPrincipal
    except ImportError:  # pragma: no cover - kit not on path
        return None


#: The store bound FOR THE REQUEST. Unset = unauthenticated = no store.
_bound_tenant_store: ContextVar[Optional[TenantStore]] = ContextVar(
    "steward_tenant_store", default=None
)


def bind_tenant_store(store: TenantStore) -> None:
    """Bind a resolved store for the current request scope."""
    if store is None or not isinstance(store, TenantStore):
        raise TenantStoreError(
            f"{CLIENT_SUPPLIED_STORE_NAME}: bind requires a resolved TenantStore"
        )
    _bound_tenant_store.set(store)


def current_tenant_store() -> TenantStore:
    """The store bound for this request. Never a default, never shared."""
    store = _bound_tenant_store.get()
    if store is None:
        raise TenantStoreError(
            f"{NO_AUTHENTICATED_TENANT}: no tenant store is bound for this "
            "request — there is no default store"
        )
    return store


def assert_tenant_store_seam() -> None:
    """Boot probe (1.8/T1.4): the seam refuses client-supplied names.

    Structural sanity over the resolver itself: a raw string, a dict, or a
    fake object can never resolve to a store, and an unbound request has no
    store. If any of these slips through, the platform refuses to boot with
    the named reason instead of running with a god-path.
    """
    for candidate in ("tenant_a", "tenant_a:estate_a", {"tenant_id": "tenant_a"}):
        try:
            resolve_tenant_store(candidate)
        except TenantStoreError as exc:
            if CLIENT_SUPPLIED_STORE_NAME not in str(exc):
                raise
        else:
            raise TenantStoreError(
                f"{TENANT_STORE_NOT_ADDRESSABLE}: resolve_tenant_store accepted "
                f"a client-supplied name ({candidate!r}) — refusing to boot"
            )
    try:
        current_tenant_store()
    except TenantStoreError as exc:
        if NO_AUTHENTICATED_TENANT not in str(exc):
            raise
    else:
        raise TenantStoreError(
            f"{NO_AUTHENTICATED_TENANT}: an unbound request resolved a store — "
            "a default store exists; refusing to boot"
        )


def archive_path_for(store: TenantStore) -> Path:
    """Backup target INSIDE the tenant's own root — never shared storage.

    Lifecycle (1.6/T1.6): backup/restore/delete operate on the tenant's
    resolved root only. A restore cannot cross tenants because it can only
    address the store the resolver handed out.
    """
    return store.storage_root / "backups"


def delete_tenant_store(store: TenantStore) -> None:
    """Delete the tenant's own root and nothing else.

    Bounded to the resolved root: a tenant's delete can never reach another
    tenant's file or collection.
    """
    import shutil

    root = store.storage_root
    if root.exists():
        shutil.rmtree(root)
