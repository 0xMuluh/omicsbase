import type { Model, Types } from 'mongoose';
import type { Cell, Revision, Execution, Event, Artifact, NoteEngineResult } from './contracts';
export type Identifier = string | Types.ObjectId;
export interface NoteModels {
  NoteCell: Model<Cell>;
  NoteCellRevision: Model<Revision>;
  NoteCellExecution: Model<Execution>;
  NoteExecutionEvent: Model<Event>;
  NoteExecutionArtifact: Model<Artifact>;
}
export interface NoteDependencies {
  models: NoteModels;
  getConversationModel: () => Model<{ conversationId: string; user: string }> | undefined;
  bridgeConversationFiles: (conversationId: string, userId: string) => Promise<void>;
  executeOnEngine: (input: {
    code: string;
    threadId: string;
    executionId: string;
    timeoutSeconds: number;
  }) => Promise<NoteEngineResult>;
  fetchArtifactFromEngine: (
    conversationId: string,
    relativePath: string,
  ) => Promise<{ buffer: Buffer; contentType: string }>;
  signalCancel: (conversationId: string, executionId: string) => Promise<void>;
  internalSecret: string;
}
export interface CellInput {
  conversationId: string;
  userId: string;
  content?: string;
  cellId?: string | null;
  messageId?: string | null;
  blockKey?: string | null;
  position?: number;
}
export interface RevisionInput {
  conversationId: string;
  userId: string;
  cellId: string;
  content: string;
  language?: string;
}
export interface ExecutionInput {
  conversationId: string;
  userId: string;
  cellId: string;
  revisionId?: string | null;
  timeoutSeconds?: number;
}
