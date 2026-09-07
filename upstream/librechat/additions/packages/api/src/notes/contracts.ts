import type { Types } from 'mongoose';

export type Status = 'queued' | 'running' | 'completed' | 'failed' | 'timed_out' | 'cancelled';
export interface Cell {
  messageId: string | null;
  blockKey: string | null;
  position: number;
  status: string;
  createdAt?: Date;
  updatedAt?: Date;
  conversationId: string;
  user: string;
  latestRevisionId: Types.ObjectId | null;
  latestExecutionId: Types.ObjectId | null;
}
export interface Revision {
  language: string;
  createdAt?: Date;
  cellId: Types.ObjectId;
  conversationId: string;
  user: string;
  revision: number;
  content: string;
}
export interface Execution {
  createdAt?: Date;
  updatedAt?: Date;
  cellId: Types.ObjectId;
  revisionId: Types.ObjectId;
  conversationId: string;
  user: string;
  status: Status;
  attempt: number;
  timeoutSeconds: number;
  startedAt: Date;
  finishedAt?: Date;
  cancelRequested?: boolean;
  stdout?: string;
  markdown?: string;
  error?: string | null;
  engineCellId?: string | null;
  engineRunDir?: string | null;
}
export interface Event {
  payload?: { error?: string | null } | null;
  createdAt?: Date;
  executionId: Types.ObjectId;
  conversationId: string;
  sequence: number;
  eventType: string;
  status: Status;
}
export interface Artifact {
  createdAt?: Date;
  executionId: Types.ObjectId;
  conversationId: string;
  user: string;
  artifactType: 'console' | 'plot' | 'table';
  relativePath: string;
  mimeType: string;
  byteSize: number;
  previewMarkdown?: string | null;
  rows?: number | null;
  cols?: number | null;
}
export interface NoteEngineResult {
  success?: boolean;
  cancelled?: boolean;
  timed_out?: boolean;
  stdout?: string;
  markdown?: string;
  error?: string;
  cell_id?: string;
  engine_run_dir?: string;
  run_dir?: string;
  plots?: string[];
  tables?: { url?: string; file?: string; markdown?: string; rows?: number; cols?: number }[];
}
