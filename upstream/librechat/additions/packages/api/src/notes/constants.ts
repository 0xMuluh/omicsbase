import type { Status } from './contracts';
export const TERMINAL: Set<Status> = new Set<Status>([
  'completed',
  'failed',
  'timed_out',
  'cancelled',
]);
