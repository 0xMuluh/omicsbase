import type { Cell, Revision, Execution, Artifact } from './contracts';
import type { Identifier } from './serviceTypes';
export type SerializedArtifact = Omit<
  Artifact,
  'executionId' | 'conversationId' | 'user' | 'createdAt'
> & { id: string; executionId: string; contentUrl: null };
export type SerializedRevision = Pick<
  Revision,
  'revision' | 'language' | 'content' | 'createdAt'
> & { id: string; cellId: string };
export type SerializedExecution = Omit<
  Execution,
  'cellId' | 'revisionId' | 'conversationId' | 'user' | 'cancelRequested'
> & {
  id: string;
  cellId: string;
  revisionId: string;
  cancelRequested: boolean;
  artifacts?: SerializedArtifact[];
};
export type SerializedCell = Omit<Cell, 'user' | 'latestRevisionId' | 'latestExecutionId'> & {
  id: string;
  latestRevisionId: string | null;
  latestExecutionId: string | null;
  revision?: SerializedRevision;
  latestExecution?: SerializedExecution;
};

export function toId(doc: Identifier | { _id: Identifier }): string;
export function toId(doc: Identifier | { _id: Identifier } | null | undefined): string | null;
export function toId(doc: Identifier | { _id: Identifier } | null | undefined) {
  if (!doc) {
    return null;
  }
  return (typeof doc === 'object' && '_id' in doc ? doc._id : doc).toString();
}

export function serializeCell(
  cell: Cell & { _id: Identifier },
  revision: (Revision & { _id: Identifier }) | null = null,
  execution: (Execution & { _id: Identifier }) | null = null,
): SerializedCell {
  return {
    id: toId(cell),
    conversationId: cell.conversationId,
    messageId: cell.messageId,
    blockKey: cell.blockKey,
    position: cell.position,
    status: cell.status,
    latestRevisionId: cell.latestRevisionId ? toId(cell.latestRevisionId) : null,
    latestExecutionId: cell.latestExecutionId ? toId(cell.latestExecutionId) : null,
    createdAt: cell.createdAt,
    updatedAt: cell.updatedAt,
    revision: revision ? serializeRevision(revision) : undefined,
    latestExecution: execution ? serializeExecution(execution) : undefined,
  };
}

export function serializeRevision(rev: Revision & { _id: Identifier }): SerializedRevision {
  return {
    id: toId(rev),
    cellId: toId(rev.cellId),
    revision: rev.revision,
    language: rev.language,
    content: rev.content,
    createdAt: rev.createdAt,
  };
}

export function serializeExecution(
  exec: Execution & { _id: Identifier },
  artifacts: ReturnType<typeof serializeArtifact>[] | undefined = undefined,
): SerializedExecution {
  return {
    id: toId(exec),
    cellId: toId(exec.cellId),
    revisionId: toId(exec.revisionId),
    attempt: exec.attempt,
    status: exec.status,
    timeoutSeconds: exec.timeoutSeconds,
    cancelRequested: !!exec.cancelRequested,
    error: exec.error,
    stdout: exec.stdout,
    markdown: exec.markdown,
    engineRunDir: exec.engineRunDir,
    engineCellId: exec.engineCellId,
    startedAt: exec.startedAt,
    finishedAt: exec.finishedAt,
    createdAt: exec.createdAt,
    updatedAt: exec.updatedAt,
    artifacts,
  };
}

export function serializeArtifact(art: Artifact & { _id: Identifier }): SerializedArtifact {
  return {
    id: toId(art),
    executionId: toId(art.executionId),
    artifactType: art.artifactType,
    relativePath: art.relativePath,
    mimeType: art.mimeType,
    byteSize: art.byteSize,
    previewMarkdown: art.previewMarkdown,
    rows: art.rows,
    cols: art.cols,
    contentUrl: null, // filled by routes with conversation/cell paths
  };
}
