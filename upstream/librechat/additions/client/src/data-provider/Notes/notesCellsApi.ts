import { request } from 'librechat-data-provider';

export type NoteCellExecutionStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'timed_out'
  | 'cancelled';

export type NoteArtifact = {
  id: string;
  executionId: string;
  artifactType: 'plot' | 'table' | 'console';
  relativePath: string;
  mimeType: string;
  byteSize: number;
  previewMarkdown?: string | null;
  rows?: number | null;
  cols?: number | null;
  contentUrl?: string | null;
};

export type NoteCellRevision = {
  id: string;
  cellId: string;
  revision: number;
  language: string;
  content: string;
  createdAt?: string;
};

export type NoteCellExecution = {
  id: string;
  cellId: string;
  revisionId: string;
  attempt: number;
  status: NoteCellExecutionStatus;
  timeoutSeconds: number;
  cancelRequested?: boolean;
  error?: string | null;
  stdout?: string | null;
  markdown?: string | null;
  createdAt?: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  artifacts?: NoteArtifact[];
};

export type NoteCell = {
  id: string;
  conversationId: string;
  messageId?: string | null;
  blockKey?: string | null;
  position?: number;
  status?: string;
  latestRevisionId?: string | null;
  latestExecutionId?: string | null;
  revision?: NoteCellRevision;
  latestExecution?: NoteCellExecution;
  createdAt?: string;
  updatedAt?: string;
};

const base = (conversationId: string) => `/api/notes/${encodeURIComponent(conversationId)}`;

export const noteCellsApi = {
  listCells: (conversationId: string) =>
    request.get(`${base(conversationId)}/cells`) as Promise<{ cells: NoteCell[] }>,

  createOrFindCell: (
    conversationId: string,
    body: {
      content?: string;
      messageId?: string | null;
      blockKey?: string | null;
      cellId?: string;
    },
  ) => request.post(`${base(conversationId)}/cells`, body) as Promise<NoteCell>,

  getCell: (conversationId: string, cellId: string) =>
    request.get(`${base(conversationId)}/cells/${encodeURIComponent(cellId)}`) as Promise<NoteCell>,

  appendRevision: (conversationId: string, cellId: string, content: string) =>
    request.post(`${base(conversationId)}/cells/${encodeURIComponent(cellId)}/revisions`, {
      content,
      language: 'r',
    }) as Promise<NoteCellRevision>,

  execute: (conversationId: string, cellId: string, revisionId?: string) =>
    request.post(`${base(conversationId)}/cells/${encodeURIComponent(cellId)}/execute`, {
      revisionId,
    }) as Promise<NoteCellExecution>,

  getExecution: (conversationId: string, cellId: string, executionId: string) =>
    request.get(
      `${base(conversationId)}/cells/${encodeURIComponent(cellId)}/executions/${encodeURIComponent(executionId)}`,
    ) as Promise<NoteCellExecution>,

  listExecutions: (conversationId: string, cellId: string) =>
    request.get(
      `${base(conversationId)}/cells/${encodeURIComponent(cellId)}/executions`,
    ) as Promise<{ executions: NoteCellExecution[] }>,

  cancel: (conversationId: string, cellId: string, executionId: string) =>
    request.post(
      `${base(conversationId)}/cells/${encodeURIComponent(cellId)}/executions/${encodeURIComponent(executionId)}/cancel`,
      {},
    ) as Promise<NoteCellExecution>,

  listEvents: (conversationId: string, cellId: string, executionId: string, afterSequence = 0) =>
    request.get(
      `${base(conversationId)}/cells/${encodeURIComponent(cellId)}/executions/${encodeURIComponent(executionId)}/events?after_sequence=${afterSequence}`,
    ) as Promise<{ events: Array<{ sequence: number; eventType: string; status?: string }> }>,
};

export const TERMINAL_STATUSES = new Set<NoteCellExecutionStatus>([
  'completed',
  'failed',
  'timed_out',
  'cancelled',
]);

export function artifactAuthUrl(contentUrl?: string | null): string | undefined {
  if (!contentUrl) {
    return undefined;
  }
  if (contentUrl.startsWith('http')) {
    return contentUrl;
  }
  return contentUrl;
}
