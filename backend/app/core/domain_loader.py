from typing import Dict, Optional, List

# Mock store (in production, fetch from Cerebrum‑Blocks /store/containers)
AVAILABLE_DOMAINS = {
    "construction": {
        "id": "construction",
        "name": "Construction",
        "status": "available",
        "description": "Construction project management, BIM, BOQ, and site operations"
    },
    "medical": {
        "id": "medical",
        "name": "Medical",
        "status": "coming_soon",
        "description": "Clinical decision support, EHR integration, diagnosis assistance"
    },
    "finance": {
        "id": "finance",
        "name": "Finance",
        "status": "coming_soon",
        "description": "Financial analysis, risk assessment, regulatory compliance"
    },
    "hotel": {
        "id": "hotel_operations",
        "name": "Hotel Operations",
        "status": "coming_soon",
        "description": "Hotel management, guest services, revenue optimization"
    },
    "legal": {
        "id": "legal",
        "name": "Legal",
        "status": "coming_soon",
        "description": "Legal research, contract analysis, citation formatting"
    },
}

def load_domain_manifest(domain_id: str) -> Optional[Dict]:
    """Load domain manifest from store (mock)."""
    return AVAILABLE_DOMAINS.get(domain_id)

def list_available_domains() -> List[Dict]:
    """Return all domains with status."""
    return list(AVAILABLE_DOMAINS.values())
