from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

class FeatureCategory(str, Enum):
    AI = "AI"
    DOCUMENT = "Document"
    STORAGE = "Storage"
    VISION = "Vision"
    INFRA = "Infrastructure"

class Feature(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    category: FeatureCategory
    default: bool = False

class AIConfig(BaseModel):
    base_model: str = "Llama-3.2-3B"
    lora_rank: int = 32
    learning_rate: float = 2e-4
    vector_db: str = "ZVec"
    hnsw_preset: str = "balanced"  # fast, balanced, accurate

class SessionConfig(BaseModel):
    features: List[str] = Field(default_factory=list)
    domain: str = "construction"
    ai_config: AIConfig = Field(default_factory=AIConfig)

class UploadResult(BaseModel):
    status: str = "pending"  # pending, processing, completed, failed
    progress: float = 0.0
    total_chunks: int = 0
    indexed_collection: Optional[str] = None
    failed_files: List[str] = Field(default_factory=list)
    message: Optional[str] = None

class SessionState(BaseModel):
    session_id: str
    user_id: str
    phase: int = 1
    phase_status: str = "in_progress"
    config: SessionConfig = Field(default_factory=SessionConfig)
    upload: UploadResult = Field(default_factory=UploadResult)
    # Indexed data stored with the session for later packaging
    corpus: Optional[str] = None
    chunks: List[str] = Field(default_factory=list)
    embeddings: List[List[float]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
