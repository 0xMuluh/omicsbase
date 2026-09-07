const express = require('express');
const requireJwtAuth = require('~/server/middleware/requireJwtAuth');
const noteCells = require('~/server/services/NoteCells/service');

const router = express.Router({ mergeParams: true });

const getUserId = (req) => req.user?.id ?? req.user?._id?.toString() ?? '';

const asyncHandler = (fn) => (req, res, next) => {
  Promise.resolve(fn(req, res, next)).catch(next);
};

function sendError(res, err) {
  const status = err.status || 500;
  return res.status(status).json({ error: err.message || 'Internal error' });
}

function withArtifactUrls(conversationId, cellId, executionId, payload) {
  if (!payload) {
    return payload;
  }
  const base = `/api/notes/${encodeURIComponent(conversationId)}/cells/${encodeURIComponent(cellId)}/executions/${encodeURIComponent(executionId)}/artifacts`;
  if (Array.isArray(payload.artifacts)) {
    payload.artifacts = payload.artifacts.map((a) => ({
      ...a,
      contentUrl: `${base}/${encodeURIComponent(a.id)}/content`,
    }));
  }
  return payload;
}

/** Internal agent sync execute (engine MCP → LibreChat durable path). No JWT. */
router.post(
  '/:conversationId/internal/execute-sync',
  asyncHandler(async (req, res) => {
    if (!noteCells.verifyInternalSecret(req.get('X-Internal-Secret'))) {
      return res.status(401).json({ error: 'Unauthorized' });
    }
    const { conversationId } = req.params;
    const code = req.body?.code ?? '';
    const timeoutSeconds = Number(req.body?.timeout_seconds) || 180;
    let userId = req.body?.user_id || (await noteCells.resolveUserForConversation(conversationId));
    if (!userId) {
      userId = 'system';
    }
    try {
      const result = await noteCells.executeSyncForAgent({
        conversationId,
        userId: String(userId),
        code,
        timeoutSeconds,
      });
      return res.json(result);
    } catch (err) {
      return sendError(res, err);
    }
  }),
);

router.post(
  '/:conversationId/internal/start',
  asyncHandler(async (req, res) => {
    if (!noteCells.verifyInternalSecret(req.get('X-Internal-Secret'))) {
      return res.status(401).json({ error: 'Unauthorized' });
    }
    const { conversationId } = req.params;
    const userId = await noteCells.resolveUserForConversation(conversationId);
    if (!userId) {
      return res.status(404).json({ error: 'Conversation not found' });
    }
    const result = await noteCells.agentLifecycle.start(
      conversationId,
      userId,
      req.body?.code ?? '',
      Number(req.body?.timeout_seconds) || 180,
      req.body?.cell_id,
    );
    res.json(result);
  }),
);

router.post(
  '/:conversationId/internal/finish/:executionId',
  asyncHandler(async (req, res) => {
    if (!noteCells.verifyInternalSecret(req.get('X-Internal-Secret'))) {
      return res.status(401).json({ error: 'Unauthorized' });
    }
    try {
      const result = await noteCells.agentLifecycle.finish(
        req.params.conversationId,
        req.params.executionId,
        req.body,
      );
      res.json(result);
    } catch (err) {
      return sendError(res, err);
    }
  }),
);

const authed = express.Router({ mergeParams: true });
authed.use(requireJwtAuth);

authed.use(
  asyncHandler(async (req, res, next) => {
    const userId = getUserId(req);
    if (!userId) {
      return res.status(401).json({ error: 'Unauthorized' });
    }
    const conversationId = req.params.conversationId;
    try {
      await noteCells.assertConversationAccess(conversationId, userId);
    } catch (err) {
      if (
        err.status === 404 &&
        req.method === 'POST' &&
        (req.path === '/cells' || req.path.endsWith('/cells'))
      ) {
        req.noteUserId = userId;
        req.noteConversationId = conversationId;
        return next();
      }
      return sendError(res, err);
    }
    req.noteUserId = userId;
    req.noteConversationId = conversationId;
    return next();
  }),
);

