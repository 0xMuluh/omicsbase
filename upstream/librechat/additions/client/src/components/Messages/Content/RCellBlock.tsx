import React, { useState, useCallback, useEffect, useRef, useMemo } from 'react';
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
import axios from 'axios';
import { useToastContext, TooltipAnchor } from '@librechat/client';
import { useMessageContext } from '~/Providers';
import { cn } from '~/utils';
import useLazyHighlight from '~/components/Chat/Messages/Content/Parts/useLazyHighlight';
import {
  noteCellsApi,
  TERMINAL_STATUSES,
  type NoteArtifact,
  type NoteCellExecution,
  type NoteCellExecutionStatus,
} from '~/data-provider/Notes/notesCellsApi';

interface TablePreview {
  file: string;
  rows: number;
  cols: number;
  markdown: string;
  url?: string;
}

interface RCellBlockProps {
  initialCode: string;
  initialOutput?: string;
  initialPlots?: string[];
  initialTables?: TablePreview[];
  isAgentGenerated?: boolean;
  isParentRunning?: boolean;
  cellId?: string;
  executionId?: string;
  blockKey?: string;
}

function parseMarkdownOutput(md: string) {
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

function parseCellMeta(md?: string): {
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

function statusLabel(status?: NoteCellExecutionStatus | null): string {
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

function statusTone(status?: NoteCellExecutionStatus | null): string {
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

function statusIcon(status?: NoteCellExecutionStatus | null) {
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

export default function RCellBlock({
  initialCode,
  initialOutput,
  initialPlots = [],
  initialTables = [],
  isAgentGenerated = false,
  isParentRunning = false,
  cellId: cellIdProp,
  executionId: executionIdProp,
  blockKey: blockKeyProp,
}: RCellBlockProps) {
  const { conversationId, messageId } = useMessageContext();
  const { showToast } = useToastContext();

  const metaFromOutput = useMemo(() => parseCellMeta(initialOutput), [initialOutput]);
  // Never derive blockKey from code content — streaming code would create new cells every chunk.
  const blockKey =
    blockKeyProp ||
    (messageId ? `msg-${messageId}-${cellIdProp || metaFromOutput.cellId || 'r'}` : null) ||
    (cellIdProp || metaFromOutput.cellId
      ? `cell-${cellIdProp || metaFromOutput.cellId}`
      : 'r-pending');

  const [code, setCode] = useState(initialCode ? initialCode.trim() : '');
  const [savedCode, setSavedCode] = useState(initialCode ? initialCode.trim() : '');
  const [isEditing, setIsEditing] = useState(false);
  const [isCopied, setIsCopied] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isBootstrapping, setIsBootstrapping] = useState(true);
  const [cellId, setCellId] = useState<string | null>(cellIdProp || metaFromOutput.cellId || null);
  const [execution, setExecution] = useState<NoteCellExecution | null>(null);
  const [history, setHistory] = useState<NoteCellExecution[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [authPlotUrls, setAuthPlotUrls] = useState<string[]>([]);
  const [tables, setTables] = useState<TablePreview[]>(initialTables);
  const [legacyStdout, setLegacyStdout] = useState<string | null>(null);

  const wasLive = useRef(isParentRunning);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const blobUrlsRef = useRef<string[]>([]);
  const [userEdited, setUserEdited] = useState(false);

  const isDirty = userEdited && code !== savedCode;
  const execStatus = execution?.status ?? null;
  const isActive = !!execStatus && !TERMINAL_STATUSES.has(execStatus);

  const clearPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const revokeBlobs = useCallback(() => {
    for (const url of blobUrlsRef.current) {
      URL.revokeObjectURL(url);
    }
    blobUrlsRef.current = [];
  }, []);

  const loadArtifacts = useCallback(
    async (exec: NoteCellExecution) => {
      revokeBlobs();
      const arts = exec.artifacts || [];
      const plotArts = arts.filter((a) => a.artifactType === 'plot' && a.contentUrl);
      const tableArts = arts.filter((a) => a.artifactType === 'table');

      const plotUrls: string[] = [];
      for (const art of plotArts) {
        try {
          const res = await axios.get(art.contentUrl!, { responseType: 'blob' });
          const url = URL.createObjectURL(res.data);
          blobUrlsRef.current.push(url);
          plotUrls.push(url);
        } catch {
          /* skip */
        }
      }
      setAuthPlotUrls(plotUrls);

      setTables(
        tableArts.map((a: NoteArtifact, i) => ({
          file: a.relativePath.split('/').pop() || `table_${i}.csv`,
          rows: a.rows ?? 0,
          cols: a.cols ?? 0,
          markdown: a.previewMarkdown || '',
          url: a.contentUrl || undefined,
        })),
      );

      if (!plotUrls.length && !tableArts.length && exec.markdown) {
        const { plots, cleanedText } = parseMarkdownOutput(exec.markdown);
        setLegacyStdout(cleanedText);
        if (plots.length) {
          setAuthPlotUrls(plots);
        }
      } else {
        setLegacyStdout(null);
      }
    },
    [revokeBlobs],
  );

  const startPolling = useCallback(
    (cId: string, eId: string) => {
      if (!conversationId) {
        return;
      }
      clearPoll();
      pollRef.current = setInterval(async () => {
        try {
          const next = await noteCellsApi.getExecution(conversationId, cId, eId);
          setExecution(next);
          if (TERMINAL_STATUSES.has(next.status)) {
            clearPoll();
            await loadArtifacts(next);
            const hist = await noteCellsApi.listExecutions(conversationId, cId);
            setHistory(hist.executions || []);
          }
        } catch {
          /* keep polling briefly */
        }
      }, 1500);
    },
    [conversationId, clearPoll, loadArtifacts],
  );

  // Bootstrap: bind durable cell
  useEffect(() => {
    let cancelled = false;
    async function boot() {
      wasLive.current ||= isParentRunning;
      if (
        isAgentGenerated &&
        wasLive.current &&
        metaFromOutput.cellId &&
        metaFromOutput.executionId &&
        metaFromOutput.status
      ) {
        const result: NoteCellExecution = {
          id: metaFromOutput.executionId,
          cellId: metaFromOutput.cellId,
          revisionId: '',
          attempt: 1,
          timeoutSeconds: 180,
          status: metaFromOutput.status,
          markdown: initialOutput?.replace(/<!--.*?-->/g, '').trim() || '',
        };
        setCellId(result.cellId);
        setExecution(result);
        setHistory([result]);
        setSavedCode(initialCode.trim());
        setIsBootstrapping(false);
        await loadArtifacts(result);
        if (conversationId && conversationId !== 'new') {
          noteCellsApi
            .listExecutions(conversationId, metaFromOutput.cellId)
            .then((h) => {
              if (!cancelled && h?.executions?.length) {
                setHistory(h.executions);
                const found = h.executions.find((e) => e.id === metaFromOutput.executionId);
                if (found) {
                  setExecution(found);
                }
              }
            })
            .catch(() => {});
        }
        return;
      }
      if (isAgentGenerated && isParentRunning) {
        return;
      }
      if (!conversationId || conversationId === 'new') {
        setIsBootstrapping(false);
        return;
      }
      try {
        const cell = await noteCellsApi.createOrFindCell(conversationId, {
          content: code,
          messageId: messageId || null,
          blockKey,
          cellId: cellIdProp || metaFromOutput.cellId || cellId || undefined,
        });
        if (cancelled) {
          return;
        }
        setCellId(cell.id);
        const revContent = cell.revision?.content ?? code;
        // Agent cells already have a saved revision from execute_r_cell — don't look "Unsaved".
        const display = (initialCode || revContent || '').trim();
        const canonical = (revContent || display).trim();
        setCode(isAgentGenerated ? display || canonical : canonical);
        setSavedCode(canonical || display);
        setUserEdited(false);

        let exec = cell.latestExecution || null;
        const preferredExecId = executionIdProp || metaFromOutput.executionId;
        if (preferredExecId && cell.id) {
          try {
            exec = await noteCellsApi.getExecution(conversationId, cell.id, preferredExecId);
          } catch {
            /* use latest */
          }
        }
        if (exec) {
          setExecution(exec);
          if (!TERMINAL_STATUSES.has(exec.status)) {
            startPolling(cell.id, exec.id);
          } else {
            await loadArtifacts(exec);
          }
        } else if (initialOutput) {
          const { plots, cleanedText } = parseMarkdownOutput(initialOutput);
          setLegacyStdout(cleanedText);
          setAuthPlotUrls(Array.from(new Set([...initialPlots, ...plots])));
        }

        const hist = await noteCellsApi.listExecutions(conversationId, cell.id);
        if (!cancelled) {
          setHistory(hist.executions || []);
        }
      } catch (err: any) {
        if (!cancelled) {
          showToast({
            message: err?.message || 'Failed to bind note cell',
            status: 'warning',
          });
        }
      } finally {
        if (!cancelled) {
          setIsBootstrapping(false);
        }
      }
    }
    boot();
    return () => {
      cancelled = true;
      clearPoll();
      revokeBlobs();
    };
    // intentionally once per conversation/message/block
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, messageId, blockKey, isParentRunning, metaFromOutput.executionId]);

  useEffect(() => {
    if (!initialCode || !initialCode.trim() || isEditing || userEdited) {
      return;
    }
    const next = initialCode.trim();
    setCode(next);
    // Keep draft and saved in sync for streamed/agent updates unless the user edited.
    setSavedCode(next);
  }, [initialCode, isEditing, userEdited]);

  const handleCodeChange = useCallback((value: string) => {
    setUserEdited(true);
    setCode(value);
  }, []);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code);
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000);
    } catch {
      showToast({ message: 'Failed to copy code', status: 'error' });
    }
  }, [code, showToast]);

  const handleSave = useCallback(async () => {
    if (!conversationId || !cellId || isSaving) {
      return;
    }
    setIsSaving(true);
    try {
      await noteCellsApi.appendRevision(conversationId, cellId, code);
      setSavedCode(code);
      setUserEdited(false);
      showToast({ message: 'Cell saved', status: 'success' });
    } catch (err: any) {
      showToast({ message: err?.message || 'Save failed', status: 'error' });
    } finally {
      setIsSaving(false);
    }
  }, [conversationId, cellId, code, isSaving, showToast]);

  const handleRun = useCallback(async () => {
    if (!conversationId || !cellId || isDirty || isActive) {
      return;
    }
    try {
      const exec = await noteCellsApi.execute(conversationId, cellId);
      setExecution(exec);
      setAuthPlotUrls([]);
      setTables([]);
      setLegacyStdout(null);
      startPolling(cellId, exec.id);
    } catch (err: any) {
      showToast({ message: err?.message || 'Execute failed', status: 'error' });
    }
  }, [conversationId, cellId, isDirty, isActive, startPolling, showToast]);

  const handleCancel = useCallback(async () => {
    if (!conversationId || !cellId || !execution?.id) {
      return;
    }
    try {
      const next = await noteCellsApi.cancel(conversationId, cellId, execution.id);
      setExecution(next);
    } catch (err: any) {
      showToast({ message: err?.message || 'Cancel failed', status: 'error' });
    }
  }, [conversationId, cellId, execution?.id, showToast]);

  const executionError =
    execution?.error ||
    (execStatus === 'failed' && !execution?.stdout && !legacyStdout
      ? 'Execution failed without error details'
      : null);

  const stdout = execution?.stdout || legacyStdout;

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
            description={!isDirty ? (isSaving ? 'Saving...' : 'No changes to save') : 'Save new revision'}
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
                  onClick={async () => {
                    if (!conversationId || !cellId) {
                      return;
                    }
                    const full = await noteCellsApi.getExecution(conversationId, cellId, h.id);
                    setExecution(full);
                    await loadArtifacts(full);
                    setShowHistory(false);
                  }}
                >
                  <span className={cn('inline-flex items-center gap-1.5', statusTone(h.status))}>
                    {statusIcon(h.status)}
                    #{h.attempt} {statusLabel(h.status)}
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

      {(execution || stdout || executionError || authPlotUrls.length > 0 || tables.length > 0) && (
        <div className="border-t border-border-light bg-surface-secondary/30 p-3 dark:border-border-heavy dark:bg-surface-secondary/10">
          <div className="mb-2 flex items-center justify-between text-[11px] font-medium text-text-secondary">
            <span className="flex items-center gap-1.5">
              <Terminal className="size-3.5" />
              Execution Output
            </span>
            <span className={cn('inline-flex items-center gap-1 font-mono text-xs', statusTone(execStatus))}>
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
    </div>
  );
}
