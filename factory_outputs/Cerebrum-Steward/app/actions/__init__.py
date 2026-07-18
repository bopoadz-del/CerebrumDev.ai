ACTION_CATALOG = [
  {
    "action_id": "estate.estate_registry",
    "block_ids": [
      "estate_registry"
    ],
    "capability_id": "estate_registry",
    "notes": "dual-registered",
    "strategy": "REUSE"
  },
  {
    "action_id": "estate.estate_maintenance",
    "block_ids": [
      "estate_maintenance"
    ],
    "capability_id": "estate_maintenance",
    "notes": "dual-registered",
    "strategy": "REUSE"
  },
  {
    "action_id": "estate.evidence_verifier",
    "block_ids": [
      "evidence_verifier"
    ],
    "capability_id": "evidence_verifier",
    "notes": "dual-registered",
    "strategy": "REUSE"
  },
  {
    "action_id": "estate.readiness_engine",
    "block_ids": [
      "readiness_engine"
    ],
    "capability_id": "readiness_engine",
    "notes": "dual-registered",
    "strategy": "REUSE"
  },
  {
    "action_id": "estate.portfolio_rollup",
    "block_ids": [
      "portfolio_rollup"
    ],
    "capability_id": "portfolio_rollup",
    "notes": "dual-registered",
    "strategy": "REUSE"
  },
  {
    "action_id": "estate.composed_ops_loop",
    "block_ids": [
      "estate_registry",
      "estate_maintenance",
      "readiness_engine"
    ],
    "capability_id": "composed_ops_loop",
    "notes": "dual-registered",
    "strategy": "COMPOSE"
  },
  {
    "action_id": "estate.evidence_capture",
    "block_ids": [
      "capture",
      "evidence_verifier"
    ],
    "capability_id": "evidence_capture",
    "notes": "dual-registered",
    "strategy": "COMPOSE"
  },
  {
    "action_id": "estate.human_authority_gate",
    "block_ids": [],
    "capability_id": "human_authority_gate",
    "notes": "kernel/template generation (no block ids)",
    "strategy": "GENERATE"
  }
]
