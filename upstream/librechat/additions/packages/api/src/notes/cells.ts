import { isValidObjectId } from 'mongoose';
import { NoteError } from './errors';
import {
  serializeCell,
  serializeRevision,
  serializeExecution,
  serializeArtifact,
} from './serialization';
import type { NoteModels, CellInput, RevisionInput } from './serviceTypes';

import type { SerializedCell, SerializedRevision } from './serialization';
export interface CellStore {
  createCell(input: CellInput): Promise<SerializedCell>;
  findOrCreateCell(input: CellInput): Promise<SerializedCell>;
  listCells(conversationId: string, userId: string): Promise<SerializedCell[]>;
  getCell(conversationId: string, userId: string, cellId: string): Promise<SerializedCell>;
  appendRevision(input: RevisionInput): Promise<SerializedRevision>;
}
export function createCellStore(models: NoteModels): CellStore {
  const { NoteCell, NoteCellRevision, NoteCellExecution, NoteExecutionArtifact } = models;
  async function createCell({
    conversationId,
    userId,
    content = '',
    messageId = null,
    blockKey = null,
    position = 0,
  }: CellInput) {
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
  }: CellInput) {
    if (cellId && isValidObjectId(cellId)) {
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

  async function listCells(conversationId: string, userId: string) {
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
      if (execution && artifacts) {
        serialized.latestExecution = {
          ...serializeExecution(execution),
          artifacts: artifacts.map(serializeArtifact),
        };
      }
      out.push(serialized);
    }
    return out;
  }

  async function getCell(conversationId: string, userId: string, cellId: string) {
    const cell = await NoteCell.findOne({ _id: cellId, conversationId, user: userId });
    if (!cell) {
      throw new NoteError('Cell not found', 404);
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

  async function appendRevision({
    conversationId,
    userId,
    cellId,
    content,
    language = 'r',
  }: RevisionInput) {
    const cell = await NoteCell.findOne({ _id: cellId, conversationId, user: userId });
    if (!cell) {
      throw new NoteError('Cell not found', 404);
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

  return { createCell, findOrCreateCell, listCells, getCell, appendRevision };
}
