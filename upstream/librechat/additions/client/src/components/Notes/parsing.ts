import type { NoteCellExecutionStatus } from '~/data-provider/Notes/notesCellsApi';

export function parseMarkdownOutput(md: string) {
  const plots: string[] = [];
  const plotRegex = /!\[.*?\]\((http[^\s)]+)\)/g;
  let match;
  while ((match = plotRegex.exec(md)) !== null) {
    if (!plots.includes(match[1])) {
      plots.push(match[1]);
    }
  }
  const cleanedText = md.replace(/!\[.*?\]\(http[^\s)]+\)/g, '').trim();
  return { plots, cleanedText };
}

export function parseCellMeta(md?: string): {
  cellId?: string;
  executionId?: string;
  status?: NoteCellExecutionStatus;
} {
  if (!md) {
    return {};
  }
  const match = /<!--\s*noteCell\s+cellId=([^\s]+)\s+executionId=([^\s]+)/.exec(md);
  if (!match) {
    return {};
  }
  const status = /\sstatus=(completed|failed|timed_out|cancelled)\s*-->/.exec(md)?.[1] as
    | NoteCellExecutionStatus
    | undefined;
  return { cellId: match[1], executionId: match[2], status };
}
