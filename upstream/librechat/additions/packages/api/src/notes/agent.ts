import { Types } from 'mongoose';
import type { Model } from 'mongoose';

type Status = 'queued' | 'running' | 'completed' | 'failed' | 'timed_out' | 'cancelled';
interface Cell {
  conversationId: string;
  user: string;
  latestRevisionId: Types.ObjectId;
  latestExecutionId: Types.ObjectId;
}
interface Revision {
  cellId: Types.ObjectId;
  conversationId: string;
  user: string;
  revision: number;
  content: string;
}
interface Execution {
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
interface Event {
  executionId: Types.ObjectId;
  conversationId: string;
  sequence: number;
  eventType: string;
  status: Status;
}
interface Artifact {
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

export function buildNoteArtifacts(
  execution: Pick<Execution, 'conversationId' | 'user'> & { _id: Types.ObjectId },
  result: NoteEngineResult,
): Artifact[] {
  const base = {
    executionId: execution._id,
    conversationId: execution.conversationId,
    user: execution.user,
  };
  const runDir = result.engine_run_dir || result.run_dir;
  const marker = `/projects/${execution.conversationId}/`;
  const relative = (url?: string) => {
    const index = url?.indexOf(marker) ?? -1;
    return index < 0 ? null : url!.slice(index + marker.length);
  };
  const artifacts: Artifact[] = [];
  if (result.stdout != null) {
    artifacts.push({
      ...base,
      artifactType: 'console',
      relativePath: runDir ? `${runDir}/console.txt` : 'console',
      mimeType: 'text/plain',
      byteSize: Buffer.byteLength(result.stdout, 'utf8'),
    });
  }
  for (const url of result.plots || []) {
    const path = relative(url);
    if (path) {
      artifacts.push({
        ...base,
        artifactType: 'plot',
        relativePath: path,
        mimeType: 'image/png',
        byteSize: 0,
      });
    }
  }
  for (const table of result.tables || []) {
    const path =
      relative(table.url) || (runDir && table.file ? `${runDir}/tables/${table.file}` : null);
    if (path) {
      artifacts.push({
        ...base,
        artifactType: 'table',
        relativePath: path,
        mimeType: 'text/csv',
        byteSize: 0,
        previewMarkdown: table.markdown || null,
        rows: table.rows ?? null,
        cols: table.cols ?? null,
      });
    }
  }
  return artifacts;
}

interface AgentCellIds {
  cellId: string;
  revisionId: string;
  executionId: string;
}
interface AgentLifecycle {
  start(
    conversationId: string,
    user: string,
    content: string,
    timeoutSeconds?: number,
    targetCellId?: string,
  ): Promise<AgentCellIds>;
  finish(
    conversationId: string,
    executionId: string,
    result: NoteEngineResult,
  ): Promise<{ status: Status }>;
}

export function createNoteAgentLifecycle(models: {
  NoteCell: Model<Cell>;
  NoteCellRevision: Model<Revision>;
  NoteCellExecution: Model<Execution>;
  NoteExecutionArtifact: Model<Artifact>;
  NoteExecutionEvent: Model<Event>;
}): AgentLifecycle {
  return {
    async start(
      conversationId: string,
      user: string,
      content: string,
      timeoutSeconds = 180,
      targetCellId?: string,
    ): Promise<AgentCellIds> {
      let cellId: Types.ObjectId;
      let revisionNumber = 1;
      let attemptNumber = 1;

      if (targetCellId && Types.ObjectId.isValid(targetCellId)) {
        const existingCell = await models.NoteCell.findOne({
          _id: new Types.ObjectId(targetCellId),
          conversationId,
        });
        if (existingCell) {
          cellId = existingCell._id as Types.ObjectId;
          const lastRevision = await models.NoteCellRevision.findOne({ cellId }).sort({ revision: -1 });
          if (lastRevision && typeof lastRevision.revision === 'number') {
            revisionNumber = lastRevision.revision + 1;
          }
          const lastExecution = await models.NoteCellExecution.findOne({ cellId }).sort({ attempt: -1 });
          if (lastExecution && typeof lastExecution.attempt === 'number') {
            attemptNumber = lastExecution.attempt + 1;
          }
        } else {
          cellId = new Types.ObjectId();
        }
      } else {
        cellId = new Types.ObjectId();
      }

      const revisionId = new Types.ObjectId();
      const executionId = new Types.ObjectId();
      await models.NoteCellRevision.create({
        _id: revisionId,
        cellId,
        conversationId,
        user,
        revision: revisionNumber,
        content,
      });
      await models.NoteCellExecution.create({
        _id: executionId,
        cellId,
        revisionId,
        conversationId,
        user,
        status: 'running',
        attempt: attemptNumber,
        timeoutSeconds,
        startedAt: new Date(),
      });
      await models.NoteCell.updateOne(
        { _id: cellId },
        {
          $set: {
            conversationId,
            user,
            latestRevisionId: revisionId,
            latestExecutionId: executionId,
          },
        },
        { upsert: true },
      );
      await models.NoteExecutionEvent.create({
        executionId,
        conversationId,
        sequence: 1,
        eventType: 'running',
        status: 'running',
      });
      return {
        cellId: String(cellId),
        revisionId: String(revisionId),
        executionId: String(executionId),
      };
    },
    async finish(
      conversationId: string,
      executionId: string,
      result: NoteEngineResult,
    ): Promise<{ status: Status }> {
      const execution = await models.NoteCellExecution.findOne({
        _id: executionId,
        conversationId,
      });
      if (!execution) {
        throw Object.assign(new Error('Execution not found'), { status: 404 });
      }
      if (execution.status !== 'running' && execution.status !== 'queued') {
        return { status: execution.status };
      }
      let status: Status = 'completed';
      if (result.cancelled) {
        status = 'cancelled';
      } else if (result.timed_out) {
        status = 'timed_out';
      } else if (result.success === false) {
        status = 'failed';
      }
      const artifacts = buildNoteArtifacts(execution, result);
      if (artifacts.length) {
        await models.NoteExecutionArtifact.insertMany(artifacts);
      }
      await models.NoteCellExecution.updateOne(
        { _id: execution._id },
        {
          $set: {
            status,
            finishedAt: new Date(),
            stdout: result.stdout || '',
            markdown: result.markdown || '',
            error: result.error || null,
            engineCellId: result.cell_id || null,
            engineRunDir: result.engine_run_dir || result.run_dir || null,
          },
        },
      );
      const last = await models.NoteExecutionEvent.findOne({ executionId: execution._id }).sort({
        sequence: -1,
      });
      await models.NoteExecutionEvent.create({
        executionId: execution._id,
        conversationId,
        sequence: (last?.sequence || 0) + 1,
        eventType: status,
        status,
      });
      return { status };
    },
  };
}
