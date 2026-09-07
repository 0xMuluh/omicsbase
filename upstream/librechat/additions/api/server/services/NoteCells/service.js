const mongoose = require('mongoose');
const { buildNoteArtifacts, createNoteAgentLifecycle } = require('@librechat/api');
const { logger } = require('@librechat/data-schemas');
const {
  NoteCell,
  NoteCellRevision,
  NoteCellExecution,
  NoteExecutionArtifact,
  NoteExecutionEvent,
} = require('./models');
const {
  executeOnEngine,
  fetchArtifactFromEngine,
  INTERNAL_SECRET,
  ENGINE_URL,
} = require('./engineClient');
const { bridgeConversationFiles } = require('./noteDataBridge');

const TERMINAL = new Set(['completed', 'failed', 'timed_out', 'cancelled']);
const runningJobs = new Map();

function toId(doc) {
  if (!doc) {
    return null;
  }
  return (doc._id || doc).toString();
}

function serializeCell(cell, revision = null, execution = null) {
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

function serializeRevision(rev) {
  return {
    id: toId(rev),
    cellId: toId(rev.cellId),
    revision: rev.revision,
    language: rev.language,
    content: rev.content,
    createdAt: rev.createdAt,
  };
}

function serializeExecution(exec, artifacts = undefined) {
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

function serializeArtifact(art) {
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

async function assertConversationAccess(conversationId, userId) {
  const Conversation = mongoose.models.Conversation;
  if (!Conversation) {
    return true;
  }
  const convo = await Conversation.findOne({ conversationId, user: userId }).select('_id').lean();
  if (!convo) {
    const err = new Error('Conversation not found');
    err.status = 404;
    throw err;
  }
  return true;
}

async function appendEvent(executionId, conversationId, eventType, status, payload = null) {
  const last = await NoteExecutionEvent.findOne({ executionId }).sort({ sequence: -1 }).lean();
  const sequence = (last?.sequence || 0) + 1;
  await NoteExecutionEvent.create({
    executionId,
    conversationId,
    sequence,
    eventType,
    status,
    payload,
  });
  return sequence;
}

async function createCell({
  conversationId,
  userId,
  content = '',
  messageId = null,
  blockKey = null,
  position = 0,
}) {
  const cell = await NoteCell.create({
    conversationId,
    user: userId,
    messageId,
    blockKey,
    position,
    status: 'active',
  });

  const revision = await NoteCellRevision.create({
    cellId: cell._id,
    conversationId,
    user: userId,
    revision: 1,
    language: 'r',
    content: content || '',
  });

  cell.latestRevisionId = revision._id;
  await cell.save();

  return serializeCell(cell, revision);
}

async function findOrCreateCell({
  conversationId,
  userId,
  cellId = null,
  messageId = null,
  blockKey = null,
  content = '',
}) {
  if (cellId && mongoose.isValidObjectId(cellId)) {
    const cell = await NoteCell.findOne({ _id: cellId, conversationId, user: userId });
    if (cell) {
      const revision = cell.latestRevisionId
        ? await NoteCellRevision.findById(cell.latestRevisionId)
        : null;
      return serializeCell(cell, revision);
    }
  }

  if (messageId && blockKey) {
    const existing = await NoteCell.findOne({
      conversationId,
      user: userId,
      messageId,
      blockKey,
    });
    if (existing) {
      const revision = existing.latestRevisionId
        ? await NoteCellRevision.findById(existing.latestRevisionId)
        : null;
      return serializeCell(existing, revision);
    }
  }

  return createCell({ conversationId, userId, content, messageId, blockKey });
}

async function listCells(conversationId, userId) {
  const cells = await NoteCell.find({ conversationId, user: userId }).sort({
    position: 1,
    createdAt: 1,
  });
  const out = [];
  for (const cell of cells) {
    const revision = cell.latestRevisionId
      ? await NoteCellRevision.findById(cell.latestRevisionId).lean()
      : null;
    let execution = null;
    let artifacts = undefined;
    if (cell.latestExecutionId) {
      execution = await NoteCellExecution.findById(cell.latestExecutionId).lean();
      if (execution) {
        artifacts = await NoteExecutionArtifact.find({ executionId: execution._id }).lean();
      }
    }
    const serialized = serializeCell(cell, revision, execution);
    if (artifacts) {
      serialized.latestExecution = {
        ...serializeExecution(execution),
        artifacts: artifacts.map(serializeArtifact),
      };
    }
    out.push(serialized);
  }
  return out;
}

async function getCell(conversationId, userId, cellId) {
  const cell = await NoteCell.findOne({ _id: cellId, conversationId, user: userId });
  if (!cell) {
    const err = new Error('Cell not found');
    err.status = 404;
    throw err;
  }
  const revision = cell.latestRevisionId
    ? await NoteCellRevision.findById(cell.latestRevisionId)
    : null;
  let execution = null;
  let artifacts;
  if (cell.latestExecutionId) {
    execution = await NoteCellExecution.findById(cell.latestExecutionId);
    if (execution) {
      artifacts = await NoteExecutionArtifact.find({ executionId: execution._id });
    }
  }
  const result = serializeCell(cell, revision, execution);
  if (execution && artifacts) {
    result.latestExecution = {
      ...serializeExecution(execution),
      artifacts: artifacts.map(serializeArtifact),
    };
  }
  return result;
}

async function appendRevision({ conversationId, userId, cellId, content, language = 'r' }) {
  const cell = await NoteCell.findOne({ _id: cellId, conversationId, user: userId });
  if (!cell) {
    const err = new Error('Cell not found');
    err.status = 404;
    throw err;
  }
  const last = await NoteCellRevision.findOne({ cellId: cell._id }).sort({ revision: -1 });
  const nextRev = (last?.revision || 0) + 1;
  const revision = await NoteCellRevision.create({
    cellId: cell._id,
    conversationId,
    user: userId,
    revision: nextRev,
    language,
    content: content ?? '',
  });
  cell.latestRevisionId = revision._id;
  await cell.save();
  return serializeRevision(revision);
}

async function registerArtifacts(execution, engineResult) {
  const artifacts = buildNoteArtifacts(execution, engineResult);
  return artifacts.length ? NoteExecutionArtifact.insertMany(artifacts) : [];
}

async function processExecution(executionId) {
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
      logger.warn('[NoteCells] Data bridge pre-execution warning:', bridgeErr.message);
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
    await appendEvent(execution._id, execution.conversationId, execution.status, execution.status, {
      error: execution.error,
    });
  } catch (err) {
    const fresh = await NoteCellExecution.findById(execution._id);
    if (!fresh || TERMINAL.has(fresh.status)) {
      return;
    }
    if (err.code === 'CANCELLED' || fresh.cancelRequested) {
      fresh.status = 'cancelled';
      fresh.error = 'Cancelled';
    } else if (err.code === 'TIMED_OUT') {
      fresh.status = 'timed_out';
      fresh.error = err.message;
    } else {
      fresh.status = 'failed';
      fresh.error = err.message || String(err);
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

function enqueueExecution(executionId) {
  const key = toId(executionId);
  if (runningJobs.has(key)) {
    return runningJobs.get(key);
  }
  const promise = processExecution(executionId).catch((err) => {
    logger.error(`[NoteCells] execution job failed: ${err.message}`);
  });
  runningJobs.set(key, promise);
  return promise;
}

async function executeCell({
  conversationId,
  userId,
  cellId,
  revisionId = null,
  timeoutSeconds = 180,
}) {
  const cell = await NoteCell.findOne({ _id: cellId, conversationId, user: userId });
  if (!cell) {
    const err = new Error('Cell not found');
    err.status = 404;
    throw err;
  }

  const revId = revisionId || cell.latestRevisionId;
  if (!revId) {
    const err = new Error('No saved revision; save before run');
    err.status = 400;
    throw err;
  }

  const revision = await NoteCellRevision.findOne({ _id: revId, cellId: cell._id });
  if (!revision) {
    const err = new Error('Revision not found');
    err.status = 404;
    throw err;
  }

  const attempt = (await NoteCellExecution.countDocuments({ cellId: cell._id })) + 1;

  const execution = await NoteCellExecution.create({
    cellId: cell._id,
    revisionId: revision._id,
    conversationId,
    user: userId,
    attempt,
    status: 'queued',
    timeoutSeconds,
  });

  cell.latestExecutionId = execution._id;
  await cell.save();
  await appendEvent(execution._id, conversationId, 'queued', 'queued');
  enqueueExecution(execution._id);

  return serializeExecution(execution);
}

async function getExecution(conversationId, userId, cellId, executionId) {
  const execution = await NoteCellExecution.findOne({
    _id: executionId,
    cellId,
    conversationId,
    user: userId,
  });
  if (!execution) {
    const err = new Error('Execution not found');
    err.status = 404;
    throw err;
  }
  const artifacts = await NoteExecutionArtifact.find({ executionId: execution._id });
  return {
    ...serializeExecution(execution),
    artifacts: artifacts.map(serializeArtifact),
  };
}

async function listExecutions(conversationId, userId, cellId) {
  const executions = await NoteCellExecution.find({
    cellId,
    conversationId,
    user: userId,
  })
    .sort({ createdAt: -1 })
    .limit(50);
  return executions.map((e) => serializeExecution(e));
}

async function cancelExecution(conversationId, userId, cellId, executionId) {
  const execution = await NoteCellExecution.findOne({
    _id: executionId,
    cellId,
    conversationId,
    user: userId,
  });
  if (!execution) {
    const err = new Error('Execution not found');
    err.status = 404;
    throw err;
  }
  if (TERMINAL.has(execution.status)) {
    return serializeExecution(execution);
  }
  execution.cancelRequested = true;
  await execution.save();
  await appendEvent(execution._id, conversationId, 'cancel_requested', execution.status);

  // Signal NoteKernel cancel flag (best-effort)
  try {
    const headers = { 'Content-Type': 'application/json' };
    if (INTERNAL_SECRET) {
      headers['X-Internal-Secret'] = INTERNAL_SECRET;
    }
    await fetch(`${ENGINE_URL}/api/cancel`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ thread_id: conversationId, execution_id: toId(execution._id) }),
    });
  } catch (err) {
    logger.warn(`[NoteCells] engine cancel signal failed: ${err.message}`);
  }

  return serializeExecution(execution);
}

async function listEvents(conversationId, userId, cellId, executionId, afterSequence = 0) {
  const execution = await NoteCellExecution.findOne({
    _id: executionId,
    cellId,
    conversationId,
    user: userId,
  })
    .select('_id')
    .lean();
  if (!execution) {
    const err = new Error('Execution not found');
    err.status = 404;
    throw err;
  }
  const events = await NoteExecutionEvent.find({
    executionId,
    sequence: { $gt: afterSequence },
  }).sort({ sequence: 1 });
  return events.map((e) => ({
    id: toId(e),
    executionId: toId(e.executionId),
    sequence: e.sequence,
    eventType: e.eventType,
    status: e.status,
    payload: e.payload,
    createdAt: e.createdAt,
  }));
}

async function getArtifactContent(conversationId, userId, cellId, executionId, artifactId) {
  const execution = await NoteCellExecution.findOne({
    _id: executionId,
    cellId,
    conversationId,
    user: userId,
  });
  if (!execution) {
    const err = new Error('Execution not found');
    err.status = 404;
    throw err;
  }
  const artifact = await NoteExecutionArtifact.findOne({
    _id: artifactId,
    executionId: execution._id,
  });
  if (!artifact) {
    const err = new Error('Artifact not found');
    err.status = 404;
    throw err;
  }

  if (artifact.artifactType === 'console' && execution.stdout != null) {
    return {
      buffer: Buffer.from(execution.stdout, 'utf8'),
      contentType: 'text/plain; charset=utf-8',
      artifact: serializeArtifact(artifact),
    };
  }

  const { buffer, contentType } = await fetchArtifactFromEngine(
    conversationId,
    artifact.relativePath,
  );
  return {
    buffer,
    contentType: artifact.mimeType || contentType,
    artifact: serializeArtifact(artifact),
  };
}

/**
 * Agent path: upsert cell + revision, queue execution, wait until terminal.
 * Returns markdown + ids for MCP tool output.
 */
async function executeSyncForAgent({ conversationId, userId, code, timeoutSeconds = 180 }) {
  try {
    await bridgeConversationFiles(conversationId, userId);
  } catch (bridgeErr) {
    logger.warn('[NoteCells] Data bridge pre-agent-execution warning:', bridgeErr.message);
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

  await runningJobs.get(execution.id);
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

async function resolveUserForConversation(conversationId) {
  const Conversation = mongoose.models.Conversation;
  if (!Conversation) {
    return null;
  }
  const convo = await Conversation.findOne({ conversationId }).select('user').lean();
  return convo?.user ? String(convo.user) : null;
}

function verifyInternalSecret(headerValue) {
  if (!INTERNAL_SECRET) {
    return false;
  }
  return headerValue === INTERNAL_SECRET;
}

const agentLifecycle = createNoteAgentLifecycle({
  NoteCell,
  NoteCellRevision,
  NoteCellExecution,
  NoteExecutionArtifact,
  NoteExecutionEvent,
});

module.exports = {
  agentLifecycle,
  TERMINAL,
  assertConversationAccess,
  createCell,
  findOrCreateCell,
  listCells,
  getCell,
  appendRevision,
  executeCell,
  getExecution,
  listExecutions,
  cancelExecution,
  listEvents,
  getArtifactContent,
  executeSyncForAgent,
  resolveUserForConversation,
  verifyInternalSecret,
  serializeArtifact,
  enqueueExecution,
};
