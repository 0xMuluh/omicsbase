import { NoteError } from './errors';
import { toId } from './serialization';
import type { NoteModels, Identifier } from './serviceTypes';
import type { Status } from './contracts';

import type { Event } from './contracts';
export interface EventStore {
  appendEvent(
    executionId: Identifier,
    conversationId: string,
    eventType: string,
    status: Status,
    payload?: { error?: string | null } | null,
  ): Promise<number>;
  listEvents(
    conversationId: string,
    userId: string,
    cellId: string,
    executionId: string,
    afterSequence?: number,
  ): Promise<
    (Omit<Event, 'executionId' | 'conversationId'> & { id: string; executionId: string })[]
  >;
}
export function createEventStore(models: NoteModels): EventStore {
  const { NoteExecutionEvent, NoteCellExecution } = models;
  async function appendEvent(
    executionId: Identifier,
    conversationId: string,
    eventType: string,
    status: Status,
    payload: { error?: string | null } | null = null,
  ) {
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

  async function listEvents(
    conversationId: string,
    userId: string,
    cellId: string,
    executionId: string,
    afterSequence = 0,
  ) {
    const execution = await NoteCellExecution.findOne({
      _id: executionId,
      cellId,
      conversationId,
      user: userId,
    })
      .select('_id')
      .lean();
    if (!execution) {
      throw new NoteError('Execution not found', 404);
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

  return { appendEvent, listEvents };
}
