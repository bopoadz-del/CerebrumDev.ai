from pydantic import BaseModel, Field
from typing import Literal, Optional, List, Dict, Any
from datetime import datetime


class AIConfig(BaseModel):
    base_model: str = "Llama-3.2-3B"
    lora_rank: int = 32
    learning_rate: float = 2e-4
    vector_db: str = "ZVec"
    hnsw_preset: str = "balanced"  # fast, balanced, accurate


class SessionConfig(BaseModel):
    domain: str = "construction"
    ai_config: AIConfig = Field(default_factory=AIConfig)


class UploadResult(BaseModel):
    status: str = "pending"  # pending, processing, completed, failed
    progress: float = 0.0
    total_chunks: int = 0
    indexed_collection: Optional[str] = None
    failed_files: List[str] = Field(default_factory=list)
    message: Optional[str] = None


class DeploymentResult(BaseModel):
    status: str = "pending"  # pending, packaging, deploying, live, failed, packaged
    target: str = "cloud"  # cloud, edge
    progress: float = 0.0
    url: Optional[str] = None
    api_key: Optional[str] = None
    service_id: Optional[str] = None
    deploy_id: Optional[str] = None
    message: Optional[str] = None
    package_path: Optional[str] = None


class TrainingJob(BaseModel):
    job_id: str = ""
    status: Literal["idle", "preparing", "queued", "running", "succeeded", "failed"] = "idle"
    fine_tuned_model_id: Optional[str] = None
    progress: float = 0.0
    error: Optional[str] = None
    dataset_size: int = 0
    dataset_url: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


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
    # Phase 3: AI chat + chain generation + rule injection
    chat_history: List[Dict[str, str]] = Field(default_factory=list)
    proposed_chain: Optional[Dict[str, Any]] = None
    extracted_rules: List[str] = Field(default_factory=list)
    chain_approved: bool = False
    rules_injected: bool = False
    container_modified_path: Optional[str] = None
    validation_passed: bool = False
    # Phase 4: Tinker – user-provided Q&A pairs for fine-tuning
    training_data: List[Dict[str, str]] = Field(default_factory=list)
    training_job: TrainingJob = Field(default_factory=TrainingJob)
    training_enabled: bool = True
    # Phase 5: deploy / ship
    deployment: DeploymentResult = Field(default_factory=DeploymentResult)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
