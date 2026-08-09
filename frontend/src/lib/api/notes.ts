import { API_BASE, fetchArtifactBlob, request } from "./client";
import { streamNoteThreadTurn } from "./streams";
import type { FileAttachment } from "./types/messages";
import type { NoteCell, NoteCellExecution, NoteCellRevision, NoteCellType, NoteDataFile, NoteExecutionEvent, NoteThread, NoteThreadStatus, NoteThreadSummary, NoteTurnStreamEvent } from "./types/notes";
import type { WorkspaceReport } from "./types/executions";

export const notesApi = {
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
    data: { title?: string; status?: NoteThreadStatus; metadata?: Record<string, unknown> | null },
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
      metadata?: Record<string, unknown> | null;
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
      metadata?: Record<string, unknown> | null;
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
    data: { revision?: number; parameters?: Record<string, unknown>; timeout_seconds?: number; cache_policy?: "off" | "reuse"; upstream_execution_ids?: string[]; idempotency_key?: string },
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
    request<{
      project_id: string;
      note_thread: NoteThread;
      carried_forward?: {
        files: number;
        cells: number;
        question: string | null;
        notes: boolean;
        manifest: { path: string; sha256: string; cell_count: number; upload_count: number };
        auto_build: { requested: boolean; queued: boolean; job_id: string | null; reason: string | null };
      };
    }>("/notes/" + threadId + "/workspace", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  streamNoteThreadTurn: (
    threadId: string,
    data: { message: string; auto_execute?: boolean; idempotency_key?: string; attachments?: FileAttachment[] },
    onEvent: (event: NoteTurnStreamEvent) => void,
    signal?: AbortSignal,
  ) => streamNoteThreadTurn(threadId, data, onEvent, signal),
  listStandaloneNoteThreads: () =>
    request<NoteThreadSummary[]>("/notes"),
  createStandaloneNoteThread: (data: { title?: string; thread_type?: string } = {}) =>
    request<NoteThread>("/notes", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  // Example datasets + thread-attached data files
  listImportableDatasets: () =>
    request<{ datasets: { package: string; dataset: string; description: string; domain_hint?: string }[] }>("/datasets/importable"),
  importProjectDataset: (projectId: string, packageName: string, dataset: string) =>
    request<{ status: string; files: { name: string; role: string; format: string }[] }>("/datasets/projects/" + projectId + "/import", {
      method: "POST",
      body: JSON.stringify({ package: packageName, dataset }),
    }),
  uploadStandaloneNoteFile: async (threadId: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(`${API_BASE}/notes/${threadId}/files`, { method: "POST", body: formData });
    if (!res.ok) {
      const detail = await res.text().catch(() => res.statusText);
      throw new Error(`Upload failed (${res.status}): ${detail}`);
    }
    return res.json() as Promise<NoteDataFile>;
  },
  listStandaloneNoteFiles: (threadId: string) => request<NoteDataFile[]>(`/notes/${threadId}/files`),
  importStandaloneNoteDataset: (threadId: string, packageName: string, dataset: string) =>
    request<{ status: string; package: string; dataset: string; files: { name: string; format: string; r_path: string }[] }>(
      `/notes/${threadId}/datasets/import`,
      { method: "POST", body: JSON.stringify({ package: packageName, dataset }) },
    ),
  uploadProjectNoteFile: async (projectId: string, threadId: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(`${API_BASE}/projects/${projectId}/notes/${threadId}/files`, { method: "POST", body: formData });
    if (!res.ok) {
      const detail = await res.text().catch(() => res.statusText);
      throw new Error(`Upload failed (${res.status}): ${detail}`);
    }
    return res.json() as Promise<NoteDataFile>;
  },
  listProjectNoteFiles: (projectId: string, threadId: string) =>
    request<NoteDataFile[]>(`/projects/${projectId}/notes/${threadId}/files`),

  getStandaloneNoteThread: (threadId: string) =>
    request<NoteThread>("/notes/" + threadId),
  updateStandaloneNoteThread: (
    threadId: string,
    data: { title?: string; status?: NoteThreadStatus; metadata?: Record<string, unknown> | null },
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
      metadata?: Record<string, unknown> | null;
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
      metadata?: Record<string, unknown> | null;
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
    data: { revision?: number; parameters?: Record<string, unknown>; timeout_seconds?: number; cache_policy?: "off" | "reuse"; upstream_execution_ids?: string[]; idempotency_key?: string },
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
};
