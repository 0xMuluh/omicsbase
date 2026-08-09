import { API_BASE, request } from "./client";
import type { AgentStreamEvent, FileAttachment } from "./types/messages";
import type { ChunkRunResult, FilePreview, FileTreeNode, Job } from "./types/projects";
import { projectsApi } from "./projects";
import { notesApi } from "./notes";
import { streamAgentMessage } from "./streams";
import { eventsApi } from "./events";
import { executionsApi } from "./executions";
import { editsApi } from "./edits";

// --- API Functions ---

export const api = {
  ...projectsApi,
  ...notesApi,
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
      attachments?: FileAttachment[];
    idempotency_key?: string;
    },
    onEvent: (event: AgentStreamEvent) => void,
    signal?: AbortSignal,
  ) => streamAgentMessage(projectId, data, onEvent, signal),
  ...eventsApi,

  // Jobs
  listJobs: (projectId: string) => request<Job[]>(`/projects/${projectId}/jobs`),
  getJob: (projectId: string, jobId: string) =>
    request<Job>(`/projects/${projectId}/jobs/${jobId}`),

  // Files & Report
  getFileTree: (projectId: string) => request<FileTreeNode[]>(`/projects/${projectId}/files/tree`),
  getFileContent: (projectId: string, filePath: string) =>
    request<{ content: string; path: string; type: string; sha256?: string }>(
      `/projects/${projectId}/files/content/${filePath}`
    ),
  getFilePreview: (projectId: string, filePath: string) =>
    request<FilePreview>(`/projects/${projectId}/files/preview/${filePath}`),
  saveFileContent: (projectId: string, filePath: string, content: string, baseSha256: string) =>
    request<{ content: string; path: string; type: string; saved: boolean; sha256?: string; transaction_id?: string }>(
      `/projects/${projectId}/files/content/${filePath}`,
      {
        method: "PATCH",
        headers: { "If-Match": `"${baseSha256}"` },
        body: JSON.stringify({ content }),
      }
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

  ...editsApi,
  ...executionsApi,

  // Health
  health: () => request<{ status: string }>("/health"),
  prerequisites: () => request<Record<string, unknown>>("/prerequisites"),
};

export type * from "./types";
