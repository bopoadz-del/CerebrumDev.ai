# Pending sibling-repo pushes

This agent can push to CerebrumDev.ai but received **HTTP 403** when pushing to:

- `bopoadz-del/TEKsystems_GlobalRetailMNC` (Milestone 0B scrub)
- `bopoadz-del/Cerebrum-Blocks` (estate blocks + private_estate_operations kit)
- Creating `bopoadz-del/Cerebrum-Steward` (GitHub API 403)

## Apply locally

```bash
# TEKsystems 0B
cd TEKsystems_GlobalRetailMNC
git am ../CerebrumDev.ai/docs/provenance/pending_sibling_pushes/teksystems-scrub-0b.patch
git push -u origin cursor/scrub-brands-cities-bd2c

# Cerebrum-Blocks estate kit
cd Cerebrum-Blocks
git am ../CerebrumDev.ai/docs/provenance/pending_sibling_pushes/cerebrum-blocks-estate.patch
git push -u origin cursor/estate-blocks-steward-bd2c

# Steward output already checked into CerebrumDev.ai:
# factory_outputs/Cerebrum-Steward — publish as bopoadz-del/Cerebrum-Steward when ready.
```
