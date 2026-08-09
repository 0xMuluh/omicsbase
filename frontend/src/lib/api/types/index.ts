// --- Types ---

export interface ProjectFile {
  id: string;
  file_role: string | null;
  original_name: string | null;
  detected_format: string | null;
  file_summary: Record<string, unknown> | null;
  created_at: string;
}

export interface FileAttachment {
  id?: string | null;
  name: string;
  format?: string | null;
  mime_type?: string | null;
  size_bytes?: number | null;
  r_path?: string | null;
  source?: "project" | "thread" | string;
}

export interface Project {
  id: string;
  name: string;
  name_source: "default" | "auto" | "user";
  question: string | null;
  notes: string | null;
  custom_plan_text: string | null;
  auto_build: boolean;
  status: string;
  agent_state: string | null;
  agent_memory: Record<string, unknown> | null;
  agent_actions: {
    time: string;
    type: string;
    status: string;
    summary: string;
    details?: Record<string, unknown>;
    files?: string[];
    job_id?: string;
  }[] | null;
  study_manifest: StudyManifest | null;
  analysis_plan: AnalysisPlan | null;
  project_dir: string | null;
  created_at: string;
  updated_at: string;
  files: ProjectFile[];
}

interface StudyManifest {
  version: string;
  generated_at: string;
  status: "ready" | "needs_input" | "invalid";
  domain: "microbiome" | "metabolomics" | "unknown";
  domain_candidates: { domain: string; score: number }[];
  summary: {
    file_count: number;
    data_file_count: number;
    recognized_data_file_count: number;
    error_count: number;
    warning_count: number;
  };
  files: {
    id: string;
    name: string;
    role: string;
    format: string;
    dimensions: Record<string, number>;
    columns: string[];
    inspection_status: string;
  }[];
  roles: Record<string, string[]>;
  identifier_candidates: { file: string; column: string; role: string; confidence: string }[];
  grouping_candidates: { file: string; column: string; levels: string[]; role: string; confidence: string }[];
  validations: { code: string; severity: "error" | "warning"; message: string }[];
}

export interface WorkflowStep {
  id: string;
  name: string;
  classification: "standard" | "contested";
  recipe_id: string | null;
  enabled: boolean;
  rationale: string | null;
  ensemble_methods: { id: string; name: string; r_package?: string }[] | null;
  parameters: Record<string, unknown> | null;
}

export interface AnalysisPlan {
  project_name: string;
  domain: "microbiome" | "metabolomics";
  report_pack_id?: string | null;
  capabilities?: string[];
  parameters?: Record<string, unknown> | null;
  study_type: string;
  question: string;
  detected_inputs: { file: string; role: string; format: string; details: string }[];
  grouping_variable: string | null;
  group_levels: string[];
  covariates: string[];
  workflow: WorkflowStep[];
  estimated_runtime_minutes: number | null;
  recipe_registry_version: string | null;
  notes: string | null;
}

interface EditTransactionFile {
  path: string;
  before_sha256: string | null;
  after_sha256: string | null;
  strategies?: string[];
  reasons?: string[];
  diff?: string;
}

export interface EditTransaction {
  transaction_id: string;
  status: string;
  origin?: string;
  summary?: string;
  files: EditTransactionFile[];
  modified_files?: string[];
  diagnostics?: Record<string, unknown>[];
  created_at?: string | null;
  committed_at?: string | null;
  reverted_by?: string | null;
}

interface ExecutionValidatorEvidence {
  step_id: string;
  path: string;
  status: string;
  input_sha256?: string | null;
  evidence?: { step?: string; status?: string; time?: string; detail?: string }[];
}

export interface ExecutionProvenanceSummary {
  run_id: string;
  started_at?: string | null;
  finished_at?: string | null;
  status: string;
  resume_from_step?: string | null;
  target_pages: string[];
  validators: ExecutionValidatorEvidence[];
  artifacts: { path: string; exists: boolean; sha256?: string | null; size_bytes?: number | null }[];
}

export interface EditReview {
  review_id: string;
  status: string;
  created_at?: string | null;
  origin?: string;
  summary?: string;
  files?: EditTransactionFile[];
  prepared?: EditTransaction;
}

