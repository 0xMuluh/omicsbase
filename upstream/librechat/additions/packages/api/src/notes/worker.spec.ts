import { createExecutionWorker } from './worker';

function makeExecution() {
  return {
    _id: 'execution-1',
    cellId: 'cell-1',
    revisionId: 'revision-1',
    conversationId: 'conversation-1',
    user: 'user-1',
    status: 'queued',
    attempt: 1,
    timeoutSeconds: 180,
    save: jest.fn(async () => undefined),
  };
}

test('keeps execution non-terminal until artifacts are registered', async () => {
  const execution = makeExecution();
  const statusAtArtifactInsert: string[] = [];
  const artifacts = {
    insertMany: jest.fn(async () => {
      statusAtArtifactInsert.push(execution.status);
      return [];
    }),
  };
  const models = {
    NoteCellExecution: { findById: jest.fn(async () => execution) },
    NoteCellRevision: { findById: jest.fn(async () => ({ content: 'plot(1)' })) },
    NoteExecutionArtifact: artifacts,
  };
  const appendEvent = jest.fn(async () => undefined);
  const worker = createExecutionWorker(
    {
      models,
      bridgeConversationFiles: jest.fn(async () => undefined),
      executeOnEngine: jest.fn(async () => ({
        success: true,
        stdout: '',
        plots: ['http://engine/projects/conversation-1/runs/cell/plots/plot_001.png'],
        run_dir: 'runs/cell',
      })),
      fetchArtifactFromEngine: jest.fn(),
      signalCancel: jest.fn(),
      internalSecret: 'test-secret',
    } as never,
    appendEvent,
  );

  await worker.enqueueExecution('execution-1');

  expect(statusAtArtifactInsert).toEqual(['running']);
  expect(execution.status).toBe('completed');
  expect(appendEvent).toHaveBeenLastCalledWith(
    'execution-1',
    'conversation-1',
    'completed',
    'completed',
    { error: null },
  );
});

test('does not execute R when attachment preparation fails', async () => {
  const execution = makeExecution();
  const executeOnEngine = jest.fn();
  const worker = createExecutionWorker({
    models: {
      NoteCellExecution: { findById: jest.fn(async () => execution) },
      NoteCellRevision: { findById: jest.fn(async () => ({ content: 'readRDS("data/tse.Rds")' })) },
    },
    bridgeConversationFiles: jest.fn(async () => { throw new Error('Attachment permission denied'); }),
    executeOnEngine,
  } as never, jest.fn(async () => undefined));
  await worker.enqueueExecution('execution-1');
  expect(executeOnEngine).not.toHaveBeenCalled();
  expect(execution.status).toBe('failed');
});
