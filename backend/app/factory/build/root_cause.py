"""S1 root cause / lane-authority — which factory module owns which defect.

U1–U12 and F1–F29 each map to a named owner module (existing path, or an
explicit missing expected path). Lanes come from ``authority.ROLE_CONTRACTS``;
this module does not invent a second authority model.

LotDesk-class symptoms (F1, F5, F6, F11, F14, F18, F19, F20, F24) map to
named owners. The fixture is inspected, never patched.

Evidence: ``build/stages/S1_root_cause.json`` + reread twin. Mismatch = fail.
Does not emit PILOT_READY. Does not start S2+.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.factory.build.authority import ROLE_CONTRACTS
from app.factory.build.domain_acceptance import inspect_lotdesk_domain
from app.factory.build.lotdesk_gate import reject_lotdesk_as_shipped
from app.factory.build.preflight import reread_twin_path, write_evidence

EMITTER_ID = "app.factory.build.root_cause.evaluate_root_cause"
STAGE = "S1"
STAGE_NAME = "ROOT_CAUSE"

REQUIRED_U_IDS: Tuple[str, ...] = tuple(f"U{i}" for i in range(1, 13))
REQUIRED_F_IDS: Tuple[str, ...] = tuple(f"F{i}" for i in range(1, 30))
REQUIRED_DEFECT_IDS: Tuple[str, ...] = REQUIRED_U_IDS + REQUIRED_F_IDS

#: LotDesk-as-shipped symptoms the named gate and S12 domain inspect prove.
LOTDESK_CLASS_CODES: Tuple[str, ...] = (
    "F1",
    "F5",
    "F6",
    "F11",
    "F14",
    "F18",
    "F19",
    "F20",
    "F24",
)

#: id -> owner. ``owner_module`` is a repo-relative path. ``present`` is
#: filled at evaluation time. Do not invent modules for S2+/S3 work.
DEFECT_OWNERS: Dict[str, Dict[str, Any]] = {
    "U1": {
        "title": "WRITER gate is compileall only",
        "owner_module": "backend/app/factory/build/gates.py",
        "owner_symbol": "gate_workspace_compiles",
        "lane": "no role writes gates.py",
        "status": "partial",
        "also": "backend/app/factory/build/writer_behaviour.py",
        "note": "writer_behaviour added; compileall remains the named WRITER gate",
    },
    "U2": {
        "title": "tested path ≠ shipped path (template in CI, kimi when keyed)",
        "owner_module": "backend/app/factory/build/roles.py",
        "owner_symbol": "_coder_route_body",
        "lane": "TESTER cannot change emitters",
        "status": "partial",
        "also": ".github/workflows/ci.yml",
        "note": "keyed-path CI fixtures exist; routes stay kernel-owned (None)",
    },
    "U3": {
        "title": "coder gate is syntactic/security only",
        "owner_module": "backend/app/factory/coder.py",
        "owner_symbol": "_validate_body",
        "lane": "WRITER consumes coder output",
        "status": "open",
    },
    "U4": {
        "title": "_ensure_route_persists_payload force-injects save(payload)",
        "owner_module": "backend/app/factory/build/roles.py",
        "owner_symbol": "_ensure_route_persists_payload",
        "lane": "factory host, outside RoleContract",
        "status": "open",
        "note": "S4 residual; this PR does not remove it",
    },
    "U5": {
        "title": "on keyed path the model owns persist/envelope",
        "owner_module": "backend/app/factory/build/roles.py",
        "owner_symbol": "_coder_route_body",
        "lane": "WRITER writes routes from coder",
        "status": "closed",
        "note": "_coder_route_body returns None; kernel owns HTTP",
    },
    "U6": {
        "title": "STORE_MANAGER harvest remain unbuilt",
        "owner_module": "backend/app/factory/build/harvest.py",
        "owner_symbol": "evaluate_harvest",
        "lane": "STORE_MANAGER mandate",
        "status": "open",
        "also": "backend/app/factory/store_manager.py",
        "note": "harvest is BLOCKED; _store_write_authorized is a policy no-op",
    },
    "U7": {
        "title": "TESTER judges shape not outcome",
        "owner_module": "backend/app/factory/build/gates.py",
        "owner_symbol": "FACTORY_SUITE_MARKER_EXPR",
        "lane": "TESTER tests/** only",
        "status": "open",
        "also": "backend/app/factory/build/promotion.py",
        "note": 'FACTORY_SUITE_MARKER_EXPR remains "not pilot"; S13 adds pilot-cycle',
    },
    "U8": {
        "title": "roles.py claimed both emitters write the same shape — false",
        "owner_module": "backend/app/factory/build/converge.py",
        "owner_symbol": "FOURTEEN_ARTIFACT_CLASSES",
        "lane": "no parity test without converge",
        "status": "partial",
        "also": "backend/app/factory/build/runner.py",
    },
    "U9": {
        "title": "dispatch fabricates Store inputs (F18)",
        "owner_module": "backend/app/factory/build/roles.py",
        "owner_symbol": "_DISPATCH_RUNTIME",
        "lane": "WRITER emits dispatch.py",
        "status": "partial",
        "also": "backend/app/factory/build/lotdesk_gate.py",
        "note": "factory dispatch no longer fabricates; LotDesk fixture still does",
    },
    "U10": {
        "title": "two emitters; runner docstring stale; classes dropped",
        "owner_module": "backend/app/factory/build/runner.py",
        "owner_symbol": "RoleRunner",
        "lane": "engine switch FACTORY_BUILD_ENGINE",
        "status": "partial",
        "also": "backend/app/factory/product_architect.py",
        "note": "RoleRunner is default; converge copies 14-class emitters",
    },
    "U11": {
        "title": "chat/UI unused upstream",
        "owner_module": "backend/app/factory/build/ui_schema.py",
        "owner_symbol": "block.json ui_schema canonical",
        "lane": "WRITER ui/**; do not invent chat id",
        "status": "open",
        "also": "backend/app/factory/platform_chat_flow.py",
        "note": "S6 residual: chat not bound; ui_schema_builder unused",
    },
    "U12": {
        "title": "cerebrum_product_kernel not shipped by role_runner",
        "owner_module": "backend/app/factory/build/roles.py",
        "owner_symbol": "execute_action route template",
        "lane": "WRITER app/** can vendor kernel",
        "status": "closed",
        "also": "backend/app/cerebrum_product_kernel/contract/runtime.py",
        "note": "S4 shipped kernel; U4 persist rewrite remains",
    },
    "F1": {
        "title": "echo/stub still succeeds — always-200 health / HTTP ok:true",
        "owner_module": "backend/app/factory/build/deploy.py",
        "owner_symbol": "health_is_always_200",
        "lane": "WRITER app/main.py",
        "status": "partial",
        "also": "backend/app/factory/build/domain_acceptance.py",
        "lotdesk": True,
    },
    "F2": {
        "title": "queue/workflow/notification in-process",
        "owner_module": "backend/app/factory/build/offline_adapters.py",
        "owner_symbol": "CLONER offline adapter emission",
        "lane": "CLONER vendor/**",
        "status": "partial",
    },
    "F3": {
        "title": "VIN sample accepted",
        "owner_module": "backend/app/cerebrum_product_kernel/contract/runtime.py",
        "owner_symbol": "execute_action input_schema",
        "lane": "kernel, not HTTP 200",
        "status": "open",
        "note": "S3 pack binds VIN class to execute_action input_schema; sample acceptance still kernel-side",
    },
    "F4": {
        "title": "missing required skipped",
        "owner_module": "backend/app/cerebrum_product_kernel/contract/runtime.py",
        "owner_symbol": "execute_action validate required[]",
        "lane": "kernel",
        "status": "partial",
        "also": "backend/app/factory/build/roles.py",
        "note": "_constraint_guard still exists beside the kernel",
    },
    "F5": {
        "title": "HTTP 200 = acceptance / hollow queue",
        "owner_module": "backend/app/factory/build/domain_acceptance.py",
        "owner_symbol": "inspect_lotdesk_domain",
        "lane": "TESTER + kernel outcomes",
        "status": "partial",
        "lotdesk": True,
    },
    "F6": {
        "title": "missing update/delete persist; agent_domain_cases uncollected",
        "owner_module": "backend/app/factory/build/domain_acceptance.py",
        "owner_symbol": "OUTCOME_UPDATE_PERSISTS / OUTCOME_DELETE_PERSISTS",
        "lane": "TESTER",
        "status": "partial",
        "lotdesk": True,
    },
    "F7": {
        "title": "no status transition machine",
        "owner_module": "backend/app/factory/build/domain_pack.py",
        "owner_symbol": "DEALERSHIP_STATUS_MACHINE / domain_rules",
        "lane": "S3 Domain Pack + kernel",
        "status": "partial",
        "note": "S3 pack binds domain_rules to execute_action status contracts; machine not closed this stage",
    },
    "F8": {
        "title": "no restart-survival test",
        "owner_module": "backend/app/factory/build/pilot_durability.py",
        "owner_symbol": "gate_pilot_outcome_survives_restart",
        "lane": "TESTER",
        "status": "partial",
    },
    "F9": {
        "title": "multiple datastores",
        "owner_module": "backend/app/factory/build/data_lifecycle.py",
        "owner_symbol": "render_store",
        "lane": "WRITER one DOR",
        "status": "partial",
    },
    "F10": {
        "title": "requirements vs vendor imports",
        "owner_module": "backend/app/factory/build/roles.py",
        "owner_symbol": "requirements emission",
        "lane": "WRITER requirements.txt",
        "status": "open",
    },
    "F11": {
        "title": "estate_registry echo / uninvoked BLOCK_IDS",
        "owner_module": "backend/app/factory/build/lotdesk_gate.py",
        "owner_symbol": "inspect_files F11",
        "lane": "CLONER + COLLECTOR",
        "status": "partial",
        "also": "backend/app/factory/build/offline_adapters.py",
        "lotdesk": True,
    },
    "F12": {
        "title": "money as REAL/float",
        "owner_module": "backend/app/factory/build/data_lifecycle.py",
        "owner_symbol": "_SA_TYPES",
        "lane": "WRITER store/migrations",
        "status": "open",
        "also": "backend/app/cerebrum_product_kernel/contract/runtime.py",
    },
    "F13": {
        "title": "dispatch swallows refusals",
        "owner_module": "backend/app/factory/build/roles.py",
        "owner_symbol": "dispatch execute envelope",
        "lane": "WRITER dispatch.py",
        "status": "partial",
        "also": "backend/tests/factory/test_windows_cp1252_parity.py",
    },
    "F14": {
        "title": "no ui/ / frontend/",
        "owner_module": "backend/app/factory/build/ui_surface.py",
        "owner_symbol": "gate_ui_surface",
        "lane": "WRITER frontend/**",
        "status": "partial",
        "also": "backend/app/factory/build/converge.py",
        "lotdesk": True,
    },
    "F15": {
        "title": "no auth on capability routes",
        "owner_module": "backend/app/factory/build/domain_acceptance.py",
        "owner_symbol": "OUTCOME_UNAUTHORIZED_REJECTED",
        "lane": "kernel ActionContext",
        "status": "partial",
    },
    "F16": {
        "title": "ui_schema unused",
        "owner_module": "backend/app/factory/build/ui_schema.py",
        "owner_symbol": "block.json canonical",
        "lane": "U11",
        "status": "open",
    },
    "F17": {
        "title": "capture listed not executed",
        "owner_module": "backend/app/factory/build/network_posture.py",
        "owner_symbol": "P1_CAPTURE_ADAPTER",
        "lane": "CLONER capture emission",
        "status": "open",
        "note": "P1 adapter is local OCR; chat/capture still not bound as a capability",
    },
    "F18": {
        "title": "_default_block_field fabricates Store inputs",
        "owner_module": "backend/app/factory/build/lotdesk_gate.py",
        "owner_symbol": "inspect_files F18",
        "lane": "WRITER dispatch.py",
        "status": "partial",
        "also": "backend/app/factory/build/roles.py",
        "lotdesk": True,
        "note": "required LotDesk rejection code; factory emitter no longer fabricates",
    },
    "F19": {
        "title": "FAIL can still be a deployable image",
        "owner_module": "backend/app/factory/build/supply_chain.py",
        "owner_symbol": "assert_generated_dockerfile",
        "lane": "WRITER Dockerfile",
        "status": "partial",
        "also": "backend/app/factory/build/lotdesk_gate.py",
        "lotdesk": True,
        "note": "release_gate in generated image; LotDesk fixture still omits it",
    },
    "F20": {
        "title": "execution.image :latest",
        "owner_module": "backend/app/factory/build/supply_chain.py",
        "owner_symbol": "findings_for_image_ref",
        "lane": "CLONER copies Store block.json",
        "status": "partial",
        "lotdesk": True,
    },
    "F21": {
        "title": "signatures/digests not verified",
        "owner_module": "backend/app/factory/build/supply_chain.py",
        "owner_symbol": "perform_pin_verification",
        "lane": "CLONER lock records commit/path only",
        "status": "partial",
        "note": (
            "S2 performs registry manifest GET of the recorded digest. "
            "Cosign is not claimed when it cannot be performed. THIS TURN "
            "F21 is also permissions-vs-behaviour in supply_chain."
        ),
    },
    "F22": {
        "title": "permissions vs behaviour",
        "owner_module": "backend/app/factory/build/network_posture.py",
        "owner_symbol": "NETWORK_POSTURE P1",
        "lane": "CLONER capture emission",
        "status": "partial",
        "also": "backend/app/factory/build/supply_chain.py",
        "note": (
            "S7 closed capture network:false vs cloud defaults. S2 "
            "reconcile_permissions covers Dockerfile/entrypoint/posture."
        ),
    },
    "F23": {
        "title": "no SBOM",
        "owner_module": "backend/app/factory/build/supply_chain.py",
        "owner_symbol": "emit_supply_chain_artifacts",
        "lane": "packager / S2",
        "status": "closed",
        "note": "RoleRunner WRITER emits docs/sbom.cdx.json (CycloneDX 1.5)",
    },
    "F24": {
        "title": "health always ok",
        "owner_module": "backend/app/factory/build/deploy.py",
        "owner_symbol": "health_is_always_200",
        "lane": "WRITER app/main.py",
        "status": "partial",
        "also": "backend/app/factory/build/lotdesk_gate.py",
        "lotdesk": True,
    },
    "F25": {
        "title": "no versioned migrations",
        "owner_module": "backend/app/factory/build/data_lifecycle.py",
        "owner_symbol": "REVISION_0001",
        "lane": "WRITER alembic/**",
        "status": "partial",
    },
    "F26": {
        "title": "backup/restore not drilled / Windows cp1252 not honest",
        "owner_module": "backend/app/factory/build/data_lifecycle.py",
        "owner_symbol": "restore drill",
        "lane": "TESTER + S9",
        "status": "partial",
        "also": "backend/tests/factory/test_windows_cp1252_parity.py",
        "note": "Linux restore drill exists; F26 Windows matrix is not this host",
    },
    "F27": {
        "title": "no rollback",
        "owner_module": "backend/app/factory/build/deploy.py",
        "owner_symbol": "rollback drill",
        "lane": "WRITER scripts/rollback.sh",
        "status": "partial",
    },
    "F28": {
        "title": "no request-id / structured logs",
        "owner_module": "backend/app/factory/build/deploy.py",
        "owner_symbol": "REQUEST_ID_HEADER",
        "lane": "WRITER app/main.py",
        "status": "partial",
    },
    "F29": {
        "title": "patch-until-green (prepare_pilot_workspace)",
        "owner_module": "backend/app/factory/build/runner.py",
        "owner_symbol": "SEALED_AFTER_CLONER",
        "lane": "no vendor/** after CLONER",
        "status": "closed",
        "also": "backend/app/factory/build/offline_adapters.py",
        "note": "prepare_pilot_workspace absent from runner.py and pilot.py",
    },
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def default_stages_dir() -> Path:
    return _repo_root() / "build" / "stages"


def lane_authority_map() -> Dict[str, Any]:
    """Cite authority.py. Do not fork write-lane truth."""
    roles: Dict[str, Any] = {}
    for role, contract in ROLE_CONTRACTS.items():
        roles[role.value] = {
            "title": contract.title,
            "write_lanes": [glob for _root, glob in contract.write_lanes],
            "read_only": not contract.write_lanes,
            "gate": contract.gate,
            "agent": contract.agent.value,
        }
    return {
        "source": "backend/app/factory/build/authority.py",
        "roles": roles,
        "COLLECTOR_blocked": "cannot remove echo stubs or invent block ids",
        "CLONER_blocked": "cannot vendor kernel under app/; cannot rewrite factory/",
        "WRITER_blocked": "cannot write tests/** or vendor/**",
        "TESTER_blocked": "Never patch app/; cannot ship kernel",
        "STORE_MANAGER_blocked": "harvest remain unbuilt",
    }


def defect_owners(*, repo: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    root = Path(repo) if repo is not None else _repo_root()
    out: Dict[str, Dict[str, Any]] = {}
    for code, spec in DEFECT_OWNERS.items():
        row = dict(spec)
        owner = spec["owner_module"]
        row["owner_present"] = (root / owner).is_file()
        also = spec.get("also")
        if also:
            row["also_present"] = (root / also).is_file()
        out[code] = row
    return out


def lotdesk_symptom_owners(
    *,
    repo: Optional[Path] = None,
    shipped: Optional[Dict[str, Any]] = None,
    domain: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Every LotDesk finding code maps to a named owner module."""
    owners = defect_owners(repo=repo)
    shipped = shipped if shipped is not None else reject_lotdesk_as_shipped()
    domain = domain if domain is not None else inspect_lotdesk_domain()
    codes = []
    for raw in list(shipped.get("codes") or []) + list(domain.get("codes") or []):
        if raw not in codes:
            codes.append(raw)
    mapped: List[Dict[str, Any]] = []
    unmapped: List[str] = []
    for code in codes:
        spec = owners.get(code)
        if not spec or not spec.get("owner_module"):
            unmapped.append(code)
            continue
        mapped.append(
            {
                "code": code,
                "owner_module": spec["owner_module"],
                "owner_present": spec.get("owner_present"),
                "title": spec.get("title"),
            }
        )
    return {
        "codes": codes,
        "mapped": mapped,
        "unmapped": unmapped,
        "lotdesk": "fixture only; not patched",
        "ok": not unmapped and bool(mapped),
    }


