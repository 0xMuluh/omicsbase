"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowUp,
  Check,
  Code2,
  Download,
  Eye,
  FileText,
  Files,
  History,
  Loader2,
  Moon,
  PanelLeftOpen,
  Pencil,
  Plus,
  Play,
  Save,
  Square,
  Sparkles,
  Sun,
  Upload,
} from "lucide-react";

import { api, NoteCell, NoteCellExecution, NoteCellRevision, NoteCellType, NoteExecutionArtifact } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { MarkdownRenderer } from "@/components/MarkdownRenderer";
import { CodeBlock } from "@/components/CodeBlock";
import { ExecutionBlocks } from "@/components/ExecutionBlocks";
import { ExecutionHistory } from "@/components/ExecutionHistory";
import { ExecutionOutput } from "@/components/ExecutionOutput";
import { NoteExecutionArtifacts } from "@/components/NoteExecutionArtifacts";
import { ThreadOverviewRail } from "@/components/ThreadOverviewRail";
import { ProjectsSidebarContent } from "@/components/ProjectsSidebar";
import { useCodeTheme } from "@/lib/use-code-theme";
import { useReuseCache } from "@/lib/use-note-settings";

const editableCellTypes = new Set<NoteCellType>(["markdown", "agent", "code"]);

function latestRevision(cell: NoteCell): NoteCellRevision | null {
  return cell.revisions[cell.revisions.length - 1] || null;
}

function cellTypeLabel(type: NoteCellType) {
  return type === "markdown"
    ? "Markdown"
    : type === "agent"
      ? "Question"
      : type === "code"
        ? "Code"
        : type === "output"
          ? "Output"
          : "Provenance";
}

function cellTypeIcon(type: NoteCellType) {
  if (type === "code") return <Code2 className="h-3.5 w-3.5" />;
  if (type === "agent") return <Sparkles className="h-3.5 w-3.5" />;
  if (type === "output") return <Check className="h-3.5 w-3.5" />;
  return <FileText className="h-3.5 w-3.5" />;
}

