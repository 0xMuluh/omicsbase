const mongoose = require('mongoose');

const EXECUTION_STATUSES = [
  'queued',
  'running',
  'completed',
  'failed',
  'timed_out',
  'cancelled',
];

const noteCellSchema = new mongoose.Schema(
  {
    conversationId: { type: String, required: true, index: true },
    user: { type: String, required: true, index: true },
    messageId: { type: String, default: null, index: true },
    blockKey: { type: String, default: null },
    position: { type: Number, default: 0 },
    status: { type: String, default: 'active' },
    latestRevisionId: { type: mongoose.Schema.Types.ObjectId, default: null },
    latestExecutionId: { type: mongoose.Schema.Types.ObjectId, default: null },
  },
  { timestamps: true },
);

noteCellSchema.index({ conversationId: 1, user: 1, messageId: 1, blockKey: 1 });

const noteCellRevisionSchema = new mongoose.Schema(
  {
    cellId: { type: mongoose.Schema.Types.ObjectId, required: true, index: true },
    conversationId: { type: String, required: true, index: true },
    user: { type: String, required: true },
    revision: { type: Number, required: true },
    language: { type: String, default: 'r' },
    content: { type: String, default: '' },
  },
  { timestamps: { createdAt: true, updatedAt: false } },
);

noteCellRevisionSchema.index({ cellId: 1, revision: 1 }, { unique: true });

const noteCellExecutionSchema = new mongoose.Schema(
  {
    cellId: { type: mongoose.Schema.Types.ObjectId, required: true, index: true },
    revisionId: { type: mongoose.Schema.Types.ObjectId, required: true },
    conversationId: { type: String, required: true, index: true },
    user: { type: String, required: true },
    attempt: { type: Number, default: 1 },
    status: { type: String, enum: EXECUTION_STATUSES, default: 'queued', index: true },
    timeoutSeconds: { type: Number, default: 180 },
    cancelRequested: { type: Boolean, default: false },
    error: { type: String, default: null },
    stdout: { type: String, default: null },
    markdown: { type: String, default: null },
    engineRunDir: { type: String, default: null },
    engineCellId: { type: String, default: null },
    startedAt: { type: Date, default: null },
    finishedAt: { type: Date, default: null },
  },
  { timestamps: true },
);

const noteExecutionArtifactSchema = new mongoose.Schema(
  {
    executionId: { type: mongoose.Schema.Types.ObjectId, required: true, index: true },
    conversationId: { type: String, required: true },
    user: { type: String, required: true },
    artifactType: { type: String, enum: ['plot', 'table', 'console'], required: true },
    relativePath: { type: String, required: true },
    mimeType: { type: String, default: 'application/octet-stream' },
    byteSize: { type: Number, default: 0 },
    previewMarkdown: { type: String, default: null },
    rows: { type: Number, default: null },
    cols: { type: Number, default: null },
  },
  { timestamps: { createdAt: true, updatedAt: false } },
);

const noteExecutionEventSchema = new mongoose.Schema(
  {
    executionId: { type: mongoose.Schema.Types.ObjectId, required: true, index: true },
    conversationId: { type: String, required: true },
    sequence: { type: Number, required: true },
    eventType: { type: String, required: true },
    status: { type: String, default: null },
    payload: { type: mongoose.Schema.Types.Mixed, default: null },
  },
  { timestamps: { createdAt: true, updatedAt: false } },
);

noteExecutionEventSchema.index({ executionId: 1, sequence: 1 }, { unique: true });

function getModel(name, schema) {
  return mongoose.models[name] || mongoose.model(name, schema);
}

module.exports = {
  EXECUTION_STATUSES,
  NoteCell: getModel('NoteCell', noteCellSchema),
  NoteCellRevision: getModel('NoteCellRevision', noteCellRevisionSchema),
  NoteCellExecution: getModel('NoteCellExecution', noteCellExecutionSchema),
  NoteExecutionArtifact: getModel('NoteExecutionArtifact', noteExecutionArtifactSchema),
  NoteExecutionEvent: getModel('NoteExecutionEvent', noteExecutionEventSchema),
};
