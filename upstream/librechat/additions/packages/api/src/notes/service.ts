import { logger } from '@librechat/data-schemas';
import { createNoteAgentLifecycle } from './agent';
import { createCellStore } from './cells';
import { createEventStore } from './events';
import { createExecutionWorker } from './worker';
import { createExecutionStore } from './executions';
import { NoteError, errorMessage } from './errors';
import { serializeArtifact } from './serialization';
import { TERMINAL } from './constants';
import type { NoteDependencies } from './serviceTypes';

type AgentExecution = Awaited<ReturnType<ReturnType<typeof createExecutionStore>['getExecution']>>;
export type NoteService = ReturnType<typeof createCellStore> &
  ReturnType<typeof createExecutionStore> &
  ReturnType<typeof createEventStore> &
  ReturnType<typeof createExecutionWorker> & {
    agentLifecycle: ReturnType<typeof createNoteAgentLifecycle>;
    TERMINAL: typeof TERMINAL;
    assertConversationAccess(conversationId: string, userId: string): Promise<boolean>;
    executeSyncForAgent(input: {
      conversationId: string;
      userId: string;
      code: string;
      timeoutSeconds?: number;
    }): Promise<{
      cellId: string;
      revisionId: string | null;
      executionId: string;
      status: AgentExecution['status'];
      success: boolean;
      markdown: string;
      error: AgentExecution['error'];
      stdout: AgentExecution['stdout'];
      artifacts: AgentExecution['artifacts'];
    }>;
    resolveUserForConversation(conversationId: string): Promise<string | null>;
    verifyInternalSecret(headerValue: string | undefined): boolean;
    serializeArtifact: typeof serializeArtifact;
  };

export function createNoteService(deps: NoteDependencies): NoteService {
  const { bridgeConversationFiles, internalSecret: INTERNAL_SECRET } = deps;
  const cells = createCellStore(deps.models);
  const events = createEventStore(deps.models);
  const worker = createExecutionWorker(deps, events.appendEvent);
  const executions = createExecutionStore(deps, events.appendEvent, worker.enqueueExecution);
  const { findOrCreateCell } = cells;
  const { executeCell, getExecution } = executions;
  async function assertConversationAccess(conversationId: string, userId: string) {
    const Conversation = deps.getConversationModel();
    if (!Conversation) {
      return true;
    }
    const convo = await Conversation.findOne({ conversationId, user: userId }).select('_id').lean();
    if (!convo) {
      throw new NoteError('Conversation not found', 404);
    }
    return true;
  }

  async function executeSyncForAgent({
    conversationId,
    userId,
    code,
    timeoutSeconds = 180,
  }: {
    conversationId: string;
    userId: string;
    code: string;
    timeoutSeconds?: number;
  }) {
    try {
      await bridgeConversationFiles(conversationId, userId);
    } catch (bridgeErr) {
      logger.warn('[NoteCells] Data bridge pre-agent-execution warning:', errorMessage(bridgeErr));
    }

    const cellPayload = await findOrCreateCell({
      conversationId,
      userId,
      messageId: null,
      blockKey: `agent-${Date.now()}`,
      content: code || '',
    });

    const execution = await executeCell({
      conversationId,
      userId,
      cellId: cellPayload.id,
      revisionId: cellPayload.latestRevisionId,
      timeoutSeconds,
    });

    await worker.waitForExecution(execution.id);
    const current = await getExecution(conversationId, userId, cellPayload.id, execution.id);

    return {
      cellId: cellPayload.id,
      revisionId: cellPayload.latestRevisionId,
      executionId: current.id,
      status: current.status,
      success: current.status === 'completed',
      markdown: current.markdown || current.stdout || current.error || '(no output)',
      error: current.error,
      stdout: current.stdout,
      artifacts: current.artifacts || [],
    };
  }

  async function resolveUserForConversation(conversationId: string) {
    const Conversation = deps.getConversationModel();
    if (!Conversation) {
      return null;
    }
    const convo = await Conversation.findOne({ conversationId }).select('user').lean();
    return convo?.user ? String(convo.user) : null;
  }

  function verifyInternalSecret(headerValue: string | undefined) {
    if (!INTERNAL_SECRET) {
      return false;
    }
    return headerValue === INTERNAL_SECRET;
  }

  return {
    createCell: cells.createCell,
    findOrCreateCell: cells.findOrCreateCell,
    listCells: cells.listCells,
    getCell: cells.getCell,
    appendRevision: cells.appendRevision,
    executeCell: executions.executeCell,
    getExecution: executions.getExecution,
    listExecutions: executions.listExecutions,
    cancelExecution: executions.cancelExecution,
    getArtifactContent: executions.getArtifactContent,
    appendEvent: events.appendEvent,
    listEvents: events.listEvents,
    enqueueExecution: worker.enqueueExecution,
    waitForExecution: worker.waitForExecution,
    agentLifecycle: createNoteAgentLifecycle(deps.models),
    TERMINAL,
    assertConversationAccess,
    executeSyncForAgent,
    resolveUserForConversation,
    verifyInternalSecret,
    serializeArtifact,
  };
}
