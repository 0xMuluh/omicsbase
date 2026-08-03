/* eslint-disable @typescript-eslint/no-explicit-any */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      "X-Tenant-ID": "default_tenant",
      "X-User-ID": "default_user",
      ...options?.headers,
    },
    ...options,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`API error ${res.status}: ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

async function fetchArtifactBlob(path: string): Promise<Blob> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      "X-Tenant-ID": "default_tenant",
      "X-User-ID": "default_user",
    },
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`API error ${res.status}: ${detail}`);
  }
  return res.blob();
}

// --- Types ---

export interface ProjectFile {
  id: string;
  file_role: string | null;
  original_name: string | null;
  detected_format: string | null;
  file_summary: Record<string, any> | null;
  created_at: string;
}

export interface Project {
  id: string;
  name: string;
  question: string | null;
  notes: string | null;
  custom_plan_text: string | null;
  auto_build: boolean;
  status: string;
  agent_state: string | null;
  agent_memory: Record<string, any> | null;
  agent_actions: {
    time: string;
    type: string;
    status: string;
    summary: string;
    details?: Record<string, any>;
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

export interface StudyManifest {
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
  parameters: Record<string, any> | null;
}

export interface AnalysisPlan {
  project_name: string;
  domain: "microbiome" | "metabolomics";
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
  metadata?: Record<string, any> | null;
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
  project_id?: string;
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
  metadata: Record<string, any> | null;
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
  metadata: Record<string, any> | null;
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
  metadata: Record<string, any> | null;
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
  parameters: Record<string, any> | null;
  result_metadata: Record<string, any> | null;
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
}

export interface NoteExecutionEvent {
  id: string;
  execution_id: string;
  sequence: number;
  event_type: string;
  status: string;
  payload: Record<string, any>;
  created_at: string;
}

export interface NoteThreadSummary {
  id: string;
  project_id: string | null;
  scope: "standalone" | "workspace";
  title: string;
  thread_type: string;
  status: NoteThreadStatus;
  metadata: Record<string, any> | null;
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
  metadata: Record<string, any> | null;
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

// --- API Functions ---

export const api = {
  // Projects
  listProjects: () => request<Project[]>("/projects/"),
  createProject: (data: { name?: string; question?: string; notes?: string; custom_plan_text?: string; auto_build?: boolean }) =>
    request<Project>("/projects/", { method: "POST", body: JSON.stringify(data) }),
  getProject: (id: string) => request<Project>(`/projects/${id}`),
  deleteProject: (id: string) => request<void>(`/projects/${id}`, { method: "DELETE" }),
  updateProject: (id: string, data: { name?: string; notes?: string; status?: string }) =>
    request<Project>(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(data) }),

  // Files
  uploadFile: async (projectId: string, file: File, role?: string) => {
    const formData = new FormData();
    formData.append("file", file);
    const url = `${API_BASE}/projects/${projectId}/files?file_role=${role || "auto"}`;
    const res = await fetch(url, { method: "POST", body: formData });
    if (!res.ok) {
      const detail = await res.text().catch(() => res.statusText);
      throw new Error(`Upload failed (${res.status}): ${detail}`);
    }
    return res.json() as Promise<ProjectFile>;
  },
  listFiles: (projectId: string) => request<ProjectFile[]>(`/projects/${projectId}/files`),

  // Planning & Generation
  startPlanning: (projectId: string) =>
    request<Job>(`/projects/${projectId}/plan`, { method: "POST" }),
  approvePlan: (projectId: string, plan: AnalysisPlan) =>
    request<{ status: string }>(`/projects/${projectId}/approve`, {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, plan }),
    }),
  startGeneration: (projectId: string) =>
    request<Job>(`/projects/${projectId}/generate`, { method: "POST" }),
  startRendering: (projectId: string) =>
    request<Job>(`/projects/${projectId}/run`, { method: "POST" }),
  editProject: (projectId: string, instruction: string) =>
    request<Job>(`/projects/${projectId}/edit`, {
      method: "POST",
      body: JSON.stringify({ instruction }),
    }),
  assistantMessage: (projectId: string, message: string, history: ChatMessage[] = []) =>
    request<AssistantMessage>(`/projects/${projectId}/assistant`, {
      method: "POST",
      body: JSON.stringify({
        message,
        history: history.map((item) => ({ role: item.role, content: item.content })),
      }),
    }),
  listMessages: (projectId: string) =>
    request<ProjectMessage[]>(`/projects/${projectId}/messages`),
  listNoteThreads: (projectId: string) =>
    request<NoteThreadSummary[]>(`/projects/${projectId}/notes`),
  createNoteThread: (projectId: string, data: { title?: string; thread_type?: string } = {}) =>
    request<NoteThread>(`/projects/${projectId}/notes`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  getNoteThread: (projectId: string, threadId: string) =>
    request<NoteThread>(`/projects/${projectId}/notes/${threadId}`),
  updateNoteThread: (
    projectId: string,
    threadId: string,
    data: { title?: string; status?: NoteThreadStatus; metadata?: Record<string, any> | null },
  ) =>
    request<NoteThreadSummary>(`/projects/${projectId}/notes/${threadId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteNoteThread: (projectId: string, threadId: string) =>
    request<void>(`/projects/${projectId}/notes/${threadId}`, { method: "DELETE" }),
  createNoteCell: (
    projectId: string,
    threadId: string,
    data: {
      cell_type: NoteCellType;
      language?: string | null;
      content?: string;
      position?: number | null;
      metadata?: Record<string, any> | null;
    },
  ) =>
    request<NoteCell>(`/projects/${projectId}/notes/${threadId}/cells`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  appendNoteCellRevision: (
    projectId: string,
    threadId: string,
    cellId: string,
    data: {
      cell_type: NoteCellType;
      language?: string | null;
      content?: string;
      metadata?: Record<string, any> | null;
    },
  ) =>
    request<NoteCellRevision>(
      `/projects/${projectId}/notes/${threadId}/cells/${cellId}/revisions`,
      {
        method: "POST",
        body: JSON.stringify(data),
      },
    ),
  executeNoteCell: (
    projectId: string,
    threadId: string,
    cellId: string,
    data: { revision?: number; parameters?: Record<string, any>; timeout_seconds?: number; cache_policy?: "off" | "reuse"; upstream_execution_ids?: string[] },
  ) =>
    request<NoteCellExecution>(
      "/projects/" + projectId + "/notes/" + threadId + "/cells/" + cellId + "/execute",
      { method: "POST", body: JSON.stringify(data) },
    ),
  getNoteCellExecution: (projectId: string, threadId: string, cellId: string, executionId: string) =>
    request<NoteCellExecution>(
      "/projects/" + projectId + "/notes/" + threadId + "/cells/" + cellId + "/executions/" + executionId,
    ),
  cancelNoteCellExecution: (projectId: string, threadId: string, cellId: string, executionId: string) =>
    request<NoteCellExecution>(
      "/projects/" + projectId + "/notes/" + threadId + "/cells/" + cellId + "/executions/" + executionId + "/cancel",
      { method: "POST" },
    ),
  listNoteCellExecutionEvents: (
    projectId: string,
    threadId: string,
    cellId: string,
    executionId: string,
    afterSequence = 0,
  ) =>
    request<NoteExecutionEvent[]>(
      "/projects/" + projectId + "/notes/" + threadId + "/cells/" + cellId + "/executions/" + executionId + "/events?after_sequence=" + String(afterSequence) + "&limit=500",
    ),
  createWorkspaceFromStandaloneNoteThread: (threadId: string, data: { name?: string; question?: string; notes?: string; auto_build?: boolean } = {}) =>
    request<{ project_id: string; note_thread: NoteThread }>("/notes/" + threadId + "/workspace", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  streamNoteThreadTurn: (
    threadId: string,
    data: { message: string; auto_execute?: boolean; idempotency_key?: string },
    onEvent: (event: NoteTurnStreamEvent) => void,
    signal?: AbortSignal,
  ) => streamNoteThreadTurn(threadId, data, onEvent, signal),
  listStandaloneNoteThreads: () =>
    request<NoteThreadSummary[]>("/notes"),
  listRecentNoteThreads: () =>
    request<NoteThreadSummary[]>("/notes/all"),
  createStandaloneNoteThread: (data: { title?: string; thread_type?: string } = {}) =>
    request<NoteThread>("/notes", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  getStandaloneNoteThread: (threadId: string) =>
    request<NoteThread>("/notes/" + threadId),
  updateStandaloneNoteThread: (
    threadId: string,
    data: { title?: string; status?: NoteThreadStatus; metadata?: Record<string, any> | null },
  ) =>
    request<NoteThreadSummary>("/notes/" + threadId, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteStandaloneNoteThread: (threadId: string) =>
    request<void>("/notes/" + threadId, { method: "DELETE" }),
  createStandaloneNoteCell: (
    threadId: string,
    data: {
      cell_type: NoteCellType;
      language?: string | null;
      content?: string;
      position?: number | null;
      metadata?: Record<string, any> | null;
    },
  ) =>
    request<NoteCell>("/notes/" + threadId + "/cells", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  appendStandaloneNoteCellRevision: (
    threadId: string,
    cellId: string,
    data: {
      cell_type: NoteCellType;
      language?: string | null;
      content?: string;
      metadata?: Record<string, any> | null;
    },
  ) =>
    request<NoteCellRevision>(
      "/notes/" + threadId + "/cells/" + cellId + "/revisions",
      {
        method: "POST",
        body: JSON.stringify(data),
      },
    ),
  executeStandaloneNoteCell: (
    threadId: string,
    cellId: string,
    data: { revision?: number; parameters?: Record<string, any>; timeout_seconds?: number; cache_policy?: "off" | "reuse"; upstream_execution_ids?: string[] },
  ) =>
    request<NoteCellExecution>(
      "/notes/" + threadId + "/cells/" + cellId + "/execute",
      { method: "POST", body: JSON.stringify(data) },
    ),
  getStandaloneNoteCellExecution: (threadId: string, cellId: string, executionId: string) =>
    request<NoteCellExecution>(
      "/notes/" + threadId + "/cells/" + cellId + "/executions/" + executionId,
    ),
  cancelStandaloneNoteCellExecution: (threadId: string, cellId: string, executionId: string) =>
    request<NoteCellExecution>(
      "/notes/" + threadId + "/cells/" + cellId + "/executions/" + executionId + "/cancel",
      { method: "POST" },
    ),
  listStandaloneNoteCellExecutionEvents: (
    threadId: string,
    cellId: string,
    executionId: string,
    afterSequence = 0,
  ) =>
    request<NoteExecutionEvent[]>(
      "/notes/" + threadId + "/cells/" + cellId + "/executions/" + executionId + "/events?after_sequence=" + String(afterSequence) + "&limit=500",
    ),
  getNoteExecutionArtifactContent: (
    projectId: string,
    threadId: string,
    cellId: string,
    executionId: string,
    artifactId: string,
  ) =>
    fetchArtifactBlob(
      "/projects/" + projectId + "/notes/" + threadId + "/cells/" + cellId + "/executions/" + executionId + "/artifacts/" + artifactId + "/content",
    ),
  getStandaloneNoteExecutionArtifactContent: (
    threadId: string,
    cellId: string,
    executionId: string,
    artifactId: string,
  ) =>
    fetchArtifactBlob(
      "/notes/" + threadId + "/cells/" + cellId + "/executions/" + executionId + "/artifacts/" + artifactId + "/content",
    ),
  listReports: (projectId: string) =>
    request<WorkspaceReport[]>(`/projects/${projectId}/reports`),
  exportNoteThreadReport: (
    projectId: string,
    threadId: string,
    data: { name?: string; slug?: string; overwrite?: boolean } = {},
  ) =>
    request<WorkspaceReport>(`/projects/${projectId}/reports/from-note/${threadId}`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  streamHomeChat: async (
    message: string,
    onEvent: (event: {
      type: string;
      message?: string;
      token?: string;
      name?: string;
      question?: string;
      use_example?: string | null;
      project_id?: string | null;
    }) => void,
    projectId?: string | null,
    signal?: AbortSignal,
  ) => {
    const response = await fetch(`${API_BASE}/chat/home`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Tenant-ID": "default_tenant",
        "X-User-ID": "default_user",
      },
      body: JSON.stringify({ message, project_id: projectId || undefined }),
      signal,
    });
    if (!response.ok) {
      const detail = await response.text().catch(() => response.statusText);
      throw new Error(`Chat error ${response.status}: ${detail}`);
    }
    if (!response.body) throw new Error("Home chat stream had no body.");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        onEvent(JSON.parse(line));
      }
      if (done) break;
    }
    if (buffer.trim()) onEvent(JSON.parse(buffer));
  },
  streamAgentMessage: (
    projectId: string,
    data: {
      message: string;
      selected_file?: string | null;
      selected_content?: string | null;
      selected_content_dirty?: boolean;
      preview_path?: string | null;
      chat_mode?: "build" | "discuss";
    idempotency_key?: string;
    },
    onEvent: (event: AgentStreamEvent) => void,
    signal?: AbortSignal,
  ) => streamAgentMessage(projectId, data, onEvent, signal),
  getLocks: (projectId: string) => request<{ paths: string[] }>(`/projects/${projectId}/locks`),
  updateLocks: (projectId: string, paths: string[]) =>
    request<{ paths: string[] }>(`/projects/${projectId}/locks`, {
      method: "PUT",
      body: JSON.stringify({ paths }),
    }),
  getTranscriptUrl: (
    projectId: string,
    options?: { format?: "markdown" | "html"; include_tools?: boolean; include_timestamps?: boolean },
  ) => {
    const params = new URLSearchParams();
    params.set("format", options?.format || "markdown");
    if (options?.include_tools === false) params.set("include_tools", "false");
    if (options?.include_timestamps === false) params.set("include_timestamps", "false");
    return `${API_BASE}/projects/${projectId}/transcript?${params.toString()}`;
  },
  subscribeProjectEvents: (
    projectId: string,
    onEvent: (event: ProjectEvent) => void,
    onError?: () => void,
  ) => {
    const source = new EventSource(`${API_BASE}/projects/${projectId}/events`);
    source.addEventListener("workspace", (rawEvent) => {
      onEvent(JSON.parse((rawEvent as MessageEvent).data) as ProjectEvent);
    });
    source.onerror = () => onError?.();
    return () => source.close();
  },

  // Jobs
  listJobs: (projectId: string) => request<Job[]>(`/projects/${projectId}/jobs`),
  getJob: (projectId: string, jobId: string) =>
    request<Job>(`/projects/${projectId}/jobs/${jobId}`),

  // Files & Report
  getFileTree: (projectId: string) => request<FileTreeNode[]>(`/projects/${projectId}/files/tree`),
  getFileContent: (projectId: string, filePath: string) =>
    request<{ content: string; path: string; type: string }>(
      `/projects/${projectId}/files/content/${filePath}`
    ),
  getFilePreview: (projectId: string, filePath: string) =>
    request<FilePreview>(`/projects/${projectId}/files/preview/${filePath}`),
  saveFileContent: (projectId: string, filePath: string, content: string) =>
    request<{ content: string; path: string; type: string; saved: boolean }>(
      `/projects/${projectId}/files/content/${filePath}`,
      { method: "PATCH", body: JSON.stringify({ content }) }
    ),
  runCodeChunk: (projectId: string, code: string, filePath?: string | null) =>
    request<ChunkRunResult>(`/projects/${projectId}/files/run-chunk`, {
      method: "POST",
      body: JSON.stringify({ code, file_path: filePath || null }),
    }),
  getChunkOutputUrl: (htmlUrl: string) => `${API_BASE}${htmlUrl}`,
  getReportUrl: (projectId: string, path: string = "index.html") =>
    `${API_BASE}/projects/${projectId}/files/report/${path}`,
  getRawFileUrl: (projectId: string, filePath: string) =>
    `${API_BASE}/projects/${projectId}/files/content/${filePath.split("/").map(encodeURIComponent).join("/")}`,
  getDownloadUrl: (projectId: string) =>
    `${API_BASE}/projects/${projectId}/files/download`,

  // Health
  health: () => request<{ status: string }>("/health"),
  prerequisites: () => request<Record<string, any>>("/prerequisites"),
};


type DurableStreamEvent = {
  type?: string;
  sequence?: number;
  run?: { status?: string };
};

async function streamNdjsonWithReplay<T extends DurableStreamEvent>(
  url: string,
  body: Record<string, unknown>,
  headers: HeadersInit,
  onEvent: (event: T) => void,
  signal?: AbortSignal,
): Promise<void> {
  const suppliedKey = body.idempotency_key;
  const idempotencyKey =
    typeof suppliedKey === "string" && suppliedKey.trim()
      ? suppliedKey
      : Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
  const requestBody = { ...body, idempotency_key: idempotencyKey };
  const maxAttempts = 3;
  let lastSequence = -1;

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    if (signal?.aborted) throw new Error("The agent stream was aborted.");
    let response: Response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers,
        body: JSON.stringify(requestBody),
        signal,
      });
    } catch (error) {
      if (signal?.aborted || attempt === maxAttempts - 1) throw error;
      await new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1)));
      continue;
    }

    if (!response.ok) {
      const detail = await response.text().catch(() => response.statusText);
      const retryable = response.status === 429 || response.status >= 500;
      if (!retryable || attempt === maxAttempts - 1) {
        throw new Error("Agent stream error " + response.status + ": " + detail);
      }
      await new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1)));
      continue;
    }
    if (!response.body) throw new Error("Agent stream did not return a response body.");

    let terminal = false;
    const emitLine = (line: string) => {
      const event = JSON.parse(line) as T;
      const sequence = Number(event.sequence);
      if (Number.isFinite(sequence)) {
        if (sequence <= lastSequence) return;
        lastSequence = sequence;
      }
      onEvent(event);
      if (
        event.type === "final" ||
        event.type === "cancelled" ||
        event.type === "paused" ||
        (event.type === "run" &&
          ["completed", "failed", "cancelled"].includes(event.run?.status || ""))
      ) {
        terminal = true;
      }
    };

    try {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (line.trim()) emitLine(line);
        }
        if (done) break;
      }
      if (buffer.trim()) emitLine(buffer);
    } catch (error) {
      if (signal?.aborted || attempt === maxAttempts - 1) throw error;
      await new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1)));
      continue;
    }

    if (terminal) return;
    if (attempt < maxAttempts - 1) {
      await new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1)));
    }
  }

  throw new Error("Agent stream ended before the durable run completed.");
}

async function streamAgentMessage(
  projectId: string,
  data: {
    message: string;
    selected_file?: string | null;
    selected_content?: string | null;
    selected_content_dirty?: boolean;
    preview_path?: string | null;
    chat_mode?: "build" | "discuss";
    idempotency_key?: string;
  },
  onEvent: (event: AgentStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamNdjsonWithReplay<AgentStreamEvent>(
    API_BASE + "/projects/" + projectId + "/agent/stream",
    data,
    { "Content-Type": "application/json" },
    onEvent,
    signal,
  );
}

async function streamNoteThreadTurn(
  threadId: string,
  data: { message: string; auto_execute?: boolean; idempotency_key?: string },
  onEvent: (event: NoteTurnStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamNdjsonWithReplay<NoteTurnStreamEvent>(
    API_BASE + "/notes/" + threadId + "/turn",
    data,
    {
      "Content-Type": "application/json",
      "X-Tenant-ID": "default_tenant",
      "X-User-ID": "default_user",
    },
    onEvent,
    signal,
  );
}
