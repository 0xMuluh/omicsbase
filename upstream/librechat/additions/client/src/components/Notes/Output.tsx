import { cn } from '~/utils';
import { statusTone, statusIcon, statusLabel } from './status';
import { Terminal, Table as TableIcon, Image as ImageIcon, Download, XCircle } from 'lucide-react';
import { TooltipAnchor } from '@librechat/client';
import type { useRCell } from './useRCell';

type OutputProps = Pick<
  ReturnType<typeof useRCell>,
  'execution' | 'stdout' | 'executionError' | 'authPlotUrls' | 'tables' | 'execStatus'
> & { isParentRunning?: boolean };

export function Output({
  execStatus,
  isParentRunning,
  execution,
  stdout,
  executionError,
  authPlotUrls,
  tables,
}: OutputProps) {
  return (
    <>
      {(execution || stdout || executionError || authPlotUrls.length > 0 || tables.length > 0) && (
        <div className="border-t border-border-light bg-surface-secondary/30 p-3 dark:border-border-heavy dark:bg-surface-secondary/10">
          <div className="mb-2 flex items-center justify-between text-[11px] font-medium text-text-secondary">
            <span className="flex items-center gap-1.5">
              <Terminal className="size-3.5" />
              Execution Output
            </span>
            <span
              className={cn(
                'inline-flex items-center gap-1 font-mono text-xs',
                statusTone(execStatus),
              )}
            >
              {statusIcon(execStatus)}
              {statusLabel(execStatus) || (isParentRunning ? 'Running' : 'Ready')}
            </span>
          </div>

          {executionError && (
            <div className="mb-2.5 flex items-start gap-2.5 rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-700 dark:text-rose-300">
              <XCircle className="mt-0.5 size-4 shrink-0 text-rose-500" />
              <div className="min-w-0 flex-1 overflow-x-auto">
                <div className="mb-1 font-semibold text-rose-600 dark:text-rose-400">
                  Execution Error
                </div>
                <pre className="whitespace-pre-wrap font-mono text-xs text-rose-800 dark:text-rose-200">
                  {executionError.trim()}
                </pre>
              </div>
            </div>
          )}

          {stdout && (
            <div className="mb-2.5 overflow-x-auto rounded-lg bg-black/90 p-3 font-mono text-xs text-emerald-400">
              <pre className="whitespace-pre-wrap">{String(stdout).trim()}</pre>
            </div>
          )}

          {tables.length > 0 && (
            <div className="mb-2.5 space-y-2">
              {tables.map((tbl, i) => (
                <div
                  key={i}
                  className="rounded-lg border border-border-light bg-surface-primary p-3 dark:border-border-heavy"
                >
                  <div className="mb-1.5 flex items-center justify-between text-xs font-semibold text-text-primary">
                    <div className="flex items-center gap-2">
                      <TableIcon className="size-3.5 text-accent-primary" />
                      <span>
                        Table Preview ({tbl.rows} rows × {tbl.cols} cols)
                      </span>
                    </div>
                    {tbl.url && (
                      <TooltipAnchor
                        description={`Download ${tbl.file}`}
                        side="top"
                        render={
                          <a
                            href={tbl.url}
                            download={tbl.file}
                            className="inline-flex items-center gap-1 rounded p-1 text-xs text-text-secondary transition-colors hover:bg-surface-hover hover:text-text-primary"
                            aria-label={`Download ${tbl.file}`}
                          >
                            <Download className="size-3.5" />
                          </a>
                        }
                      />
                    )}
                  </div>
                  <div className="prose prose-sm dark:prose-invert max-w-none overflow-x-auto text-xs">
                    <pre className="font-mono text-xs">{tbl.markdown}</pre>
                  </div>
                </div>
              ))}
            </div>
          )}

          {authPlotUrls.length > 0 && (
            <div className="space-y-3">
              {authPlotUrls.map((plotUrl, idx) => (
                <div
                  key={idx}
                  className="group relative overflow-hidden rounded-xl border border-border-light bg-white p-2 shadow-sm transition-all dark:border-border-heavy dark:bg-black"
                >
                  <div className="mb-1.5 flex items-center justify-between px-1 text-xs text-text-secondary">
                    <span className="flex items-center gap-1.5 font-medium">
                      <ImageIcon className="size-3.5 text-indigo-500" />
                      Plot #{idx + 1}
                    </span>
                    <TooltipAnchor
                      description="Download plot"
                      side="top"
                      render={
                        <button
                          type="button"
                          onClick={() => {
                            const link = document.createElement('a');
                            link.href = plotUrl;
                            link.download = `plot_${idx + 1}.png`;
                            document.body.appendChild(link);
                            link.click();
                            document.body.removeChild(link);
                          }}
                          className="inline-flex items-center gap-1 rounded p-1 text-text-secondary transition-colors hover:bg-surface-hover hover:text-text-primary"
                          aria-label="Download plot"
                        >
                          <Download className="size-3.5" />
                        </button>
                      }
                    />
                  </div>
                  <img
                    src={plotUrl}
                    alt={`Plot ${idx + 1}`}
                    className="max-h-[500px] w-full rounded-lg object-contain"
                    loading="lazy"
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  );
}