function ComposerAddButton({
  open,
  onToggle,
  disabled,
  onAddData,
  onAddNote,
  onAddCode,
  onExport,
  exportPending,
  exported,
  showExport,
}: {
  open: boolean;
  onToggle: () => void;
  disabled?: boolean;
  onAddData: () => void;
  onAddNote: () => void;
  onAddCode: () => void;
  onExport: () => void;
  exportPending: boolean;
  exported: boolean;
  showExport: boolean;
}) {
  return (
    <div className="relative shrink-0">
      <Button
        type="button"
        size="sm"
        variant="ghost"
        onClick={onToggle}
        disabled={disabled}
        className="h-10 w-10 rounded-full border border-border bg-muted/40 p-0 text-muted-foreground hover:bg-muted hover:text-foreground"
        title="Add to this note"
        aria-label="Add to this note"
      >
        <Plus className="h-4 w-4" />
      </Button>
      {open ? (
        <div className="absolute bottom-11 left-0 z-20 min-w-48 rounded-xl border border-border bg-popover p-1.5 text-sm shadow-xl">
          <button type="button" className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-foreground hover:bg-muted" onClick={onAddData}>
            <Upload className="h-4 w-4 text-muted-foreground" /> Add data or plan
          </button>
          <button type="button" className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-foreground hover:bg-muted" onClick={onAddNote}>
            <FileText className="h-4 w-4 text-muted-foreground" /> Add note
          </button>
          <button type="button" className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-foreground hover:bg-muted" onClick={onAddCode}>
            <Code2 className="h-4 w-4 text-muted-foreground" /> Add R code
          </button>
          {showExport ? (
            <button
              type="button"
              className="flex w-full items-center gap-2 rounded-lg border-t border-border/40 px-2.5 py-2 text-left text-foreground hover:bg-muted mt-1 pt-2"
              onClick={onExport}
              disabled={exportPending}
            >
              {exportPending ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : <FileText className="h-4 w-4 text-muted-foreground" />}
              {exported ? "Exported QMD" : "Export draft QMD"}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function executionContainerClass(execution: NoteCellExecution | null | undefined): string {
  if (!execution) return "overflow-hidden rounded-xl border border-teal-500 bg-teal-500/25 shadow-sm";
  switch (execution.status) {
    case "queued":
      return "overflow-hidden rounded-xl border border-amber-500 bg-amber-500/25 shadow-sm";
    case "running":
      return "overflow-hidden rounded-xl border border-sky-500 bg-sky-500/25 shadow-sm";
    case "failed":
      return "overflow-hidden rounded-xl border border-rose-500 bg-rose-500/25 shadow-sm";
    case "completed_with_errors":
      return "overflow-hidden rounded-xl border border-orange-500 bg-orange-500/25 shadow-sm";
    case "timed_out":
      return "overflow-hidden rounded-xl border border-orange-500 bg-orange-500/25 shadow-sm";
    case "cancelled":
    case "cancel_requested":
      return "overflow-hidden rounded-xl border border-slate-400 bg-slate-400/20 shadow-sm";
    default:
      return "overflow-hidden rounded-xl border border-teal-500 bg-teal-500/25 shadow-sm";
  }
}

export function NotesSurface({ workspaceId, initialThreadId }: { workspaceId?: string; initialThreadId?: string | null }) {
  const scopeId = workspaceId || "standalone";
  const queryClient = useQueryClient();
  const router = useRouter();
  const pathname = usePathname();
  const [codeTheme, setCodeTheme] = useCodeTheme();
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [activeExecution, setActiveExecution] = useState<{ cellId: string; executionId: string } | null>(null);
  const [reuseCache] = useReuseCache();
  const [editingCellId, setEditingCellId] = useState<string | null>(null);
  const [turnDraft, setTurnDraft] = useState("");
  const [emptyPrompt, setEmptyPrompt] = useState("");
  const [turnStreaming, setTurnStreaming] = useState(false);
  const [turnStatus, setTurnStatus] = useState<string | null>(null);
  const [liveTurnText, setLiveTurnText] = useState("");
  const [turnError, setTurnError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const threadBottomRef = useRef<HTMLDivElement>(null);
  const threadScrollRef = useRef<HTMLDivElement>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState<number | null>(null);
  const [isResizingSidebar, setIsResizingSidebar] = useState(false);
  const [composerMenuOpen, setComposerMenuOpen] = useState(false);
  const [resultActions, setResultActions] = useState<Record<string, { history?: boolean; files?: boolean }>>({});
  const [downloadingResult, setDownloadingResult] = useState<string | null>(null);

  const toggleResultAction = (executionId: string, key: "history" | "files") => {
    setResultActions((prev) => {
      const current = prev[executionId] || {};
      return { ...prev, [executionId]: { ...current, [key]: !current[key] } };
    });
  };

  const listScopedThreads = () =>
    workspaceId ? api.listNoteThreads(workspaceId) : api.listStandaloneNoteThreads();
  const getScopedThread = (threadId: string) =>
    workspaceId ? api.getNoteThread(workspaceId, threadId) : api.getStandaloneNoteThread(threadId);
  const createScopedThread = (data: { title?: string; thread_type?: string }) =>
    workspaceId ? api.createNoteThread(workspaceId, data) : api.createStandaloneNoteThread(data);
  const createScopedCell = (threadId: string, data: { cell_type: NoteCellType; language?: string | null; content?: string; position?: number | null; metadata?: Record<string, any> | null }) =>
    workspaceId ? api.createNoteCell(workspaceId, threadId, data) : api.createStandaloneNoteCell(threadId, data);
  const appendScopedRevision = (threadId: string, cellId: string, data: { cell_type: NoteCellType; language?: string | null; content?: string; metadata?: Record<string, any> | null }) =>
    workspaceId ? api.appendNoteCellRevision(workspaceId, threadId, cellId, data) : api.appendStandaloneNoteCellRevision(threadId, cellId, data);
  const executeScopedCell = (threadId: string, cellId: string, data: { revision?: number; parameters?: Record<string, any>; timeout_seconds?: number; cache_policy?: "off" | "reuse"; upstream_execution_ids?: string[] }) =>
    workspaceId ? api.executeNoteCell(workspaceId, threadId, cellId, data) : api.executeStandaloneNoteCell(threadId, cellId, data);
  const getScopedExecution = (threadId: string, cellId: string, executionId: string) =>
    workspaceId ? api.getNoteCellExecution(workspaceId, threadId, cellId, executionId) : api.getStandaloneNoteCellExecution(threadId, cellId, executionId);
  const cancelScopedExecution = (threadId: string, cellId: string, executionId: string) =>
    workspaceId ? api.cancelNoteCellExecution(workspaceId, threadId, cellId, executionId) : api.cancelStandaloneNoteCellExecution(threadId, cellId, executionId);

  const executionQuery = useQuery<NoteCellExecution>({
    queryKey: ["note-cell-execution", scopeId, selectedThreadId, activeExecution ? activeExecution.cellId : null, activeExecution ? activeExecution.executionId : null],
    queryFn: () => getScopedExecution(selectedThreadId as string, activeExecution ? activeExecution.cellId : "", activeExecution ? activeExecution.executionId : ""),
    enabled: Boolean(scopeId && selectedThreadId && activeExecution),
    refetchInterval: (query) => {
      const status = query.state.data ? query.state.data.status : null;
      return status && ["completed", "completed_with_errors", "failed", "timed_out", "cancelled"].includes(status) ? false : 1500;
    },
  });

  useEffect(() => {
    const data = executionQuery.data;
    if (data && ["completed", "completed_with_errors", "failed", "timed_out", "cancelled"].includes(data.status)) {
      queryClient.invalidateQueries({ queryKey: ["note-thread", scopeId, selectedThreadId] });
    }
  }, [executionQuery.data, queryClient, scopeId, selectedThreadId]);

  const threadsQuery = useQuery({
    queryKey: ["note-threads", scopeId],
    queryFn: () => listScopedThreads(),
    enabled: Boolean(scopeId),
  });
  const threadQuery = useQuery({
    queryKey: ["note-thread", scopeId, selectedThreadId],
    queryFn: () => getScopedThread(selectedThreadId as string),
    enabled: Boolean(scopeId && selectedThreadId),
  });

  const threads = threadsQuery.data || [];
  const currentThread = threadQuery.data;

  useEffect(() => {
    setActiveExecution(null);
  }, [selectedThreadId]);

  useEffect(() => {
    if (activeExecution || !currentThread) return;
    const candidates: { cellId: string; executionId: string; createdAt: string }[] = [];
    currentThread.cells.forEach((cell) => {
      const revision = latestRevision(cell);
      const execution = cell.latest_execution && revision && cell.latest_execution.revision_id === revision.id ? cell.latest_execution : null;
      if (execution && ["queued", "running", "cancel_requested"].includes(execution.status)) {
        candidates.push({ cellId: cell.id, executionId: execution.id, createdAt: execution.created_at });
      }
    });
    if (!candidates.length) return;
    const next = candidates.reduce((a, b) => (a.createdAt >= b.createdAt ? a : b));
    // eslint-disable-next-line react-hooks/set-state-in-effect -- resume polling for a persisted execution after reload
    setActiveExecution({ cellId: next.cellId, executionId: next.executionId });
  }, [activeExecution, currentThread]);

  useEffect(() => {
    if (!threads.length) {
      setSelectedThreadId(null);
      return;
    }
    if (initialThreadId) {
      if (threads.some((item) => item.id === initialThreadId)) {
        if (initialThreadId !== selectedThreadId) setSelectedThreadId(initialThreadId);
        return;
      }
      // The requested thread is not in the list yet (still loading or gone);
      // keep the current selection and let the refetch settle it.
    }
    if (!selectedThreadId || !threads.some((item) => item.id === selectedThreadId)) {
      setSelectedThreadId(threads[0].id);
    }
  }, [threads, selectedThreadId, initialThreadId]);

  useEffect(() => {
    // Only write the URL when it does not already name a thread — never fight
    // a sidebar navigation (that caused an A->B->A oscillation).
    if (selectedThreadId && !initialThreadId) {
      router.replace(`${pathname}?thread=${selectedThreadId}`, { scroll: false });
    }
  }, [selectedThreadId, initialThreadId, pathname, router]);

  useEffect(() => {
    if (!currentThread) return;
    const next: Record<string, string> = {};
    currentThread.cells.forEach((cell) => {
      const revision = latestRevision(cell);
      if (revision) next[cell.id] = revision.content;
    });
    setDrafts(next);
  }, [currentThread?.id, currentThread?.updated_at]);

  const executeCell = useMutation({
    mutationFn: (cell: NoteCell) => {
      const revision = latestRevision(cell);
      if (!revision) throw new Error("Cannot execute a cell without an existing revision.");
      return executeScopedCell(selectedThreadId as string, cell.id, { revision: revision.revision, cache_policy: reuseCache ? "reuse" : "off" });
    },
    onSuccess: (execution, cell) => {
      setActiveExecution({ cellId: cell.id, executionId: execution.id });
    },
  });

  const cancelExecution = useMutation({
    mutationFn: () => {
      if (!activeExecution || !selectedThreadId) throw new Error("No active execution to cancel.");
      return cancelScopedExecution(selectedThreadId, activeExecution.cellId, activeExecution.executionId);
    },
    onSuccess: () => {
      void executionQuery.refetch();
    },
  });

  const createThread = useMutation({
    mutationFn: (title: string) => createScopedThread({ title: title || "Untitled note" }),
    onSuccess: (thread) => {
      setSelectedThreadId(thread.id);
      // Land the new note on its own URL so reloads/shares point at it.
      router.replace(`${pathname}?thread=${thread.id}`, { scroll: false });
      queryClient.invalidateQueries({ queryKey: ["note-threads", scopeId] });
    },
  });
  const promoteThread = useMutation({
    mutationFn: () => {
      if (workspaceId || !selectedThreadId) throw new Error("Only a standalone thread can be promoted.");
      return api.createWorkspaceFromStandaloneNoteThread(selectedThreadId, { auto_build: true });
    },
    onSuccess: (result) => {
      window.location.assign("/projects/" + result.project_id + "/workspace");
    },
  });
  const exportReport = useMutation({
    mutationFn: () => {
      if (!workspaceId || !selectedThreadId) throw new Error("Only an attached NoteThread can be exported.");
      return api.exportNoteThreadReport(workspaceId, selectedThreadId);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["reports", workspaceId] });
    },
  });
  const createCell = useMutation({
    mutationFn: (cellType: NoteCellType) => createScopedCell(selectedThreadId as string, {
      cell_type: cellType,
      language: cellType === "code" ? "r" : null,
      content: "",
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["note-thread", scopeId, selectedThreadId] });
      queryClient.invalidateQueries({ queryKey: ["note-threads", scopeId] });
    },
  });
  const saveRevision = useMutation({
    mutationFn: ({ cell, content }: { cell: NoteCell; content: string }) => {
      const revision = latestRevision(cell);
      if (!revision) throw new Error("Cannot revise a cell without an existing revision.");
      return appendScopedRevision(selectedThreadId as string, cell.id, {
        cell_type: revision.cell_type,
        language: revision.language,
        content,
        metadata: revision.metadata,
      });
    },
    onSuccess: () => {
      setActiveExecution(null);
      setEditingCellId(null);
      queryClient.invalidateQueries({ queryKey: ["note-thread", scopeId, selectedThreadId] });
      queryClient.invalidateQueries({ queryKey: ["note-threads", scopeId] });
    },
  });

  useEffect(() => {
    const bottom = threadBottomRef.current;
    if (!bottom) return;
    bottom.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [
    currentThread?.id,
    currentThread?.updated_at,
    liveTurnText,
    turnStreaming,
    turnStatus,
    executionQuery.data?.status,
  ]);

  const selectedSummary = useMemo(
    () => threads.find((item) => item.id === selectedThreadId) || null,
    [threads, selectedThreadId],
  );
  const requestError =
    turnError ||
    threadsQuery.error?.message ||
    threadQuery.error?.message ||
    createThread.error?.message ||
    promoteThread.error?.message ||
    exportReport.error?.message ||
    createCell.error?.message ||
    saveRevision.error?.message ||
    executeCell.error?.message ||
    cancelExecution.error?.message ||
    executionQuery.error?.message;

  const addCell = (cellType: NoteCellType) => {
    if (selectedThreadId && !createCell.isPending) createCell.mutate(cellType);
  };
  const saveCell = (cell: NoteCell) => {
    if (!selectedThreadId || saveRevision.isPending) return;
    saveRevision.mutate({ cell, content: drafts[cell.id] ?? latestRevision(cell)?.content ?? "" });
  };
  const runTurn = async (threadId: string, message: string) => {
    if (!message || !threadId || turnStreaming) return;
    setTurnDraft("");
    setTurnStreaming(true);
    setTurnStatus("Thinking about your question");
    setLiveTurnText("");
    setTurnError(null);
    try {
      await api.streamNoteThreadTurn(
        threadId,
        { message, auto_execute: true },
        (event) => {
          if (event.type === "token") {
            setLiveTurnText((current) => current + (event.token || ""));
          }
          if (event.type === "status" || event.type === "tool_started") {
            setTurnStatus(event.message || event.status || (event.tool === "run_r_cell" ? "Running R cell…" : event.tool ? "Using " + event.tool : "Working"));
          }
          if (event.type === "error") {
            setTurnError(event.message || "This question encountered an error.");
          }
          if (event.type === "final") {
            setLiveTurnText(event.message || "");
            setTurnStatus(null);
          }
          if (event.type === "execution_queued" && event.execution && event.cell) {
            setActiveExecution({ cellId: event.cell.id, executionId: event.execution.id });
          }
          if (["note_cell", "execution_queued", "thread_updated", "final"].includes(event.type)) {
            queryClient.invalidateQueries({ queryKey: ["note-thread", scopeId, threadId] });
            queryClient.invalidateQueries({ queryKey: ["note-threads", scopeId] });
          }
        },
      );
    } catch (error) {
      setTurnError(error instanceof Error ? error.message : "This question could not be completed.");
    } finally {
      setTurnStreaming(false);
      setTurnStatus(null);
      queryClient.invalidateQueries({ queryKey: ["note-thread", scopeId, threadId] });
      queryClient.invalidateQueries({ queryKey: ["note-threads", scopeId] });
    }
  };

  const submitTurn = () => {
    if (!selectedThreadId || !currentThread || currentThread.status !== "active") return;
    void runTurn(selectedThreadId, turnDraft.trim());
  };

  const handleEmptySubmit = async () => {
    const message = emptyPrompt.trim();
    if (!message || createThread.isPending || turnStreaming) return;
    setEmptyPrompt("");
    // A selected thread with no cells yet: answer inside it instead of
    // creating another note.
    if (selectedThreadId && currentThread && currentThread.cells.length === 0) {
      await runTurn(selectedThreadId, message);
      return;
    }
    try {
      const thread = await createThread.mutateAsync(message.slice(0, 72) || "Untitled note");
      setSelectedThreadId(thread.id);
      await runTurn(thread.id, message);
    } catch (error) {
      setTurnError(error instanceof Error ? error.message : "The note could not be created.");
    }
  };

  return (
    <main className="flex h-screen overflow-hidden bg-background">
      {sidebarOpen ? (
        <aside
          className="relative hidden h-full min-h-0 shrink-0 border-r border-border lg:flex"
          style={{ width: sidebarWidth === null ? "320px" : String(sidebarWidth) + "px" }}
        >
          <ProjectsSidebarContent onClose={() => setSidebarOpen(false)} notesScope={workspaceId || "standalone"} activeThreadId={selectedThreadId} />
          <div
            className="absolute inset-y-0 right-[-3px] z-10 hidden w-1 cursor-col-resize touch-none lg:block"
            onPointerDown={(event) => {
              event.currentTarget.setPointerCapture(event.pointerId);
              setIsResizingSidebar(true);
            }}
            onPointerMove={(event) => {
              if (!isResizingSidebar) return;
              setSidebarWidth(Math.min(520, Math.max(260, event.clientX)));
            }}
            onPointerUp={() => setIsResizingSidebar(false)}
            onPointerCancel={() => setIsResizingSidebar(false)}
            aria-label="Resize recent projects sidebar"
            role="separator"
            aria-orientation="vertical"
          />
        </aside>
      ) : null}

      <section className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
        <header className="flex min-h-11 shrink-0 items-center justify-between gap-3 border-b border-border px-4 md:px-6">
          <div className="flex min-w-0 items-center gap-3">
            {!sidebarOpen ? (
              <Button size="icon" variant="ghost" onClick={() => setSidebarOpen(true)} title="Show recent projects" aria-label="Show recent projects">
                <PanelLeftOpen className="h-4 w-4" />
              </Button>
            ) : null}
            <Link
              href={workspaceId ? "/projects/" + scopeId + "/workspace" : "/"}
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border px-2.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <ArrowLeft className="h-3.5 w-3.5" /> {workspaceId ? "Workspace" : "Home"}
            </Link>
          </div>
          <div className="flex items-center gap-2">
            {!workspaceId && selectedThreadId ? (
              <Button size="sm" variant="outline" onClick={() => promoteThread.mutate()} disabled={promoteThread.isPending}>
                {promoteThread.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />} Promote to workspace
              </Button>
            ) : null}
            {!workspaceId ? (
              <Button size="sm" onClick={() => createThread.mutate("Untitled note")} disabled={!scopeId || createThread.isPending}>
                {createThread.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
                New note
              </Button>
            ) : null}
          </div>
        </header>
          <div ref={threadScrollRef} className="min-h-0 flex-1 overflow-y-auto">
          {!selectedSummary ? (
            <div className="flex min-h-full items-center justify-center p-8">
              <div className="w-full max-w-2xl">
                <div className="mb-8 text-center">
                  <h1 className="font-display text-4xl font-medium tracking-tight text-foreground sm:text-5xl">
                    See beyond the counts.
                  </h1>
                  <p className="mx-auto mt-3 max-w-xl text-sm leading-6 text-muted-foreground">
                    Create downstream omics reports by chatting with AI.
                  </p>
                </div>
                <div className="rounded-[28px] border border-border bg-[var(--composer-surface)] p-1.5 shadow-[0_18px_50px_rgba(15,23,42,0.08)] backdrop-blur transition-colors dark:shadow-[0_30px_80px_rgba(0,0,0,0.35)]">
                  <div className="flex items-end gap-1.5">
                    <Textarea
                      value={emptyPrompt}
                      onChange={(event) => setEmptyPrompt(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                          event.preventDefault();
                          void handleEmptySubmit();
                        }
                      }}
                      rows={1}
                      placeholder="Ask OmicsBase..."
                      className="max-h-52 min-h-[40px] min-w-0 flex-1 resize-none border-0 bg-transparent px-2.5 py-1.5 text-[17px] leading-6 text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-0"
                    />
                    <Button type="button" size="icon" className="h-10 w-10 shrink-0 rounded-full" onClick={() => void handleEmptySubmit()} disabled={!emptyPrompt.trim() || createThread.isPending || turnStreaming} title="Send" aria-label="Send">
                      {createThread.isPending || turnStreaming ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ArrowUp className="h-4 w-4" />}
                    </Button>
                  </div>
                </div>
              </div>
            </div>
          ) : threadQuery.isLoading || !currentThread ? (
            <div className="flex min-h-full items-center justify-center"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
          ) : currentThread.cells.length === 0 ? (
            <div className="flex min-h-full items-center justify-center p-8">
              <div className="w-full max-w-2xl">
                <div className="mb-8 text-center">
                  <h1 className="font-display text-4xl font-medium tracking-tight text-foreground sm:text-5xl">
                    See beyond the counts.
                  </h1>
                  <p className="mx-auto mt-3 max-w-xl text-sm leading-6 text-muted-foreground">
                    Create downstream omics reports by chatting with AI.
                  </p>
                </div>
                <div className="rounded-[28px] border border-border bg-[var(--composer-surface)] p-1.5 shadow-[0_18px_50px_rgba(15,23,42,0.08)] backdrop-blur transition-colors dark:shadow-[0_30px_80px_rgba(0,0,0,0.35)]">
                  <div className="flex items-end gap-1.5">
                    <ComposerAddButton
                      open={composerMenuOpen}
                      onToggle={() => setComposerMenuOpen((value) => !value)}
                      disabled={currentThread.status !== "active" || createCell.isPending}
                        onAddData={() => {
                        setComposerMenuOpen(false);
                        fileInputRef.current?.click();
                      }}
                      onAddNote={() => {
                        setComposerMenuOpen(false);
                        addCell("markdown");
                      }}
                      onAddCode={() => {
                        setComposerMenuOpen(false);
                        addCell("code");
                      }}
                      onExport={() => {
                        setComposerMenuOpen(false);
                        exportReport.mutate();
                      }}
                      exportPending={exportReport.isPending}
                      exported={Boolean(exportReport.data)}
                      showExport={Boolean(workspaceId)}
                    />
                    <Textarea
                      value={emptyPrompt}
                      onChange={(event) => setEmptyPrompt(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                          event.preventDefault();
                          void handleEmptySubmit();
                        }
                      }}
                      rows={1}
                      placeholder="Ask OmicsBase..."
                      className="max-h-52 min-h-[40px] min-w-0 flex-1 resize-none border-0 bg-transparent px-2.5 py-1.5 text-[17px] leading-6 text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-0"
                    />
                    <Button type="button" size="icon" className="h-10 w-10 shrink-0 rounded-full" onClick={() => void handleEmptySubmit()} disabled={!emptyPrompt.trim() || createThread.isPending || turnStreaming} title="Send" aria-label="Send">
                      {createThread.isPending || turnStreaming ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ArrowUp className="h-4 w-4" />}
                    </Button>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <div data-thread-column className="mx-auto w-full max-w-4xl p-4 pb-8 md:p-6">
              {requestError ? <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-700 dark:text-red-300">{requestError}</div> : null}

              <div className="space-y-4">
                {currentThread.cells.map((cell, index) => {
                  const revision = latestRevision(cell);
                  if (!revision) return null;
                  const editable = editableCellTypes.has(revision.cell_type);
                  const isNarrative = revision.cell_type === "markdown" || revision.cell_type === "agent";
                  const isQuestion = revision.cell_type === "agent";
                  const isEditing = editingCellId === cell.id;
                  const draft = drafts[cell.id] ?? revision.content;
                  const changed = draft !== revision.content;
                  const persistedExecution = cell.latest_execution && cell.latest_execution.revision_id === revision.id ? cell.latest_execution : null;
                  const execution = activeExecution && activeExecution.cellId === cell.id ? (executionQuery.data || persistedExecution) : persistedExecution;
                  const executionBusy = Boolean(execution && ["queued", "running", "cancel_requested"].includes(execution.status));
                  const canRun = revision.cell_type === "code" && !changed && currentThread.status === "active";

                  const downloadResultArtifact = async (execution: NoteCellExecution, artifact: NoteExecutionArtifact) => {
                    if (downloadingResult || !selectedThreadId) return;
                    setDownloadingResult(artifact.id);
                    try {
                      const blob = workspaceId
                        ? await api.getNoteExecutionArtifactContent(workspaceId, selectedThreadId, cell.id, execution.id, artifact.id)
                        : await api.getStandaloneNoteExecutionArtifactContent(selectedThreadId, cell.id, execution.id, artifact.id);
                      const url = URL.createObjectURL(blob);
                      const link = document.createElement("a");
                      link.href = url;
                      link.download = artifact.relative_path.split("/").filter(Boolean).pop() || "file";
                      document.body.appendChild(link);
                      link.click();
                      link.remove();
                      setTimeout(() => URL.revokeObjectURL(url), 60_000);
                    } catch {
                      // ignore transient failures
                    } finally {
                      setDownloadingResult(null);
                    }
                  };
                  return (
                    <article
                      key={cell.id}
                      data-overview-block
                      data-overview-type={revision.cell_type === "agent" ? "user" : revision.cell_type === "markdown" ? "assistant" : "code"}
                      data-overview-id={cell.id}
                      className={isQuestion ? "flex justify-end" : isNarrative ? "group relative" : revision.cell_type === "code" ? executionContainerClass(execution) : "overflow-hidden rounded-xl border border-border bg-card shadow-sm"}>
                      {(!isNarrative || isEditing) ? (
                      <div className={isQuestion ? "flex max-w-[78%] flex-wrap items-center justify-between gap-2 rounded-t-[22px] border-b border-border bg-muted/30 px-3 py-2" : "flex flex-wrap items-center justify-between gap-2 border-b border-border bg-muted/30 px-3 py-2"}>
                        <div className="flex items-center gap-2 text-xs">
                          <span className="font-mono text-[11px] text-muted-foreground">#{index + 1}</span>
                          <span className="inline-flex items-center gap-1.5 font-medium text-foreground">{cellTypeIcon(revision.cell_type)}{cellTypeLabel(revision.cell_type)}</span>
                          {revision.language ? <Badge variant="outline" className="font-mono text-[11px]">{revision.language}</Badge> : null}
                        </div>
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          {revision.cell_type === "code" ? (
                            <div className="flex items-center gap-1.5">
                              {executionBusy ? (
                                <Button size="xs" variant="outline" onClick={() => cancelExecution.mutate()} disabled={cancelExecution.isPending}>
                                  {cancelExecution.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Square className="h-3 w-3" />} Cancel
                                </Button>
                              ) : (
                                <Button size="xs" variant="outline" onClick={() => executeCell.mutate(cell)} disabled={!canRun || executeCell.isPending || (activeExecution?.cellId === cell.id && executionQuery.isLoading)} title={changed ? "Save this revision before running" : "Run this persisted revision"}>
                                  {executeCell.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />} Run
                                </Button>
                              )}
                            </div>
                          ) : null}
                          {isNarrative || revision.cell_type === "code" ? (
                            <Tooltip>
                              <TooltipTrigger render={
                                <Button size="icon-xs" variant="ghost" aria-label={isEditing ? "Preview" : "Edit"} onClick={() => setEditingCellId(isEditing ? null : cell.id)}>
                                  {isEditing ? <Eye className="h-3 w-3" /> : <Pencil className="h-3 w-3" />}
                                </Button>
                              } />
                              <TooltipContent>{isEditing ? "Preview" : "Edit"}</TooltipContent>
                            </Tooltip>
                          ) : null}
                          {revision.cell_type === "code" ? (
                            <Tooltip>
                              <TooltipTrigger render={
                                <Button size="icon-xs" variant="ghost" aria-label="Toggle code theme" onClick={() => setCodeTheme(codeTheme === "dark" ? "light" : "dark")}>
                                  {codeTheme === "dark" ? <Sun className="h-3 w-3" /> : <Moon className="h-3 w-3" />}
                                </Button>
                              } />
                              <TooltipContent>{codeTheme === "dark" ? "Switch to light" : "Switch to dark"}</TooltipContent>
                            </Tooltip>
                          ) : null}
                          <span>revision {revision.revision}</span>
                          {editable && changed ? (
                            <Button size="xs" variant="outline" onClick={() => saveCell(cell)} disabled={saveRevision.isPending}>
                              {saveRevision.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />} Save revision
                            </Button>
                          ) : null}
                        </div>
                      </div>
                      ) : null}
                      <div className={isQuestion ? "max-w-[78%] rounded-[22px] bg-muted/80 px-4 py-3" : isNarrative ? "relative w-full py-1" : "p-3"}>
                        {isNarrative ? (
                          isEditing ? (
                            <Textarea
                              value={draft}
                              onChange={(event) => setDrafts((current) => ({ ...current, [cell.id]: event.target.value }))}
                              className="min-h-28 resize-y border-0 bg-transparent p-1 leading-6 shadow-none focus-visible:ring-0"
                              placeholder={revision.cell_type === "agent" ? "Describe what OmicsBase should investigate." : "Write a note..."}
                            />
                          ) : (
                            <MarkdownRenderer content={revision.content || "(empty note)"} className="text-base" />
                          )
                        ) : revision.cell_type === "code" ? (
                          isEditing ? (
                            <Textarea
                              value={draft}
                              onChange={(event) => setDrafts((current) => ({ ...current, [cell.id]: event.target.value }))}
                              className="min-h-28 resize-y border-0 bg-transparent p-1 font-mono text-sm leading-6 shadow-none focus-visible:ring-0"
                              placeholder="Write an R cell. Save the revision, then run it in the notebook sandbox."
                            />
                          ) : (
                            <CodeBlock code={revision.content || "(empty system cell)"} language={revision.language} />
                          )
                        ) : (
                          <CodeBlock code={revision.content || "(empty system cell)"} language={revision.language} />
                        )}
                        {isNarrative && !isEditing ? (
                          <Button size="icon-xs" variant="ghost" className="absolute right-0 top-0 opacity-0 transition-opacity group-hover:opacity-100" aria-label="Edit" onClick={() => setEditingCellId(cell.id)}>
                            <Pencil className="h-3 w-3" />
                          </Button>
                        ) : null}
                      </div>
                      {activeExecution?.cellId === cell.id && executionQuery.isLoading && !execution ? (
                        <div className="border-t border-border px-3 py-2 text-[11px] text-muted-foreground">Loading execution status...</div>
                      ) : null}
                      {execution ? (
                        <div className="border-t border-border bg-muted/20 px-3 py-3">
                          <div className="mb-2 flex items-center justify-between gap-2 text-xs">
                            <span className="font-medium text-foreground">Execution result</span>
                            {execution.cache_hit ? <span className="text-teal-700 dark:text-teal-300">Validated cache hit</span> : null}
                            {execution.result_metadata?.output_truncated ? <span className="text-amber-700 dark:text-amber-300">Preview truncated</span> : null}
                          </div>
                          {execution.status === "queued" ? (
                            <div className="flex items-center gap-2 text-xs text-muted-foreground">
                              <Loader2 className="h-3 w-3 animate-spin" /> Queued — waiting for the execution worker to start this cell.
                            </div>
                          ) : execution.status === "running" ? (
                            <div className="flex items-center gap-2 text-xs text-muted-foreground">
                              <Loader2 className="h-3 w-3 animate-spin" /> Running in the isolated R sandbox…
                            </div>
                          ) : execution.status === "cancel_requested" ? (
                            <div className="flex items-center gap-2 text-xs text-muted-foreground">
                              <Loader2 className="h-3 w-3 animate-spin" /> Cancelling…
                            </div>
                          ) : (
                            <>
                              {(() => {
                                const events = (execution.result_metadata?.events || []) as { type?: string }[];
                                const hasBlocks = events.some((event) => event.type === "text");
                                const inlineKinds = new Set(["table", "image"]);
                                const actions = resultActions[execution.id] || {};
                                const filesArtifacts = (execution.artifacts || []).filter((artifact) => !inlineKinds.has(artifact.artifact_type));
                                const imageArtifacts = (execution.artifacts || []).filter(
                                  (artifact) => artifact.artifact_type === "image" || (artifact.mime_type || "").startsWith("image/"),
                                );
                                return (
                                  <>
                                    {hasBlocks ? (
                                      <ExecutionBlocks
                                        events={execution.result_metadata?.events}
                                        artifacts={execution.artifacts || []}
                                        threadId={selectedThreadId as string}
                                        cellId={cell.id}
                                        executionId={execution.id}
                                        workspaceId={workspaceId}
                                      />
                                    ) : execution.result_metadata?.stdout_preview ? (
                                      <div data-overview-block data-overview-type="result" data-overview-id={execution.id + "-result"}>
                                        <ExecutionOutput
                                          stdout={String(execution.result_metadata.stdout_preview)}
                                          truncated={Boolean(execution.result_metadata.output_truncated)}
                                        />
                                      </div>
                                    ) : null}

                                    <div className="mb-2 flex items-center gap-1">
                                      <Tooltip>
                                        <TooltipTrigger render={
                                          <Button size="icon-xs" variant="ghost" aria-label="Execution history" onClick={() => toggleResultAction(execution.id, "history")}>
                                            <History className="h-3 w-3" />
                                          </Button>
                                        } />
                                        <TooltipContent>Execution history</TooltipContent>
                                      </Tooltip>
                                      {filesArtifacts.length > 0 ? (
                                        <Tooltip>
                                          <TooltipTrigger render={
                                            <Button size="icon-xs" variant="ghost" aria-label="Output files" onClick={() => toggleResultAction(execution.id, "files")}>
                                              <Files className="h-3 w-3" />
                                            </Button>
                                          } />
                                          <TooltipContent>Output files ({filesArtifacts.length})</TooltipContent>
                                        </Tooltip>
                                      ) : null}
                                      {hasBlocks && imageArtifacts.map((artifact) => {
                                        const name = artifact.relative_path.split("/").filter(Boolean).pop() || "plot.png";
                                        return (
                                          <Tooltip key={artifact.id}>
                                            <TooltipTrigger render={
                                              <Button size="icon-xs" variant="ghost" aria-label={"Download " + name} onClick={() => void downloadResultArtifact(execution, artifact)} disabled={downloadingResult === artifact.id}>
                                                {downloadingResult === artifact.id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
                                              </Button>
                                            } />
                                            <TooltipContent>Download {name}</TooltipContent>
                                          </Tooltip>
                                        );
                                      })}
                                    </div>

                                    {actions.history ? (
                                      <ExecutionHistory
                                        threadId={selectedThreadId as string}
                                        cellId={cell.id}
                                        executionId={execution.id}
                                        workspaceId={workspaceId}
                                        polling={executionBusy}
                                        open
                                      />
                                    ) : null}
                                    {actions.files && filesArtifacts.length > 0 ? (
                                      <NoteExecutionArtifacts
                                        artifacts={filesArtifacts}
                                        threadId={selectedThreadId as string}
                                        cellId={cell.id}
                                        executionId={execution.id}
                                        workspaceId={workspaceId}
                                        open
                                      />
                                    ) : null}
                                  </>
                                );
                              })()}
                              {execution.error ? <p className="mt-2 whitespace-pre-wrap font-mono text-xs text-red-700 dark:text-red-300">{execution.error}</p> : null}
                            </>
                          )}
                        </div>
                      ) : null}
                      {cell.revisions.length > 1 ? <div className="border-t border-border px-3 py-2 text-[10px] text-muted-foreground">{cell.revisions.length} immutable revisions are retained for provenance.</div> : null}
                    </article>
                  );
                })}
              </div>
              <div ref={threadBottomRef} className="h-px" />
            </div>
          )}
          </div>
          <ThreadOverviewRail
            containerRef={threadScrollRef}
            refreshKey={currentThread ? currentThread.id + "-" + currentThread.updated_at : "empty"}
          />
          {selectedSummary && currentThread && currentThread.cells.length > 0 ? (
            <div className="mx-auto w-full max-w-4xl shrink-0 px-4 pb-3 pt-2 md:px-6">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={async (event) => {
                  const files = Array.from(event.target.files || []);
                  event.target.value = "";
                  setComposerMenuOpen(false);
                  if (!files.length) return;
                  if (!workspaceId) {
                    setTurnError("Add data after this note is connected to a workspace.");
                    return;
                  }
                  try {
                    for (const file of files) await api.uploadFile(workspaceId, file, "auto");
                    setTurnError(null);
                    setTurnStatus(files.length === 1 ? files[0].name + " added" : files.length + " files added");
                    queryClient.invalidateQueries({ queryKey: ["projects"] });
                  } catch (error) {
                    setTurnError(error instanceof Error ? error.message : "The files could not be added.");
                  }
                }}
              />
              <div className="rounded-[28px] border border-border bg-[var(--composer-surface)] p-1.5 shadow-[0_18px_50px_rgba(15,23,42,0.08)] backdrop-blur transition-colors dark:shadow-[0_30px_80px_rgba(0,0,0,0.35)]">
                <div className="flex items-end gap-1.5">
                  <ComposerAddButton
                    open={composerMenuOpen}
                    onToggle={() => setComposerMenuOpen((value) => !value)}
                    disabled={currentThread.status !== "active" || createCell.isPending}
                    onAddData={() => {
                      setComposerMenuOpen(false);
                      fileInputRef.current?.click();
                    }}
                    onAddNote={() => {
                      setComposerMenuOpen(false);
                      addCell("markdown");
                    }}
                    onAddCode={() => {
                      setComposerMenuOpen(false);
                      addCell("code");
                    }}
                    onExport={() => {
                      setComposerMenuOpen(false);
                      exportReport.mutate();
                    }}
                    exportPending={exportReport.isPending}
                    exported={Boolean(exportReport.data)}
                    showExport={Boolean(workspaceId)}
                  />
                    <Textarea
                      value={turnDraft}
                      onChange={(event) => setTurnDraft(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                          event.preventDefault();
                          void submitTurn();
                        }
                      }}
                      disabled={turnStreaming || currentThread.status !== "active"}
                      rows={1}
                      className="max-h-52 min-h-[40px] min-w-0 flex-1 resize-none border-0 bg-transparent px-2.5 py-1.5 text-[17px] leading-6 text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-0 disabled:opacity-60"
                      placeholder="Ask OmicsBase..."
                    />
                    {turnStreaming ? <span className="shrink-0 text-[11px] text-teal-600 dark:text-teal-300">Working</span> : null}
                    <Button type="button" size="icon" className="h-10 w-10 shrink-0 rounded-full" onClick={() => void submitTurn()} disabled={turnStreaming || !turnDraft.trim() || currentThread.status !== "active"} title="Send" aria-label="Send">
                      {turnStreaming ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ArrowUp className="h-4 w-4" />}
                    </Button>
                </div>
                {turnStreaming && liveTurnText ? (
                  <div className="mt-3 max-h-40 overflow-y-auto rounded-lg border border-border bg-background px-3 py-2">
                    <MarkdownRenderer content={liveTurnText} className="text-base" />
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}
        </section>
    </main>
  );
}

