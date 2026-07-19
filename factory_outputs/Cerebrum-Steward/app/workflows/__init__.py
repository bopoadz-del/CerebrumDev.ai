WORKFLOWS = [
  {
    "description": "Compose registry \u2192 maintenance \u2192 readiness with human authority gate.",
    "name": "Estate operations loop",
    "steps": [
      {
        "capability_id": "estate_registry",
        "role": "inventory"
      },
      {
        "capability_id": "estate_maintenance",
        "role": "plan_work"
      },
      {
        "capability_id": "readiness_engine",
        "role": "gate"
      },
      {
        "capability_id": "human_authority_gate",
        "required": true,
        "role": "confirm"
      }
    ],
    "workflow_id": "estate.ops_loop"
  },
  {
    "description": "Capture artifacts then verify without PII in learning.",
    "name": "Evidence capture and verify",
    "steps": [
      {
        "capability_id": "evidence_capture",
        "role": "capture"
      },
      {
        "capability_id": "evidence_verifier",
        "role": "verify"
      }
    ],
    "workflow_id": "estate.evidence_chain"
  },
  {
    "description": "Roll up readiness and maintenance across properties.",
    "name": "Portfolio rollup",
    "steps": [
      {
        "capability_id": "portfolio_rollup",
        "role": "aggregate"
      }
    ],
    "workflow_id": "estate.portfolio_rollup"
  }
]
