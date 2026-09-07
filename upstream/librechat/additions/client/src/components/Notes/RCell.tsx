import React, { useMemo } from 'react';
import {
  Play,
  Pencil,
  Eye,
  Copy,
  Check,
  Terminal,
  Table as TableIcon,
  Image as ImageIcon,
  Loader2,
  Save,
  Square,
  History,
  Clock,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Download,
} from 'lucide-react';
import Editor from '@monaco-editor/react';
import { TooltipAnchor } from '@librechat/client';
import { useMessageContext } from '~/Providers';
import { cn } from '~/utils';
import useLazyHighlight from '~/components/Chat/Messages/Content/Parts/useLazyHighlight';
import { useRCell } from './useRCell';
import { statusLabel, statusTone, statusIcon } from './status';
import { Output } from './Output';
import type { RCellBlockProps } from './types';

export default function RCell(props: RCellBlockProps) {
  const { conversationId, messageId } = useMessageContext();
  const { isAgentGenerated, isParentRunning } = props;
  const {
    handleSelectHistory,
    code,
    isEditing,
    setIsEditing,
    isCopied,
    isSaving,
    isBootstrapping,
    execution,
    history,
    showHistory,
    setShowHistory,
    authPlotUrls,
    tables,
    isDirty,
    execStatus,
    isActive,
    cellId,
    handleCodeChange,
    handleCopy,
    handleSave,
    handleRun,
    handleCancel,
    executionError,
    stdout,
  } = useRCell(props, conversationId, messageId);
  const highlighted = useLazyHighlight(code || '# (Preparing R cell...)', 'r');

  const runDisabled =
    !cellId ||
    isDirty ||
    isActive ||
    isBootstrapping ||
    !code.trim() ||
    !conversationId ||
    conversationId === 'new';

  const containerStatusClass = useMemo(() => {
    if (isActive) {
      return 'border-sky-500/50 ring-1 ring-sky-500/30 shadow-md';
    }
    if (execStatus === 'failed') {
      return 'border-rose-500/50 ring-1 ring-rose-500/20 shadow-sm';
    }
    if (execStatus === 'queued') {
      return 'border-amber-500/50 ring-1 ring-amber-500/20 shadow-sm';
    }
    return 'border-border-light dark:border-border-heavy shadow-sm';
  }, [isActive, execStatus]);

  const headerStatusBg = useMemo(() => {
    if (isActive) {
      return 'border-sky-500/20 bg-sky-500/10 dark:bg-sky-950/20';
    }
    if (execStatus === 'failed') {
      return 'border-rose-500/20 bg-rose-500/10 dark:bg-rose-950/20';
    }
    return 'border-border-light bg-surface-secondary/60 dark:border-border-heavy dark:bg-surface-secondary/30';
  }, [isActive, execStatus]);

  return (
    <div
      className={cn(
        'my-3 w-full overflow-hidden rounded-xl border bg-surface-primary transition-all',
        containerStatusClass,
      )}
    >
      <div
        className={cn(
          'flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2 text-xs backdrop-blur-sm',
          headerStatusBg,
        )}
      >
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1 rounded border border-accent-primary/30 bg-accent-primary/10 px-2 py-0.5 font-mono text-[11px] font-semibold text-accent-primary">
            <span className="size-1.5 animate-pulse rounded-full bg-accent-primary" />
            {'<R>'}
          </span>
          {isAgentGenerated && (
            <span className="hidden items-center gap-1 font-medium text-text-tertiary sm:inline-flex">
              OB
            </span>
          )}
          {execution?.attempt && execution.attempt > 1 && (
            <TooltipAnchor
              description={`Revision attempt #${execution.attempt}`}
              side="top"
              render={
                <span className="cursor-default rounded bg-accent-primary/10 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-accent-primary">
                  v{execution.attempt}
                </span>
              }
            />
          )}
          {history.length > 1 && execution && history[0]?.id && execution.id !== history[0].id && (
            <TooltipAnchor
              description="This execution was superseded by a newer run"
              side="top"
              render={
                <span className="cursor-default rounded bg-text-tertiary/15 px-1.5 py-0.5 text-[10px] font-medium text-text-tertiary">
                  Superseded
                </span>
              }
            />
          )}
          {isBootstrapping && (
            <span className="inline-flex items-center gap-1 text-text-tertiary">
              <Loader2 className="size-3 animate-spin" />
              Binding…
            </span>
          )}
          {execStatus && (
            <span
              className={cn('inline-flex items-center gap-1 font-medium', statusTone(execStatus))}
            >
              {statusIcon(execStatus)}
              {statusLabel(execStatus)}
            </span>
          )}
          {isDirty && (
            <TooltipAnchor
              description="Unsaved modifications in cell"
              side="top"
              render={
                <span className="cursor-default rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700 dark:text-amber-300">
                  Unsaved
                </span>
              }
            />
          )}
        </div>

        <div className="flex items-center gap-1">
          <TooltipAnchor
            description={isEditing ? 'View rendered code' : 'Edit code'}
            side="top"
            render={
              <button
                type="button"
                onClick={() => setIsEditing((prev) => !prev)}
                className={cn(
                  'inline-flex size-7 items-center justify-center rounded-md text-text-secondary transition-colors hover:bg-surface-hover hover:text-text-primary',
                  isEditing && 'bg-surface-hover text-text-primary',
                )}
                aria-label={isEditing ? 'Preview code' : 'Edit code'}
              >
                {isEditing ? <Eye className="size-4" /> : <Pencil className="size-4" />}
              </button>
            }
          />

          <TooltipAnchor
            description={isCopied ? 'Copied!' : 'Copy R code'}
            side="top"
            render={
              <button
                type="button"
                onClick={handleCopy}
                className="inline-flex size-7 items-center justify-center rounded-md text-text-secondary transition-colors hover:bg-surface-hover hover:text-text-primary"
                aria-label="Copy R code"
              >
                {isCopied ? (
                  <Check className="size-4 text-emerald-500" />
                ) : (
                  <Copy className="size-4" />
                )}
              </button>
            }
          />

          <TooltipAnchor
            description={
              !isDirty ? (isSaving ? 'Saving...' : 'No changes to save') : 'Save new revision'
            }
            side="top"
            render={
              <button
                type="button"
                onClick={handleSave}
                disabled={!isDirty || isSaving || !cellId}
                className={cn(
                  'inline-flex size-7 items-center justify-center rounded-md transition-colors',
                  isDirty
                    ? 'bg-accent-primary/15 text-accent-primary hover:bg-accent-primary/25 active:scale-95'
                    : 'cursor-not-allowed text-text-tertiary opacity-50',
                )}
                aria-label="Save revision"
              >
                {isSaving ? (
                  <Loader2 className="size-4 animate-spin text-accent-primary" />
                ) : (
                  <Save className="size-4" />
                )}
              </button>
            }
          />

          {history.length > 0 && (
            <TooltipAnchor
              description="Execution history"
              side="top"
              render={
                <button
                  type="button"
                  onClick={() => setShowHistory((v) => !v)}
                  className={cn(
                    'inline-flex size-7 items-center justify-center rounded-md transition-colors hover:bg-surface-hover',
                    showHistory ? 'bg-surface-hover text-text-primary' : 'text-text-secondary',
                  )}
                  aria-label="Execution history"
                >
                  <History className="size-4" />
                </button>
              }
            />
          )}

          {isActive ? (
            <TooltipAnchor
              description="Cancel active execution"
              side="top"
              render={
                <button
                  type="button"
                  onClick={handleCancel}
                  className="inline-flex size-7 items-center justify-center rounded-md bg-slate-600 text-white shadow-sm transition-all hover:bg-slate-700 active:scale-95"
                  aria-label="Cancel execution"
                >
                  <Square className="size-3.5 fill-current" />
                </button>
              }
            />
          ) : (
            <TooltipAnchor
              description={
                runDisabled
                  ? isDirty
                    ? 'Save changes before running'
                    : !cellId
                      ? 'Cell binding in progress'
                      : !code.trim()
                        ? 'Enter code to run'
                        : 'Cannot run'
                  : 'Run code in NoteKernel'
              }
              side="top"
              render={
                <button
                  type="button"
                  onClick={handleRun}
                  disabled={runDisabled}
                  className={cn(
                    'inline-flex size-7 items-center justify-center rounded-md transition-all',
                    runDisabled
                      ? 'cursor-not-allowed bg-emerald-600/30 text-white/50'
                      : 'bg-emerald-600 text-white shadow-sm hover:bg-emerald-700 active:scale-95',
                  )}
                  aria-label="Run code in NoteKernel"
                >
                  <Play className="size-3.5 fill-current" />
                </button>
              }
            />
          )}
        </div>
      </div>

      <div className="bg-transparent p-2">
        {isEditing ? (
          <div className="min-h-[120px] overflow-hidden rounded-lg border border-border-light/40">
            <Editor
              height="200px"
              defaultLanguage="r"
              value={code}
              onChange={(value) => handleCodeChange(value || '')}
              theme="vs-dark"
              beforeMount={(monaco) => {
                monaco.editor.defineTheme('ob-code-dark', {
                  base: 'vs-dark',
                  inherit: true,
                  rules: [
                    { token: 'comment', foreground: '64748b', fontStyle: 'italic' },
                    { token: 'keyword', foreground: '60a5fa' },
                    { token: 'string', foreground: '2dd4bf' },
                    { token: 'number', foreground: 'fbbf24' },
                    { token: 'regexp', foreground: 'f87171' },
                    { token: 'type', foreground: 'f472b6' },
                    { token: 'delimiter', foreground: '94a3b8' },
                  ],
                  colors: {
                    'editor.background': '#0f172a',
                    'editor.foreground': '#f1f5f9',
                    'editorLineNumber.foreground': '#64748b',
                    'editorCursor.foreground': '#2dd4bf',
                    'editor.selectionBackground': '#33415588',
                    'editor.lineHighlightBackground': '#1e293b66',
                  },
                });
              }}
              onMount={(_editor, monaco) => {
                monaco.editor.setTheme('ob-code-dark');
              }}
              options={{
                minimap: { enabled: false },
                lineNumbers: 'on',
                scrollBeyondLastLine: false,
                fontSize: 14,
                lineHeight: 22,
                fontFamily:
                  'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
                fontLigatures: true,
                wordWrap: 'on',
                tabSize: 2,
                automaticLayout: true,
                padding: { top: 8, bottom: 8 },
                renderLineHighlight: 'line',
              }}
            />
            <div className="bg-surface-secondary/40 px-3 py-1 text-[11px] text-text-tertiary">
              Save a revision, then Run. Cancel is available while queued/running.
            </div>
          </div>
        ) : (
          <pre className="r-cell-code code-theme-dark overflow-x-auto whitespace-pre-wrap rounded-lg border border-slate-700/60 bg-slate-900 p-3 font-mono text-sm leading-6 text-slate-100">
            <code className="hljs language-r !whitespace-pre-wrap !bg-transparent !p-0 !text-[14px] !leading-6">
              {highlighted ?? (code || '# (Preparing R cell...)')}
            </code>
          </pre>
        )}
      </div>

      {showHistory && history.length > 0 && (
        <div className="border-t border-border-light bg-surface-secondary/20 px-3 py-2 dark:border-border-heavy">
          <div className="mb-1 text-[11px] font-semibold text-text-secondary">
            Execution history
          </div>
          <ul className="max-h-32 space-y-1 overflow-y-auto text-[11px]">
            {history.map((h) => (
              <li key={h.id}>
                <button
                  type="button"
                  className="flex w-full items-center justify-between rounded px-1.5 py-1 text-left hover:bg-surface-hover"
                  onClick={() => handleSelectHistory(h.id)}
                >
                  <span className={cn('inline-flex items-center gap-1.5', statusTone(h.status))}>
                    {statusIcon(h.status)}#{h.attempt} {statusLabel(h.status)}
                  </span>
                  <span className="font-mono text-text-tertiary">
                    {h.finishedAt || h.startedAt || h.createdAt
                      ? new Date(h.finishedAt || h.startedAt || h.createdAt || '').toLocaleString()
                      : ''}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <Output
        execStatus={execStatus}
        isParentRunning={isParentRunning}
        execution={execution}
        stdout={stdout}
        executionError={executionError}
        authPlotUrls={authPlotUrls}
        tables={tables}
      />
    </div>
  );
}