authed.get(
  '/cells',
  asyncHandler(async (req, res) => {
    const cells = await noteCells.listCells(req.noteConversationId, req.noteUserId);
    for (const cell of cells) {
      if (cell.latestExecution) {
        withArtifactUrls(
          req.noteConversationId,
          cell.id,
          cell.latestExecution.id,
          cell.latestExecution,
        );
      }
    }
    res.json({ cells });
  }),
);

authed.post(
  '/cells',
  asyncHandler(async (req, res) => {
    const { content, messageId, blockKey, cellId } = req.body || {};
    const cell = await noteCells.findOrCreateCell({
      conversationId: req.noteConversationId,
      userId: req.noteUserId,
      cellId,
      messageId: messageId || null,
      blockKey: blockKey || null,
      content: content || '',
    });
    res.status(201).json(cell);
  }),
);

authed.get(
  '/cells/:cellId',
  asyncHandler(async (req, res) => {
    const cell = await noteCells.getCell(req.noteConversationId, req.noteUserId, req.params.cellId);
    if (cell.latestExecution) {
      withArtifactUrls(
        req.noteConversationId,
        cell.id,
        cell.latestExecution.id,
        cell.latestExecution,
      );
    }
    res.json(cell);
  }),
);

authed.post(
  '/cells/:cellId/revisions',
  asyncHandler(async (req, res) => {
    const revision = await noteCells.appendRevision({
      conversationId: req.noteConversationId,
      userId: req.noteUserId,
      cellId: req.params.cellId,
      content: req.body?.content ?? '',
      language: req.body?.language || 'r',
    });
    res.status(201).json(revision);
  }),
);

authed.post(
  '/cells/:cellId/execute',
  asyncHandler(async (req, res) => {
    const execution = await noteCells.executeCell({
      conversationId: req.noteConversationId,
      userId: req.noteUserId,
      cellId: req.params.cellId,
      revisionId: req.body?.revisionId || null,
      timeoutSeconds: Number(req.body?.timeout_seconds) || 180,
    });
    res.status(202).json(execution);
  }),
);

authed.get(
  '/cells/:cellId/executions',
  asyncHandler(async (req, res) => {
    const executions = await noteCells.listExecutions(
      req.noteConversationId,
      req.noteUserId,
      req.params.cellId,
    );
    res.json({ executions });
  }),
);

authed.post(
  '/cells/:cellId/executions/:executionId/cancel',
  asyncHandler(async (req, res) => {
    const execution = await noteCells.cancelExecution(
      req.noteConversationId,
      req.noteUserId,
      req.params.cellId,
      req.params.executionId,
    );
    res.json(execution);
  }),
);

authed.get(
  '/cells/:cellId/executions/:executionId',
  asyncHandler(async (req, res) => {
    const execution = await noteCells.getExecution(
      req.noteConversationId,
      req.noteUserId,
      req.params.cellId,
      req.params.executionId,
    );
    withArtifactUrls(req.noteConversationId, req.params.cellId, req.params.executionId, execution);
    res.json(execution);
  }),
);

authed.get(
  '/cells/:cellId/executions/:executionId/events',
  asyncHandler(async (req, res) => {
    const after = Number(req.query.after_sequence) || 0;
    const events = await noteCells.listEvents(
      req.noteConversationId,
      req.noteUserId,
      req.params.cellId,
      req.params.executionId,
      after,
    );
    res.json({ events });
  }),
);

authed.get(
  '/cells/:cellId/executions/:executionId/artifacts/:artifactId/content',
  asyncHandler(async (req, res) => {
    const { buffer, contentType, artifact } = await noteCells.getArtifactContent(
      req.noteConversationId,
      req.noteUserId,
      req.params.cellId,
      req.params.executionId,
      req.params.artifactId,
    );
    res.setHeader('Content-Type', contentType);
    res.setHeader(
      'Content-Disposition',
      `inline; filename="${(artifact.relativePath || 'artifact').split('/').pop()}"`,
    );
    res.send(buffer);
  }),
);

authed.use((err, req, res, next) => {
  if (res.headersSent) {
    return next(err);
  }
  return sendError(res, err);
});

router.use('/:conversationId', authed);

module.exports = router;
