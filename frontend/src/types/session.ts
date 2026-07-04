export interface AIConfig {
  base_model: string;
  lora_rank: number;
  learning_rate: number;
  vector_db: string;
  hnsw_preset: string;
}

export interface SessionConfig {
  domain: string;
  ai_config: AIConfig;
}

export interface UploadState {
  status: string;
  progress: number;
  total_chunks: number;
  failed_files: string[];
  message: string;
}

export interface DeploymentState {
  status: string;
  target: string;
  progress: number;
  url: string | null;
  api_key: string | null;
  message: string | null;
  package_path: string | null;
}

export interface TrainingState {
  status: string;
  progress: number;
  fine_tuned_model_id: string | null;
  error: string | null;
  dataset_size: number;
}

export interface ChainBlock {
  id: string;
  params: Record<string, unknown>;
}

export interface ChainConnection {
  from: number;
  to: number;
}

export interface Chain {
  blocks: ChainBlock[];
  connections: ChainConnection[];
}

export interface SessionState {
  session_id: string;
  phase: number;
  phase_status: string;
  config: SessionConfig;
  upload: UploadState;
  chain_approved: boolean;
  proposed_chain: Chain | null;
  extracted_rules: string[];
  training_job: TrainingState;
  deployment: DeploymentState;
}

export const defaultConfig: SessionConfig = {
  domain: 'construction',
  ai_config: {
    base_model: 'Llama-3.2-3B',
    lora_rank: 32,
    learning_rate: 2e-4,
    vector_db: 'ZVec',
    hnsw_preset: 'balanced',
  },
};

export const defaultUploadState: UploadState = {
  status: 'idle',
  progress: 0,
  total_chunks: 0,
  failed_files: [],
  message: '',
};

export const defaultDeploymentState: DeploymentState = {
  status: 'pending',
  target: 'cloud',
  progress: 0,
  url: null,
  api_key: null,
  message: null,
  package_path: null,
};

export const defaultTrainingState: TrainingState = {
  status: 'queued',
  progress: 0,
  fine_tuned_model_id: null,
  error: null,
  dataset_size: 0,
};

export const defaultSessionState = (sessionId: string): SessionState => ({
  session_id: sessionId,
  phase: 1,
  phase_status: 'in_progress',
  config: defaultConfig,
  upload: defaultUploadState,
  chain_approved: false,
  proposed_chain: null,
  extracted_rules: [],
  training_job: defaultTrainingState,
  deployment: defaultDeploymentState,
});
