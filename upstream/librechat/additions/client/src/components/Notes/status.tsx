import { Clock, Loader2, CheckCircle2, XCircle, AlertTriangle, Square } from 'lucide-react';
import type { NoteCellExecutionStatus } from '~/data-provider/Notes/notesCellsApi';

export function statusLabel(status?: NoteCellExecutionStatus | null): string {
  switch (status) {
    case 'queued':
      return 'Queued';
    case 'running':
      return 'Running';
    case 'completed':
      return 'Completed';
    case 'failed':
      return 'Failed';
    case 'timed_out':
      return 'Timed out';
    case 'cancelled':
      return 'Cancelled';
    default:
      return '';
  }
}

export function statusTone(status?: NoteCellExecutionStatus | null): string {
  switch (status) {
    case 'queued':
      return 'text-amber-600 dark:text-amber-400';
    case 'running':
      return 'text-sky-600 dark:text-sky-400';
    case 'completed':
      return 'text-emerald-600 dark:text-emerald-400';
    case 'failed':
      return 'text-rose-600 dark:text-rose-400';
    case 'timed_out':
      return 'text-orange-600 dark:text-orange-400';
    case 'cancelled':
      return 'text-slate-500';
    default:
      return 'text-text-tertiary';
  }
}

export function statusIcon(status?: NoteCellExecutionStatus | null) {
  switch (status) {
    case 'queued':
      return <Clock className="size-3 text-amber-500" />;
    case 'running':
      return <Loader2 className="size-3 animate-spin text-sky-500" />;
    case 'completed':
      return <CheckCircle2 className="size-3 text-emerald-500" />;
    case 'failed':
      return <XCircle className="size-3 text-rose-500" />;
    case 'timed_out':
      return <AlertTriangle className="size-3 text-orange-500" />;
    case 'cancelled':
      return <Square className="size-3 text-slate-500" />;
    default:
      return null;
  }
}