export interface Job {
  id: string;
  project_id: string;
  job_type: string | null;
  status: string;
  progress: { step: string; status: string; time?: string; detail?: string; path?: string }[] | null;
  logs: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface ClarificationQuestion {
  id: string;
  prompt: string;
  options: string[];
  multiple: boolean;
  allow_custom: boolean;
  depends_on?: string | null;
}

export interface ClarificationRequest {
  message: string;
  questions: ClarificationQuestion[];
}

export interface ClarificationAnswer {
  id: string;
  values: string[];
}

export interface WorkspaceResult {
  path: string;
  name: string;
  source: "note" | "workspace";
}

export interface PendingQuestion {
  id: string;
  question: string;
  options: string[];
  multiple: boolean;
}



export interface ChunkRunResult {
  status: "completed" | "failed" | string;
  run_id: string;
  stdout: string;
  error: string | null;
  duration_seconds: number;
  html_url: string | null;
}

export interface AssistantMessage {
  type: string;
  message: string;
  instruction?: string | null;
}

export interface ChatMessage {
  id?: string;
  role: "user" | "assistant" | "tool";
  kind?: string;
  content: string;
  time: string;
  metadata?: Record<string, unknown> | null;
  attachments?: FileAttachment[];
  cell_id?: string | null;
  cell_type?: "markdown" | "agent" | "code" | "output" | "provenance" | string | null;
  cell_revision?: number | null;
  execution_id?: string | null;
}

export interface AgentStreamEvent {
  type: string;
  status?: string;
  message?: string | ProjectMessage;
  message_id?: string;
  tool?: string;
  reason?: string;
  summary?: string;
  action?: string;
  job_id?: string;
  step?: number;
  token?: string;
  chat_mode?: string;
  name?: string;
  name_source?: "default" | "auto" | "user";
  project_id?: string;
  question?: PendingQuestion;
  awaiting_answer?: PendingQuestion | null;
  quick_actions?: { type: string; label: string; prompt: string }[];
  sequence?: number;
  run_id?: string;
  run?: { status?: string };
  event?: {
    id: string;
    kind: string;
    status: string;
    title: string;
    summary: string;
    target?: Record<string, string | null | undefined>;
    log_excerpt?: string | null;
    diff?: string | null;
    cta?: { label: string; prompt: string } | null;
  };
}

export interface NoteTurnStreamEvent {
  type: string;
  status?: string;
  message?: string;
  token?: string;
  turn_id?: string;
  role?: "user" | "assistant";
  tool?: string;
  tool_call_id?: string;
  summary?: string;
  step?: number;
  thread?: NoteThreadSummary;
  cell?: NoteCell;
  execution?: NoteCellExecution;
  sequence?: number;
  run_id?: string;
  run?: { status?: string };
}

export interface ProjectMessage {
  id: string;
  project_id: string;
  role: "user" | "assistant" | "tool";
  kind: string;
  content: string;
  metadata: Record<string, unknown> | null;
  attachments?: FileAttachment[];
  cell_id?: string | null;
  cell_type?: "markdown" | "agent" | "code" | "output" | "provenance" | string | null;
  cell_revision?: number | null;
  execution_id?: string | null;
  created_at: string;
}

export type NoteCellType = "markdown" | "agent" | "code" | "output" | "provenance";
export type NoteThreadStatus = "active" | "archived";

export interface NoteCellRevision {
  id: string;
  cell_id: string;
  revision: number;
  cell_type: NoteCellType;
  language: string | null;
  content: string;
  metadata: Record<string, unknown> | null;
  created_by: string | null;
  created_at: string;
}

export interface NoteCell {
  id: string;
  thread_id: string;
  position: number;
  status: string;
  revisions: NoteCellRevision[];
  latest_execution?: NoteCellExecution | null;
  created_at: string;
  updated_at: string;
}

export interface NoteExecutionArtifact {
  id: string;
  execution_id: string;
  artifact_type: string;
  relative_path: string;
  mime_type: string;
  byte_size: number;
  sha256: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface NoteCellExecution {
  id: string;
  revision_id: string;
  attempt: number;
  status: string;
  execution_kind: string;
  timeout_seconds: number;
  cancel_requested: boolean;
  environment_fingerprint: string | null;
  input_fingerprint: string | null;
  parameters: Record<string, unknown> | null;
  result_metadata: Record<string, unknown> | null;
  artifacts: NoteExecutionArtifact[];
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  event_sequence: number;
  cache_policy: "off" | "reuse";
  cache_key: string | null;
  dependency_fingerprint: string | null;
  upstream_execution_ids: string[];
  cache_hit: boolean;
  cache_source_execution_id: string | null;
  idempotency_key: string | null;
}

export interface NoteExecutionEvent {
  id: string;
  execution_id: string;
  sequence: number;
  event_type: string;
  status: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface NoteThreadSummary {
  id: string;
  project_id: string | null;
  scope: "standalone" | "workspace";
  title: string;
  thread_type: string;
  status: NoteThreadStatus;
  metadata: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface NoteThread extends NoteThreadSummary {
  cells: NoteCell[];
}

export interface WorkspaceReport {
  id: string;
  project_id: string;
  name: string;
  slug: string;
  report_type: string;
  status: string;
  source_path: string | null;
  rendered_path: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectEvent {
  project_id: string;
  status: string;
  agent_state: string;
  agent_summary?: string | null;
  pending_guidance?: { content: string; source?: string; created_at?: string; status?: string }[];
  project_updated_at: string | null;
  latest_message_id: string | null;
  latest_message_at: string | null;
  jobs: {
    id: string;
    type: string | null;
    status: string;
    progress: Job["progress"];
    error: string | null;
    updated_at: string | null;
  }[];
}

export interface FileTreeNode {
  name: string;
  path: string;
  type: "file" | "directory";
  size?: number;
  extension?: string;
  children?: FileTreeNode[];
}

export interface FilePreview {
  path?: string;
  format: string;
  name?: string;
  editable?: boolean;
  dimensions?: { rows?: number; columns?: number };
  columns?: string[];
  column_types?: Record<string, string>;
  preview_rows?: string[][];
  preview_truncated?: boolean;
  sheets?: string[];
  selected_sheet?: string;
  note?: string;
  error?: string;
}

export interface NoteDataFile {
  name: string;
  format: string;
  size_bytes: number;
  dimensions?: Record<string, number>;
  columns?: string[];
  note?: string;
  r_path: string;
}

export interface ImportableDataset {
  package: string;
  dataset: string;
  description: string;
  domain_hint?: string;
}
