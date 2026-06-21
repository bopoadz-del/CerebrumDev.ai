from typing import List, Dict

# Mock block registry (in production, fetch from Cerebrum‑Blocks /v1/blocks)
BLOCK_REGISTRY = {
    "chat": {"id": "chat_v3", "category": "AI"},
    "ocr": {"id": "ocr_v2", "category": "Vision"},
    "pdf": {"id": "pdf_extractor", "category": "Document"},
    "google_drive": {"id": "google_drive_connector", "category": "Storage"},
    "local_drive": {"id": "local_file_reader", "category": "Storage"},
    "one_drive": {"id": "one_drive_connector", "category": "Storage"},
    "zvec": {"id": "zvec_block", "category": "AI"},
    "vector_search": {"id": "vector_search", "category": "AI"},
    "web_scraping": {"id": "web_scraper", "category": "Infra"},
    "email": {"id": "email_sender", "category": "Infra"},
    "webhook": {"id": "webhook_trigger", "category": "Infra"},
    "code_execution": {"id": "code_runner", "category": "Infra"},
}

def map_features(feature_ids: List[str]) -> List[str]:
    """Return valid block IDs for the given feature IDs."""
    return [BLOCK_REGISTRY[f]["id"] for f in feature_ids if f in BLOCK_REGISTRY]
