"use client";

import { useEffect, useCallback, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Loader2,
  PanelLeftOpen,
} from "lucide-react";

import { api } from "@/lib/api";
import type { FileAttachment } from "@/lib/api/types/messages";
import type { NoteCell, NoteCellExecution, NoteCellType, NoteDataFile, NoteExecutionArtifact } from "@/lib/api/types/notes";
import type { ImportableDataset, WorkspaceResult } from "@/lib/api/types/projects";
import { Button } from "@/components/ui/button";
import { DatasetPicker } from "@/components/composer/DatasetPicker";
import { ThemeToggle } from "@/components/ThemeToggle";
import { ThreadOverviewRail } from "@/components/ThreadOverviewRail";
import { ProjectsSidebarContent } from "@/components/ProjectsSidebar";
import { createNoteScope } from "../api/noteScope";
import { latestRevision } from "../lib/noteCellUtils";
import { NoteCellList } from "./NoteCellList";
import { NoteComposer } from "./NoteComposer";
import { NoteEmptyState } from "./NoteEmptyState";
import { NotePromotionDialog } from "./NotePromotionDialog";
import { NoteResultPicker } from "./NoteResultPicker";
import { useReuseCache } from "@/lib/use-note-settings";
import { friendlyToolLabel } from "@/lib/toolLabels";

