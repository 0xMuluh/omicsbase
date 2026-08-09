import { api } from "@/lib/api";
import type { FileAttachment } from "@/lib/api/types/messages";
import type { NoteCell, NoteCellExecution, NoteCellRevision, NoteDataFile, NoteExecutionEvent, NoteThread, NoteThreadSummary, NoteTurnStreamEvent } from "@/lib/api/types/notes";

type CreateCellData = Parameters<typeof api.createNoteCell>[2];
type AppendRevisionData = Parameters<typeof api.appendNoteCellRevision>[3];
type ExecuteCellData = Parameters<typeof api.executeNoteCell>[3];
type UpdateThreadData = Parameters<typeof api.updateNoteThread>[2];

export interface NoteScope {
  readonly kind: "standalone" | "workspace";
  readonly id: string;
  readonly workspaceId?: string;
  listThreads: () => Promise<NoteThreadSummary[]>;
  getThread: (threadId: string) => Promise<NoteThread>;
  createThread: (data: { title?: string; thread_type?: string }) => Promise<NoteThread>;
  updateThread: (threadId: string, data: UpdateThreadData) => ReturnType<typeof api.updateNoteThread>;
  createCell: (threadId: string, data: CreateCellData) => Promise<NoteCell>;
  appendRevision: (threadId: string, cellId: string, data: AppendRevisionData) => Promise<NoteCellRevision>;
  executeCell: (threadId: string, cellId: string, data: ExecuteCellData) => Promise<NoteCellExecution>;
  getExecution: (threadId: string, cellId: string, executionId: string) => Promise<NoteCellExecution>;
  cancelExecution: (threadId: string, cellId: string, executionId: string) => Promise<NoteCellExecution>;
  listExecutionEvents: (threadId: string, cellId: string, executionId: string, afterSequence?: number) => Promise<NoteExecutionEvent[]>;
  listFiles: (threadId: string) => Promise<NoteDataFile[]>;
  uploadFile: (threadId: string, file: File) => Promise<NoteDataFile>;
  importDataset: (threadId: string, packageName: string, dataset: string) => Promise<unknown>;
  getArtifactContent: (threadId: string, cellId: string, executionId: string, artifactId: string) => Promise<Blob>;
  streamTurn: (
    threadId: string,
    data: { message: string; auto_execute?: boolean; idempotency_key?: string; attachments?: FileAttachment[] },
    onEvent: (event: NoteTurnStreamEvent) => void,
    signal?: AbortSignal,
  ) => Promise<void>;
}

export function createNoteScope({ workspaceId }: { workspaceId?: string }): NoteScope {
  const kind = workspaceId ? "workspace" : "standalone";
  const id = workspaceId || "standalone";

  return {
    kind,
    id,
    workspaceId,
    listThreads: () => workspaceId ? api.listNoteThreads(workspaceId) : api.listStandaloneNoteThreads(),
    getThread: (threadId) => workspaceId ? api.getNoteThread(workspaceId, threadId) : api.getStandaloneNoteThread(threadId),
    createThread: (data) => workspaceId ? api.createNoteThread(workspaceId, data) : api.createStandaloneNoteThread(data),
    updateThread: (threadId, data) => workspaceId
      ? api.updateNoteThread(workspaceId, threadId, data)
      : api.updateStandaloneNoteThread(threadId, data),
    createCell: (threadId, data) => workspaceId
      ? api.createNoteCell(workspaceId, threadId, data)
      : api.createStandaloneNoteCell(threadId, data),
    appendRevision: (threadId, cellId, data) => workspaceId
      ? api.appendNoteCellRevision(workspaceId, threadId, cellId, data)
      : api.appendStandaloneNoteCellRevision(threadId, cellId, data),
    executeCell: (threadId, cellId, data) => workspaceId
      ? api.executeNoteCell(workspaceId, threadId, cellId, data)
      : api.executeStandaloneNoteCell(threadId, cellId, data),
    getExecution: (threadId, cellId, executionId) => workspaceId
      ? api.getNoteCellExecution(workspaceId, threadId, cellId, executionId)
      : api.getStandaloneNoteCellExecution(threadId, cellId, executionId),
    cancelExecution: (threadId, cellId, executionId) => workspaceId
      ? api.cancelNoteCellExecution(workspaceId, threadId, cellId, executionId)
      : api.cancelStandaloneNoteCellExecution(threadId, cellId, executionId),
    listExecutionEvents: (threadId, cellId, executionId, afterSequence) => workspaceId
      ? api.listNoteCellExecutionEvents(workspaceId, threadId, cellId, executionId, afterSequence)
      : api.listStandaloneNoteCellExecutionEvents(threadId, cellId, executionId, afterSequence),
    listFiles: (threadId) => workspaceId
      ? api.listProjectNoteFiles(workspaceId, threadId)
      : api.listStandaloneNoteFiles(threadId),
    uploadFile: (threadId, file) => workspaceId
      ? api.uploadProjectNoteFile(workspaceId, threadId, file)
      : api.uploadStandaloneNoteFile(threadId, file),
    importDataset: (threadId, packageName, dataset) => workspaceId
      ? api.importProjectDataset(workspaceId, packageName, dataset)
      : api.importStandaloneNoteDataset(threadId, packageName, dataset),
    getArtifactContent: (threadId, cellId, executionId, artifactId) => workspaceId
      ? api.getNoteExecutionArtifactContent(workspaceId, threadId, cellId, executionId, artifactId)
      : api.getStandaloneNoteExecutionArtifactContent(threadId, cellId, executionId, artifactId),
    streamTurn: (threadId, data, onEvent, signal) => api.streamNoteThreadTurn(threadId, data, onEvent, signal),
  };
}
