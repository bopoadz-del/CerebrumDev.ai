# Kimi / Moonshot environment (Factory Product Architect)

This milestone’s Product Architect path is **Kimi-only** (`get_factory_llm_config()`).
Kit-chain chat may still use legacy providers when explicitly configured.

## Required

```bash
export KIMI_API_KEY='sk-...'          # or CEREBRUM_LLM_API_KEY
export KIMI_BASE_URL='https://api.moonshot.cn/v1'   # optional
export KIMI_MODEL='moonshot-v1-8k'                  # optional
export LLM_PROVIDER=kimi                            # optional but recommended
```

## Tests / offline

```bash
export KIMI_MOCK=1
```

## Helper

```bash
./scripts/setup_kimi_env.sh 'sk-your-key'
```

## Role reminder

| Actor | Job |
|-------|-----|
| **Kimi API** | Product Architect in CerebrumDev UI — blueprint only |
| **Kimi Code** | Factory Engineer **and Block Store Manager** |
| **CerebrumDev.ai** | Governance, dual registry, certify, regenerate |