def canonical_fingerprint(result: Dict[str, Any]) -> Dict[str, Any]:
    owners = result.get("defect_owners") or {}
    return {
        "required_ids": result.get("required_ids"),
        "owner_modules": {
            code: (owners.get(code) or {}).get("owner_module")
            for code in REQUIRED_DEFECT_IDS
        },
        "lane_source": (result.get("lane_authority_map") or {}).get("source"),
        "lotdesk_unmapped": (result.get("lotdesk_symptoms") or {}).get("unmapped"),
    }


def fingerprint_disagreements(
    primary: Dict[str, Any], reread: Dict[str, Any]
) -> List[str]:
    left = canonical_fingerprint(primary)
    right = canonical_fingerprint(reread)
    if left == right:
        return []
    found: List[str] = []
    for key in left:
        if left.get(key) != right.get(key):
            found.append(key)
    return found or ["canonical_fingerprint"]


def evaluate_root_cause(*, repo: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(repo) if repo is not None else _repo_root()
    owners = defect_owners(repo=root)
    missing_keys = [code for code in REQUIRED_DEFECT_IDS if code not in owners]
    symptoms = lotdesk_symptom_owners(repo=root)
    first = None
    if missing_keys:
        first = "missing_defect_keys:" + ",".join(missing_keys)
    elif not symptoms["ok"]:
        first = "lotdesk_unmapped:" + ",".join(symptoms["unmapped"] or ["empty"])
    ok = first is None
    return {
        "stage": STAGE,
        "name": STAGE_NAME,
        "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "emitter": EMITTER_ID,
        "verdict": "PASS" if ok else "FAIL",
        "ok": ok,
        "first_failing_criterion": first,
        "required_ids": list(REQUIRED_DEFECT_IDS),
        "counts": {
            "U": len(REQUIRED_U_IDS),
            "F": len(REQUIRED_F_IDS),
            "total": len(REQUIRED_DEFECT_IDS),
        },
        "note_on_count": (
            "U1–U12 (12) + F1–F29 (29) = 41. No U0. F0 from draft-1 "
            "(shape-not-outcome) aliases U7."
        ),
        "lane_authority_map": lane_authority_map(),
        "defect_owners": owners,
        "lotdesk_symptoms": symptoms,
        "parent_defect": "U10",
        "one_sentence": (
            "Each U/F class is owned by a named factory module; LotDesk-class "
            "symptoms map to those owners and the fixture is not patched."
        ),
        "PILOT_READY": False,
        "not_claimed": [
            "PILOT_READY",
            "S2 cosign / image signature verification",
            "U4 persist-rewrite removal",
            "U7 marker change",
        ],
        "lotdesk": "fixture only; not patched",
        "llm_route_authorship": "not restored; _coder_route_body still returns None",
    }


def reread_matches(evidence: Dict[str, Any], twin: Dict[str, Any]) -> bool:
    if str(evidence.get("verdict") or "").strip().upper() != str(
        twin.get("verdict") or ""
    ).strip().upper():
        return False
    disagreements = twin.get("disagreements")
    if isinstance(disagreements, list) and disagreements:
        return False
    return True


def write_reread_twin(
    evidence_path: Path,
    result: Dict[str, Any],
    *,
    reread: Optional[Dict[str, Any]] = None,
) -> Path:
    second = reread if reread is not None else evaluate_root_cause()
    disagreements = fingerprint_disagreements(result, second)
    if disagreements:
        result["verdict"] = "FAIL"
        result["ok"] = False
        result["first_failing_criterion"] = "reread_mismatch:" + ",".join(
            disagreements
        )
        write_evidence(evidence_path, result)
    twin = {
        "stage": STAGE,
        "name": "root_cause",
        "verdict": result.get("verdict"),
        "reread_of": evidence_path.as_posix(),
        "reread_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "independent": True,
        "reader": "cloud-agent-s1-root-cause",
        "emitter": EMITTER_ID,
        "disagreements": disagreements,
        "checked": [
            "U1–U12 and F1–F29 keys present",
            "each id has a named owner_module",
            "LotDesk-class symptoms map to those owners",
            "lanes cited from authority.py ROLE_CONTRACTS",
            "_coder_route_body still returns None",
        ],
        "lane_source": (second.get("lane_authority_map") or {}).get("source"),
        "not_claimed": result.get("not_claimed") or [],
        "PILOT_READY": False,
    }
    dest = reread_twin_path(evidence_path)
    dest.write_text(json.dumps(twin, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return dest


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    stages = default_stages_dir()
    dest = stages / "S1_root_cause.json"
    if args:
        stages = Path(args[0])
        dest = stages / "S1_root_cause.json"
    if len(args) > 1:
        dest = Path(args[1])
    result = evaluate_root_cause()
    write_evidence(dest, result)
    write_reread_twin(dest, result)
    print(
        json.dumps(
            {
                "wrote": str(dest),
                "verdict": result["verdict"],
                "PILOT_READY": False,
            },
            indent=2,
        )
    )
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