export function NotesSurface({ workspaceId, initialThreadId }: { workspaceId?: string; initialThreadId?: string | null }) {
  const scope = useMemo(() => createNoteScope({ workspaceId }), [workspaceId]);
  const scopeId = scope.id;
  const queryClient = useQueryClient();
  const router = useRouter();
  const pathname = usePathname();
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [activeExecution, setActiveExecution] = useState<{ cellId: string; executionId: string } | null>(null);
  const [reuseCache] = useReuseCache();
  const [editingCellId, setEditingCellId] = useState<string | null>(null);
  const [stagedFiles, setStagedFiles] = useState<File[]>([]);
  const [turnDraft, setTurnDraft] = useState("");
  const [emptyPrompt, setEmptyPrompt] = useState("");
  const [workspaceResults, setWorkspaceResults] = useState<WorkspaceResult[] | null>(null);
  const [resultPickerOpen, setResultPickerOpen] = useState(false);
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
  const [datasetOpen, setDatasetOpen] = useState(false);
  const [datasets, setDatasets] = useState<ImportableDataset[] | null>(null);
  const [promotionReviewOpen, setPromotionReviewOpen] = useState(false);
  const [promotionName, setPromotionName] = useState("");
  const [promotionQuestion, setPromotionQuestion] = useState("");
  const [promotionNotes, setPromotionNotes] = useState("");
  const [promotionAutoBuild, setPromotionAutoBuild] = useState(false);

  const openDatasetPicker = useCallback(async () => {
    setComposerMenuOpen(false);
    setDatasetOpen(true);
    if (datasets === null) {
      try {
        const result = await api.listImportableDatasets();
        setDatasets(result.datasets);
      } catch {
        setDatasets([]);
      }
    }
  }, [datasets]);

  const importDataset = useMutation({
    mutationFn: async (dataset: ImportableDataset) => {
      const threadId = await ensureThread();
      if (!threadId) throw new Error("Could not create a note to import into.");
      await scope.importDataset(threadId, dataset.package, dataset.dataset);
      queryClient.invalidateQueries({ queryKey: workspaceId ? ["projects"] : ["note-thread", scopeId, threadId] });
      queryClient.invalidateQueries({ queryKey: ["note-thread-files", scopeId, threadId] });
    },
    onSuccess: () => {
      setDatasetOpen(false);
      setTurnStatus("Example dataset imported");
    },
    onError: (error) => {
      setTurnError(error instanceof Error ? error.message : "The dataset could not be imported.");
    },
  });

  const toggleResultAction = (executionId: string, key: "history" | "files") => {
    setResultActions((prev) => {
      const current = prev[executionId] || {};
      return { ...prev, [executionId]: { ...current, [key]: !current[key] } };
    });
  };

  const executionQuery = useQuery<NoteCellExecution>({
    queryKey: ["note-cell-execution", scopeId, selectedThreadId, activeExecution ? activeExecution.cellId : null, activeExecution ? activeExecution.executionId : null],
    queryFn: () => scope.getExecution(selectedThreadId as string, activeExecution ? activeExecution.cellId : "", activeExecution ? activeExecution.executionId : ""),
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
    queryFn: () => scope.listThreads(),
    enabled: Boolean(scopeId),
  });
  const threadQuery = useQuery({
    queryKey: ["note-thread", scopeId, selectedThreadId],
    queryFn: () => scope.getThread(selectedThreadId as string),
    enabled: Boolean(scopeId && selectedThreadId),
  });
  const threadFilesQuery = useQuery<NoteDataFile[]>({
    queryKey: ["note-thread-files", scopeId, selectedThreadId],
    queryFn: () => scope.listFiles(selectedThreadId as string),
    enabled: Boolean(scopeId && selectedThreadId),
  });
  const threads = useMemo(() => threadsQuery.data || [], [threadsQuery.data]);
  const currentThread = threadQuery.data;
  const threadFiles = threadFilesQuery.data || [];

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset execution selection when changing note scope
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
      // eslint-disable-next-line react-hooks/set-state-in-effect -- hydrate the selection from the loaded thread list
      setSelectedThreadId(null);
      return;
    }
    if (initialThreadId) {
      if (threads.some((item) => item.id === initialThreadId)) {
        if (initialThreadId !== selectedThreadId) {
          setSelectedThreadId(initialThreadId);
        }
        return;
      }
      // The requested thread is not in the list yet (still loading or gone);
      // keep the current selection and let the refetch settle it.
    }
    if (!selectedThreadId || !threads.some((item) => item.id === selectedThreadId)) {
      setSelectedThreadId(threads[0].id);
    }
  }, [threads, selectedThreadId, initialThreadId]);

  // Auto-run the first turn when a launch from home arrives with ?prompt=.
  const searchParams = useSearchParams();
  const autoRunPromptRef = useRef<string | null>(null);
  useEffect(() => {
    const promptParam = searchParams.get("prompt");
    if (promptParam && autoRunPromptRef.current === null) {
      autoRunPromptRef.current = promptParam;
    }
  }, [searchParams]);

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
    // eslint-disable-next-line react-hooks/set-state-in-effect -- hydrate editable drafts from the persisted thread
    setDrafts(next);
  }, [currentThread]);

  const executeCell = useMutation({
    mutationFn: (cell: NoteCell) => {
      const revision = latestRevision(cell);
      if (!revision) throw new Error("Cannot execute a cell without an existing revision.");
      const idempotencyKey = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `note-${cell.id}-${revision.revision}-${Date.now()}`;
      return scope.executeCell(selectedThreadId as string, cell.id, {
        revision: revision.revision,
        cache_policy: reuseCache ? "reuse" : "off",
        idempotency_key: idempotencyKey,
      });
    },
    onSuccess: (execution, cell) => {
      setActiveExecution({ cellId: cell.id, executionId: execution.id });
    },
  });

  const cancelExecution = useMutation({
    mutationFn: () => {
      if (!activeExecution || !selectedThreadId) throw new Error("No active execution to cancel.");
      return scope.cancelExecution(selectedThreadId, activeExecution.cellId, activeExecution.executionId);
    },
    onSuccess: () => {
      void executionQuery.refetch();
    },
  });

  const createThread = useMutation({
    mutationFn: (title: string) => scope.createThread({ title: title || "Untitled note" }),
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
      return api.createWorkspaceFromStandaloneNoteThread(selectedThreadId, {
        name: promotionName.trim() || undefined,
        question: promotionQuestion.trim() || undefined,
        notes: promotionNotes.trim() || undefined,
        auto_build: promotionAutoBuild,
      });
    },
    onSuccess: (result) => {
      setPromotionReviewOpen(false);
      window.location.assign("/projects/" + result.project_id + "/workspace");
    },
  });

  const openPromotionReview = () => {
    if (workspaceId || !currentThread) return;
    setPromotionName(currentThread.title || "");
    setPromotionQuestion("");
    setPromotionNotes("");
    setPromotionAutoBuild(false);
    setPromotionReviewOpen(true);
  };
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
    mutationFn: (cellType: NoteCellType) => scope.createCell(selectedThreadId as string, {
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
      return scope.appendRevision(selectedThreadId as string, cell.id, {
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

  const openResultPicker = () => {
    setComposerMenuOpen(false);
    setResultPickerOpen(true);
    if (workspaceResults === null && workspaceId) {
      api
        .getNoteResults(workspaceId)
        .then(setWorkspaceResults)
        .catch(() => setWorkspaceResults([]));
    }
  };

  const insertWorkspaceResult = (path: string) => {
    setResultPickerOpen(false);
    const line = `[workspace result: ${path}]`;
    if (currentThread && currentThread.cells.length > 0) {
      setTurnDraft((prev) => (prev.trim() ? `${prev}\n${line}` : line));
    } else {
      setEmptyPrompt((prev) => (prev.trim() ? `${prev}\n${line}` : line));
    }
  };
  const saveCell = (cell: NoteCell) => {
    if (!selectedThreadId || saveRevision.isPending) return;
    saveRevision.mutate({ cell, content: drafts[cell.id] ?? latestRevision(cell)?.content ?? "" });
  };
  const runTurn = useCallback(async (threadId: string, message: string, attachments?: FileAttachment[]) => {
    if (!message || !threadId || turnStreaming) return;
    setTurnDraft("");
    setTurnStreaming(true);
    setTurnStatus("Thinking");
    setLiveTurnText("");
    setTurnError(null);
    try {
      await scope.streamTurn(
        threadId,
        { message, auto_execute: true, attachments },
        (event) => {
          if (event.type === "token" || event.type === "token_chunk") {
            setLiveTurnText((current) => current + (event.token || ""));
          }
          if (event.type === "status" || event.type === "tool_started") {
            const raw = event.message || event.status || friendlyToolLabel(event.tool) || "Working";
            setTurnStatus(/^thinking about/i.test(raw) ? "Thinking" : raw);
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
  }, [queryClient, scope, scopeId, turnStreaming]);

  // Auto-run the first turn when a launch from home arrives with ?prompt=.
  useEffect(() => {
    if (!selectedThreadId || turnStreaming || threadFilesQuery.isLoading) return;
    const message = autoRunPromptRef.current;
    if (!message) return;
    autoRunPromptRef.current = null;
    router.replace(`${pathname}?thread=${selectedThreadId}`, { scroll: false });
    const initialAttachments: FileAttachment[] = (threadFilesQuery.data || []).map((file) => ({
      name: file.name,
      format: file.format,
      size_bytes: file.size_bytes,
      r_path: file.r_path,
      source: "note",
    }));
    void runTurn(selectedThreadId, message, initialAttachments.length ? initialAttachments : undefined);
  }, [selectedThreadId, turnStreaming, threadFilesQuery.isLoading, threadFilesQuery.data, pathname, router, runTurn]);

  const submitTurn = async () => {
    if (!selectedThreadId || !currentThread || currentThread.status !== "active") return;
    const filesToUpload = [...stagedFiles];
    setStagedFiles([]);
    const uploadedAttachments: FileAttachment[] = [];
    if (filesToUpload.length > 0) {
      setTurnStatus("Uploading attached files...");
      for (const file of filesToUpload) {
        try {
          const uploaded = await scope.uploadFile(selectedThreadId, file);
          uploadedAttachments.push({
            name: uploaded.name,
            format: uploaded.format,
            size_bytes: uploaded.size_bytes,
            r_path: uploaded.r_path,
            source: "note",
          });
        } catch (error) {
          console.error("Failed to upload note attachment:", file.name, error);
        }
      }
      queryClient.invalidateQueries({ queryKey: ["note-thread-files", scopeId, selectedThreadId] });
    }
    const message = turnDraft.trim() || (filesToUpload.length ? "I attached study files to this note." : "");
    if (!message) return;
    void runTurn(selectedThreadId, message, uploadedAttachments.length ? uploadedAttachments : undefined);
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

  // Create (and select) a thread when an action needs one but none is open.
  const ensureThread = async (): Promise<string | null> => {
    if (selectedThreadId) return selectedThreadId;
    try {
      const thread = await scope.createThread({ title: "Untitled note" });
      setSelectedThreadId(thread.id);
      router.replace(`${pathname}?thread=${thread.id}`, { scroll: false });
      queryClient.invalidateQueries({ queryKey: ["note-threads", scopeId] });
      return thread.id;
    } catch {
      setTurnError("Could not create a note.");
      return null;
    }
  };

  const handleThreadFileSelection = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    event.target.value = "";
    setComposerMenuOpen(false);
    if (!files.length) return;
    setStagedFiles((prev) => [...prev, ...files]);
  };
  const addCellWithEnsure = async (cellType: NoteCellType) => {
    const threadId = await ensureThread();
    if (!threadId) return;
    try {
      await scope.createCell(threadId, {
        cell_type: cellType,
        language: cellType === "code" ? "r" : null,
        content: "",
      });
      queryClient.invalidateQueries({ queryKey: ["note-thread", scopeId, threadId] });
      queryClient.invalidateQueries({ queryKey: ["note-threads", scopeId] });
    } catch (error) {
      setTurnError(error instanceof Error ? error.message : "The cell could not be added.");
    }
  };

  const openFilePicker = () => {
    setComposerMenuOpen(false);
    fileInputRef.current?.click();
  };

  const datasetPicker = datasetOpen ? (
    <DatasetPicker
      datasets={datasets}
      onClose={() => setDatasetOpen(false)}
      onPick={(dataset) => importDataset.mutate(dataset)}
      pending={importDataset.isPending}
    />
  ) : null;

  const resultPicker = resultPickerOpen ? (
    <NoteResultPicker
      results={workspaceResults}
      onClose={() => setResultPickerOpen(false)}
      onPick={insertWorkspaceResult}
    />
  ) : null;

  const downloadResultArtifact = async (cellId: string, execution: NoteCellExecution, artifact: NoteExecutionArtifact) => {
    if (downloadingResult || !selectedThreadId) return;
    setDownloadingResult(artifact.id);
    try {
      const blob = await scope.getArtifactContent(selectedThreadId, cellId, execution.id, artifact.id);
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
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={handleThreadFileSelection}
        />
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
          <NotePromotionDialog
            open={promotionReviewOpen}
            name={promotionName}
            question={promotionQuestion}
            notes={promotionNotes}
            autoBuild={promotionAutoBuild}
            pending={promoteThread.isPending}
            onNameChange={setPromotionName}
            onQuestionChange={setPromotionQuestion}
            onNotesChange={setPromotionNotes}
            onAutoBuildChange={setPromotionAutoBuild}
            onClose={() => setPromotionReviewOpen(false)}
            onSubmit={() => promoteThread.mutate()}
          />
          <div className="flex items-center gap-2">
            {!workspaceId && selectedThreadId ? (
              <Button size="sm" variant="outline" onClick={openPromotionReview} disabled={promoteThread.isPending}>
                Promote to workspace
              </Button>
            ) : null}
            <ThemeToggle />
          </div>
        </header>

        <div ref={threadScrollRef} className="min-h-0 flex-1 overflow-y-auto">
          {threadsQuery.isLoading || (threads.length > 0 && !selectedSummary) ? (
            <div className="flex min-h-full items-center justify-center"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
          ) : !selectedSummary ? (
            <NoteEmptyState
              composer={
                <NoteComposer
                  prompt={emptyPrompt}
                  onPromptChange={setEmptyPrompt}
                  onSubmit={() => void handleEmptySubmit()}
                  composerMenuOpen={composerMenuOpen}
                  onToggleComposerMenu={() => setComposerMenuOpen((value) => !value)}
                  addMenuDisabled={createThread.isPending || turnStreaming}
                  submitDisabled={!emptyPrompt.trim() || createThread.isPending || turnStreaming}
                  submitPending={createThread.isPending || turnStreaming}
                  onAddData={openFilePicker}
                  onImportDataset={() => void openDatasetPicker()}
                  onAddNote={() => {
                    setComposerMenuOpen(false);
                    void addCellWithEnsure("markdown");
                  }}
                  onAddCode={() => {
                    setComposerMenuOpen(false);
                    void addCellWithEnsure("code");
                  }}
                  beforeComposer={datasetPicker}
                />
              }
            />
          ) : threadQuery.isLoading || !currentThread ? (
            <div className="flex min-h-full items-center justify-center"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
          ) : currentThread.cells.length === 0 && !turnStreaming ? (
            <NoteEmptyState
              composer={
                <NoteComposer
                  prompt={emptyPrompt}
                  onPromptChange={setEmptyPrompt}
                  onSubmit={() => void handleEmptySubmit()}
                  composerMenuOpen={composerMenuOpen}
                  onToggleComposerMenu={() => setComposerMenuOpen((value) => !value)}
                  addMenuDisabled={currentThread.status !== "active" || createCell.isPending}
                  submitDisabled={!emptyPrompt.trim() || createThread.isPending || turnStreaming}
                  submitPending={createThread.isPending || turnStreaming}
                  onAddData={openFilePicker}
                  onImportDataset={() => void openDatasetPicker()}
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
                  onInsertResult={openResultPicker}
                  exportPending={exportReport.isPending}
                  exported={Boolean(exportReport.data)}
                  showExport={Boolean(workspaceId)}
                  beforeComposer={
                    <>
                      {resultPicker}
                      {datasetPicker}
                    </>
                  }
                />
              }
              liveTurnText={liveTurnText}
              selectedThreadId={selectedThreadId}
              turnStreaming={turnStreaming}
            />
          ) : (
            <NoteCellList
              thread={currentThread}
              threadFiles={threadFiles}
              selectedThreadId={selectedThreadId as string}
              workspaceId={workspaceId}
              requestError={requestError || null}
              drafts={drafts}
              editingCellId={editingCellId}
              activeExecution={activeExecution}
              executionData={executionQuery.data}
              executionLoading={executionQuery.isLoading}
              resultActions={resultActions}
              downloadingResult={downloadingResult}
              liveTurnText={liveTurnText}
              turnStreaming={turnStreaming}
              turnStatus={turnStatus}
              threadBottomRef={threadBottomRef}
              onEditCell={setEditingCellId}
              onDraftChange={(cellId, content) => setDrafts((current) => ({ ...current, [cellId]: content }))}
              onSaveCell={saveCell}
              savePending={saveRevision.isPending}
              onExecuteCell={(cell) => executeCell.mutate(cell)}
              executePending={executeCell.isPending}
              onCancelExecution={() => cancelExecution.mutate()}
              cancelPending={cancelExecution.isPending}
              onToggleResultAction={toggleResultAction}
              onDownloadArtifact={downloadResultArtifact}
            />
          )}
        </div>

        <ThreadOverviewRail
          containerRef={threadScrollRef}
          refreshKey={currentThread ? currentThread.id + "-" + currentThread.updated_at : "empty"}
        />

        {selectedSummary && currentThread && currentThread.cells.length > 0 ? (
          <div className="mx-auto w-full max-w-4xl shrink-0 px-4 pb-3 pt-2 md:px-6">
            <NoteComposer
              prompt={turnDraft}
              onPromptChange={setTurnDraft}
              onSubmit={() => void submitTurn()}
              composerMenuOpen={composerMenuOpen}
              onToggleComposerMenu={() => setComposerMenuOpen((value) => !value)}
              addMenuDisabled={currentThread.status !== "active" || createCell.isPending}
              inputDisabled={turnStreaming || currentThread.status !== "active"}
              submitDisabled={turnStreaming || (!turnDraft.trim() && stagedFiles.length === 0) || currentThread.status !== "active"}
              submitPending={turnStreaming}
              onAddData={openFilePicker}
              onImportDataset={() => void openDatasetPicker()}
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
              onInsertResult={openResultPicker}
              exportPending={exportReport.isPending}
              exported={Boolean(exportReport.data)}
              showExport={Boolean(workspaceId)}
              beforeComposer={
                <>
                  {resultPicker}
                  {datasetPicker}
                </>
              }
              stagedFiles={stagedFiles}
              onRemoveFile={(index) => setStagedFiles((prev) => prev.filter((_, i) => i !== index))}
            />
          </div>
        ) : null}
      </section>
    </main>
  );
}

