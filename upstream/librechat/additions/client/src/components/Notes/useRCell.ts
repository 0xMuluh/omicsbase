import { useState, useCallback, useEffect, useRef, useMemo } from 'react';
import axios from 'axios';
import { useToastContext } from '@librechat/client';
import {
  noteCellsApi,
  TERMINAL_STATUSES,
  type NoteCellExecution,
  type NoteArtifact,
} from '~/data-provider/Notes/notesCellsApi';
import { parseCellMeta, parseMarkdownOutput } from './parsing';
import type { RCellBlockProps, TablePreview } from './types';

export function useRCell(
  {
    initialCode,
    initialOutput,
    initialPlots = [],
    initialTables = [],
    isAgentGenerated = false,
    isParentRunning = false,
    cellId: cellIdProp,
    executionId: executionIdProp,
    blockKey: blockKeyProp,
  }: RCellBlockProps,
  conversationId: string | null | undefined,
  messageId: string | undefined,
) {
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
      } catch (err) {
        if (!cancelled) {
          showToast({
            message: err instanceof Error ? err.message : 'Failed to bind note cell',
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
    } catch (err) {
      showToast({ message: err instanceof Error ? err.message : 'Save failed', status: 'error' });
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
    } catch (err) {
      showToast({
        message: err instanceof Error ? err.message : 'Execute failed',
        status: 'error',
      });
    }
  }, [conversationId, cellId, isDirty, isActive, startPolling, showToast]);

  const handleCancel = useCallback(async () => {
    if (!conversationId || !cellId || !execution?.id) {
      return;
    }
    try {
      const next = await noteCellsApi.cancel(conversationId, cellId, execution.id);
      setExecution(next);
    } catch (err) {
      showToast({ message: err instanceof Error ? err.message : 'Cancel failed', status: 'error' });
    }
  }, [conversationId, cellId, execution?.id, showToast]);

  const executionError =
    execution?.error ||
    (execStatus === 'failed' && !execution?.stdout && !legacyStdout
      ? 'Execution failed without error details'
      : null);

  const stdout = execution?.stdout || legacyStdout;

  const handleSelectHistory = useCallback(
    async (executionId: string) => {
      if (!conversationId || !cellId) return;
      const full = await noteCellsApi.getExecution(conversationId, cellId, executionId);
      setExecution(full);
      await loadArtifacts(full);
      setShowHistory(false);
    },
    [conversationId, cellId, loadArtifacts],
  );

  return {
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
  };
}
