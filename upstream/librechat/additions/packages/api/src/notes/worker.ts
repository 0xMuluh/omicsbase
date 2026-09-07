import { logger } from '@librechat/data-schemas';
import type { Types } from 'mongoose';
import { buildNoteArtifacts } from './agent';
import { errorMessage, errorCode } from './errors';
import { toId } from './serialization';
import { TERMINAL } from './constants';
import type { Execution, NoteEngineResult } from './contracts';
import type { NoteDependencies, Identifier } from './serviceTypes';
import type { createEventStore } from './events';

export interface ExecutionWorker {
  enqueueExecution(id: Identifier): Promise<void> | undefined;
  waitForExecution(id: string): Promise<void> | undefined;
}
export function createExecutionWorker(
  deps: NoteDependencies,
  appendEvent: ReturnType<typeof createEventStore>['appendEvent'],
): ExecutionWorker {
  const { NoteCellExecution, NoteCellRevision, NoteExecutionArtifact } = deps.models;
  const { bridgeConversationFiles, executeOnEngine } = deps;
  const runningJobs = new Map<string, Promise<void>>();
  async function registerArtifacts(
    execution: Execution & { _id: Types.ObjectId },
    engineResult: NoteEngineResult,
  ) {
    const artifacts = buildNoteArtifacts(execution, engineResult);
    return artifacts.length ? NoteExecutionArtifact.insertMany(artifacts) : [];
  }

  async function processExecution(executionId: Identifier) {
    const execution = await NoteCellExecution.findById(executionId);
    if (!execution || TERMINAL.has(execution.status)) {
      return;
    }

    const revision = await NoteCellRevision.findById(execution.revisionId);
    if (!revision) {
      execution.status = 'failed';
      execution.error = 'Revision not found';
      execution.finishedAt = new Date();
      await execution.save();
      await appendEvent(execution._id, execution.conversationId, 'failed', 'failed', {
        error: execution.error,
      });
      return;
    }

    execution.status = 'running';
    execution.startedAt = new Date();
    await execution.save();
    await appendEvent(execution._id, execution.conversationId, 'running', 'running');

    try {
      try {
        await bridgeConversationFiles(execution.conversationId, execution.user);
      } catch (bridgeErr) {
        logger.warn('[NoteCells] Data bridge pre-execution warning:', errorMessage(bridgeErr));
      }

      const result = await executeOnEngine({
        code: revision.content,
        threadId: execution.conversationId,
        executionId: toId(execution._id),
        timeoutSeconds: execution.timeoutSeconds,
      });

      const fresh = await NoteCellExecution.findById(execution._id);
      if (fresh?.cancelRequested) {
        fresh.status = 'cancelled';
        fresh.finishedAt = new Date();
        fresh.error = 'Cancelled';
        await fresh.save();
        await appendEvent(fresh._id, fresh.conversationId, 'cancelled', 'cancelled');
        return;
      }

      execution.stdout = result.stdout || '';
      execution.markdown = result.markdown || '';
      execution.engineCellId = result.cell_id || null;
      execution.engineRunDir = result.engine_run_dir || result.run_dir || null;

      if (result.cancelled || result.timed_out) {
        execution.status = result.cancelled ? 'cancelled' : 'timed_out';
        execution.error = result.error || execution.status;
      } else if (result.success === false) {
        execution.status = 'failed';
        execution.error = result.error || 'Execution failed';
      } else {
        execution.status = 'completed';
        execution.error = result.error || null;
      }
      execution.finishedAt = new Date();
      await execution.save();
      await registerArtifacts(execution, result);
      await appendEvent(
        execution._id,
        execution.conversationId,
        execution.status,
        execution.status,
        {
          error: execution.error,
        },
      );
    } catch (err) {
      const fresh = await NoteCellExecution.findById(execution._id);
      if (!fresh || TERMINAL.has(fresh.status)) {
        return;
      }
      if (errorCode(err) === 'CANCELLED' || fresh.cancelRequested) {
        fresh.status = 'cancelled';
        fresh.error = 'Cancelled';
      } else if (errorCode(err) === 'TIMED_OUT') {
        fresh.status = 'timed_out';
        fresh.error = errorMessage(err);
      } else {
        fresh.status = 'failed';
        fresh.error = errorMessage(err) || String(err);
      }
      fresh.finishedAt = new Date();
      await fresh.save();
      await appendEvent(fresh._id, fresh.conversationId, fresh.status, fresh.status, {
        error: fresh.error,
      });
    } finally {
      runningJobs.delete(toId(executionId));
    }
  }

  function enqueueExecution(executionId: Identifier) {
    const key = toId(executionId);
    if (runningJobs.has(key)) {
      return runningJobs.get(key);
    }
    const promise = processExecution(executionId).catch((err) => {
      logger.error(`[NoteCells] execution job failed: ${errorMessage(err)}`);
    });
    runningJobs.set(key, promise);
    return promise;
  }

  return { enqueueExecution, waitForExecution: (id: string) => runningJobs.get(id) };
}
