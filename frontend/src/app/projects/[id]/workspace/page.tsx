"use client";

import { useMemo } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api, type EditTransaction } from "@/lib/api";
import { WorkspaceComposer } from "@/features/workspace/components/WorkspaceComposer";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { ThemeToggle } from "@/components/ThemeToggle";
import {
  ActionEvent,
  type ApplyResult,
  AgentActionCard,
  applyResultsToActionEvents,
  jobFailureToActionEvent,
} from "@/components/AgentActionCard";
import { useWorkspaceAgent } from "@/features/workspace/hooks/useWorkspaceAgent";
import { useWorkspaceEdits } from "@/features/workspace/hooks/useWorkspaceEdits";
import { useWorkspaceJobs } from "@/features/workspace/hooks/useWorkspaceJobs";
import { useWorkspaceLayout } from "@/features/workspace/hooks/useWorkspaceLayout";
import { WorkspaceEditorPanel } from "@/features/workspace/components/WorkspaceEditorPanel";
import { WorkspaceAssistantContent } from "@/features/workspace/components/WorkspaceAssistantContent";
import { useWorkspaceFiles } from "@/features/workspace/hooks/useWorkspaceFiles";
import { flattenFileTree } from "@/features/workspace/utils/filePaths";
import { MarkdownRenderer } from "@/components/MarkdownRenderer";
import { MessageAttachments } from "@/components/MessageAttachments";
import { ProjectsSidebarContent } from "@/components/ProjectsSidebar";
import { ThreadOverviewRail } from "@/components/ThreadOverviewRail";
import { retryStageCopy } from "@/lib/retryStage";
import { useTheme } from "next-themes";
import {
  ChevronsLeft,
  ChevronsRight,
  ChevronDown,
  Code2,
  Download,
  ExternalLink,
  FileText,
  Globe,
  HelpCircle,
  History,
  Loader2,
  MessageSquare,
  Play,
  RefreshCw,
} from "lucide-react";

const stateCopy: Record<string, string> = {
  idle: "Ready for instruction",
  generating: "Writing analysis source",
  rendering: "Rendering report preview",
  editing: "Applying requested edits",
  completed: "Report ready",
  failed: "Needs attention",
};

function statusTone(status: string | null | undefined) {
  if (status === "completed" || status === "approved" || status === "passed") {
    return "bg-emerald-500/15 text-emerald-700 border-emerald-500/20 dark:text-emerald-300";
  }
  if (status === "failed") return "bg-red-500/15 text-red-700 border-red-500/20 dark:text-red-300";
  if (status === "warning") return "bg-amber-500/15 text-amber-700 border-amber-500/20 dark:text-amber-300";
  return "bg-teal-500/15 text-teal-700 border-teal-500/20 dark:text-teal-300";
}

export default function WorkspacePage() {
  const params = useParams();
  const { resolvedTheme } = useTheme();
  const projectId = params.id as string;

  const { data: project } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
  });

  const workspaceFiles = useWorkspaceFiles({ projectId, project });
  const { activeDraft, activeTab, clearDrafts, closeConflictDialog, fileTree, hasProjectFiles, hasUploadedFiles, isDirty, selectTab } = workspaceFiles;
  const isLive = ["generating", "rendering", "editing"].includes(project?.status || "");
  const isFailed = project?.status === "failed";
  const hasPreview = Boolean(project?.project_dir);
  const agentState = project?.agent_state || "idle";
  const previewReportPath = useMemo(() => {
    const allPaths = flattenFileTree(fileTree || []);
    const renderedPages = allPaths.filter((path) => path.startsWith("output/") && path.endsWith(".html") && !path.includes("/site_libs/"));
    const hasIndex = renderedPages.includes("output/index.html");
    const firstContentPage = renderedPages.find((path) => path !== "output/index.html");
    if (isFailed && firstContentPage) return firstContentPage.replace(/^output\//, "");
    if (!hasIndex && renderedPages[0]) return renderedPages[0].replace(/^output\//, "");
    return "index.html";
  }, [fileTree, isFailed]);
  const {
    actionEvents, agentActivity, answerQuestion, askAgent, assistantPending, chatMode, displayChatMessages,
    handleSendPrompt, pendingQuestion, quickActions, setAgentActivity, setChatMode, streamingReasoning,
  } = useWorkspaceAgent({ projectId, project, activeTab, activeDraft, isDirty, previewReportPath });
  const { buildError, buildNow, buildPending, executionRuns, latestFailedJob, previewProgressSignature, retryMutation, retryStage, workspaceRefreshKey } = useWorkspaceJobs({
    projectId,
    project,
    onAgentActivity: setAgentActivity,
    hasUploadedFiles,
  });
  const {
    iframeKey,
    isResizingSidebar,
    refreshPreview,
    setIsResizingSidebar,
    setShowHistory,
    setShowProjectMenu,
    setSidebarOpen,
    setSidebarWidth,
    setViewMode,
    setWorkspaceMode,
    showHistory,
    showProjectMenu,
    sidebarOpen,
    sidebarWidth,
    viewMode,
    workspaceChatScrollRef,
    workspaceMode,
  } = useWorkspaceLayout({ project, previewProgressSignature, workspaceRefreshKey });
  const { editHistory, revertEditMutation, selectedEdit, selectedEditId, setSelectedEditId } = useWorkspaceEdits({
    projectId,
    project,
    onReverted: () => {
      clearDrafts();
      closeConflictDialog();
      refreshPreview();
    },
  });
  const recentAgentActions = useMemo(() => [...(project?.agent_actions || [])].reverse().slice(0, 5), [project?.agent_actions]);
  const applyActionEvents = useMemo(() => {
    const editAction = [...(project?.agent_actions || [])]
      .reverse()
      .find((action) => action.type === "edit" && Array.isArray(action.details?.apply_results));
    if (!editAction || !editAction.details) return [] as ActionEvent[];
    const applyResults = editAction.details.apply_results;
    if (!Array.isArray(applyResults)) return [] as ActionEvent[];
    return applyResultsToActionEvents(applyResults as ApplyResult[], String(editAction.time || "edit"));
  }, [project?.agent_actions]);
  const failureActionEvent = useMemo(
    () => (latestFailedJob ? jobFailureToActionEvent(latestFailedJob) : null),
    [latestFailedJob],
  );
  const reviewChecks = useMemo(() => {
    const reviewAction = [...(project?.agent_actions || [])].reverse().find((action) => action.type === "review");
    if (!reviewAction) return null;
    return {
      status: reviewAction.status,
      summary: reviewAction.summary,
      checks: (reviewAction.details?.checks || []) as { name: string; status: string; detail: string }[],
    };
  }, [project?.agent_actions]);

  const reportUrl = api.getReportUrl(projectId, previewReportPath);
  const downloadUrl = api.getDownloadUrl(projectId);


  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      {sidebarOpen ? (
      <aside
        className="relative flex min-h-0 shrink-0 flex-col border-r border-border bg-sidebar"
        style={{ width: sidebarWidth === null ? (viewMode === "chat" ? "320px" : "clamp(340px, 33vw, 460px)") : `${sidebarWidth}px` }}
      >
        {viewMode === "chat" ? (
          <div className="flex h-full min-h-0 flex-col">
            <div className="min-h-0 flex-1">
              <ProjectsSidebarContent notesScope="recent" onClose={() => setSidebarOpen(false)} />
            </div>
            <div className="flex shrink-0 items-center justify-between border-t border-border p-3">
              <ThemeToggle />
            </div>
          </div>
        ) : (
          <>
            <div className="border-b border-border px-4 py-4">
          <div className="flex items-start justify-between gap-3">
            <div className="relative min-w-0">
              <button
                type="button"
                onClick={() => setShowProjectMenu((value) => !value)}
                className="flex max-w-full items-center gap-1 text-left"
                aria-expanded={showProjectMenu}
              >
                <span className="truncate text-sm font-semibold text-foreground">{project?.name || "Project"}</span>
                <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              </button>
              <p className="mt-1 text-[11px] text-muted-foreground">{stateCopy[agentState] || "Working"}</p>

              {showProjectMenu ? (
                <div className="absolute left-0 top-9 z-30 w-60 overflow-hidden rounded-xl border border-border bg-popover py-1 shadow-2xl">
                  <Link href="/" className="block px-3 py-2 text-xs text-muted-foreground hover:bg-muted hover:text-foreground">
                    Dashboard
                  </Link>
                  <button
                    type="button"
                    onClick={() => {
                      setShowHistory(true);
                      setShowProjectMenu(false);
                    }}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    <History className="h-3.5 w-3.5" />
                    View change history
                  </button>
                  <a href={downloadUrl} download className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground hover:bg-muted hover:text-foreground">
                    <Download className="h-3.5 w-3.5" />
                    Download project
                  </a>
                </div>
              ) : null}
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="outline" className={`border px-2 py-1 text-[10px] ${statusTone(project?.status)}`}>
                {project?.status || "loading"}
              </Badge>
              <button
                type="button"
                onClick={() => setShowHistory((value) => !value)}
                className={`rounded-md p-1 transition-colors hover:bg-muted hover:text-foreground ${
                  showHistory ? "text-teal-300" : "text-muted-foreground"
                }`}
                title={showHistory ? "Return to chat" : "View history"}
                aria-pressed={showHistory}
              >
                <History className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={() => setSidebarOpen(false)}
                className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                title="Collapse sidebar"
              >
                <ChevronsLeft className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>

        {showHistory ? (
          <>
            <div className="shrink-0 border-b border-border px-3 py-2">
              <div className="grid grid-cols-2 rounded-lg bg-muted p-0.5">
                <button
                  type="button"
                  className="rounded-md bg-background px-3 py-1.5 text-xs text-white"
                >
                  History
                </button>
                <button
                  type="button"
                  onClick={() => setShowHistory(false)}
                  className="rounded-md px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground"
                >
                  Chat
                </button>
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3 no-scrollbar">
              <div className="space-y-3">
                {(editHistory?.transactions || []).length > 0 ? (
                  <section className="space-y-1.5">
                    <p className="px-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">Committed source edits</p>
                    {(editHistory?.transactions || []).map((transaction: EditTransaction) => (
                      <div
                        key={transaction.transaction_id}
                        role="button"
                        tabIndex={0}
                        onClick={() => setSelectedEditId(transaction.transaction_id)}
                        onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") setSelectedEditId(transaction.transaction_id); }}
                        className={`w-full rounded-lg border px-2 py-2 text-left transition-colors ${selectedEditId === transaction.transaction_id ? "border-teal-500/60 bg-teal-500/5" : "border-border/60 hover:bg-muted"}`}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <p className="truncate text-xs text-foreground/90">{transaction.summary || transaction.origin || "Source edit"}</p>
                            <p className="mt-1 truncate font-mono text-[9px] text-muted-foreground">{transaction.files.map((file) => file.path).join(", ")}</p>
                          </div>
                          {transaction.status === "committed" ? (
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              className="h-6 shrink-0 px-2 text-[10px]"
                              disabled={revertEditMutation.isPending}
                              onClick={() => revertEditMutation.mutate(transaction.transaction_id)}
                            >
                              Undo
                            </Button>
                          ) : (
                            <Badge variant="outline" className="text-[9px]">{transaction.status}</Badge>
                          )}
                        </div>
                      </div>
                    ))}
                    {selectedEdit ? (
                      <div className="mt-2 space-y-2 rounded-lg border border-border/60 bg-muted/30 p-2">
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">Diff preview</p>
                          <span className="font-mono text-[9px] text-muted-foreground">{selectedEdit.transaction_id.slice(0, 12)}</span>
                        </div>
                        {(selectedEdit.files || []).map((file) => (
                          <div key={file.path} className="space-y-1">
                            <p className="font-mono text-[10px] text-foreground/80">{file.path}</p>
                            <pre className="max-h-56 overflow-auto rounded bg-background p-2 text-[10px] leading-4 text-foreground/80">{file.diff || "Binary or unavailable diff"}</pre>
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </section>
                ) : null}
                <section className="space-y-0.5">
                  <p className="px-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">Agent activity</p>
                {[...(project?.agent_actions || [])].reverse().map((action, index) => (
                  <div
                    key={`${action.time}-${index}`}
                    className="rounded-lg px-2 py-2 transition-colors hover:bg-muted"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <p className="text-xs leading-4 text-foreground/90">{action.summary}</p>
                      <time className="shrink-0 text-[9px] text-muted-foreground/70">
                        {new Date(action.time).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                      </time>
                    </div>
                    <p className="mt-1 text-[9px] uppercase tracking-wide text-muted-foreground/70">{action.type}</p>
                  </div>
                ))}
                </section>
              </div>
            </div>
          </>
        ) : (
          <>
        <ScrollArea className="min-h-0 flex-1 px-4 py-4">
          <div className="space-y-4">
            <div className="rounded-2xl bg-muted px-3 py-3 text-xs leading-5 text-muted-foreground">
              <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                Agent
              </div>
              <p className="mt-2">{typeof project?.agent_memory?.summary === "string" ? project.agent_memory.summary : "Ask for a change or inspect the current report."}</p>
            </div>

            {isLive ? (
              <div className="flex flex-col gap-1 rounded-2xl bg-teal-500/[0.08] px-3 py-2.5 text-xs text-teal-800 dark:text-teal-100">
                <div className="flex items-center gap-2 font-medium">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Updating the report…
                </div>
                {agentActivity ? (
                  <p className="pl-6 text-[11px] text-teal-800 dark:text-teal-700/90 dark:text-teal-100/80">{agentActivity}</p>
                ) : null}
                {Array.isArray(project?.agent_memory?.pending_guidance)
                  && project.agent_memory.pending_guidance.length > 0 ? (
                  <p className="pl-6 text-[11px] text-amber-700 dark:text-amber-200/90">
                    Queued guidance: {project.agent_memory.pending_guidance.map((item: { content: string }) => item.content).join("; ")}
                  </p>
                ) : null}
              </div>
            ) : null}

            {project?.status === "needs_clarification" ? (
              <div className="flex items-center gap-3 rounded-2xl border border-amber-500/30 bg-amber-500/10 px-4 py-3">
                <HelpCircle className="h-4 w-4 shrink-0 text-amber-400" />
                <p className="flex-1 text-xs leading-5 text-amber-700 dark:text-amber-200/90">
                  The agent needs a decision before it can continue. Answer in the chat below.
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 gap-1.5 border-amber-500/40 text-amber-700 hover:bg-amber-500/10 dark:text-amber-200"
                  onClick={() => setViewMode("chat")}
                >
                  <MessageSquare className="h-3.5 w-3.5" />
                  Open chat
                </Button>
              </div>
            ) : null}

            {isFailed && failureActionEvent ? (
              <div className="space-y-2">
                <AgentActionCard
                  event={failureActionEvent}
                  onAskAgent={(prompt) => askAgent(prompt, "build")}
                  onOpenPath={selectTab}
                />
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="w-full gap-2"
                  disabled={retryMutation.isPending}
                  onClick={() => retryMutation.mutate()}
                  title={retryStageCopy[retryStage].detail}
                >
                  {retryMutation.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <RefreshCw className="h-3.5 w-3.5" />
                  )}
                  {retryStageCopy[retryStage].label}
                </Button>
                {retryMutation.isError ? (
                  <p className="text-xs text-red-600 dark:text-red-300">
                    {(retryMutation.error as Error).message}
                  </p>
                ) : null}
              </div>
            ) : null}

            <div className="space-y-2">
              {[...applyActionEvents, ...actionEvents].map((event) => (
                <AgentActionCard
                  key={event.id}
                  event={event}
                  onAskAgent={(prompt) => askAgent(prompt, "build")}
                  onOpenPath={selectTab}
                />
              ))}

              {recentAgentActions.length ? recentAgentActions.slice().reverse().map((action, index) => (
                <div key={`${action.time}-${index}`} className="flex gap-2">
                  <div className={`mt-2 h-1.5 w-1.5 shrink-0 rounded-full ${action.status === "failed" ? "bg-red-400" : action.status === "completed" || action.status === "approved" || action.status === "passed" ? "bg-emerald-400" : "bg-teal-400"}`} />
                  <div className="rounded-2xl bg-muted px-3 py-2 text-xs leading-5 text-muted-foreground">
                    {action.summary}
                  </div>
                </div>
              )) : null}

              {viewMode === "workspace" ? displayChatMessages.map((message, index) => (
                <div
                  key={message.id || `${message.time}-${index}`}
                  className={`flex w-full ${message.role === "user" ? "justify-end" : "justify-start"} py-1`}
                >
                  {message.role === "user" ? (
                    <div className="max-w-[85%] rounded-3xl bg-muted/80 px-4 py-2.5 text-base leading-relaxed text-foreground shadow-sm">
                      <MessageAttachments attachments={message.attachments} className="mb-2" />
                      {message.content}
                    </div>
                  ) : (
                    <WorkspaceAssistantContent
                      content={message.content}
                      reasoning={typeof message.metadata?.reasoning === "string" ? message.metadata.reasoning : null}
                      reasoningOpen={message.id === "streaming-assistant"}
                    />
                  )}
                </div>
              )) : null}

              {quickActions.length ? (
                <div className="flex flex-wrap gap-2">
                  {quickActions.map((action) => (
                    <Button
                      key={`${action.type}-${action.label}`}
                      type="button"
                      size="sm"
                      variant="outline"
                      className="h-7 gap-1.5 rounded-full text-[11px]"
                      disabled={assistantPending}
                      onClick={() => askAgent(action.prompt, "build")}
                    >
                      <Play className="h-3 w-3" />
                      {action.label}
                    </Button>
                  ))}
                </div>
              ) : null}
            </div>

            {displayChatMessages.length === 0 && recentAgentActions.length === 0 && actionEvents.length === 0 ? (
              <div className="px-1 text-xs leading-5 text-muted-foreground">
                Ask a question or describe the change you want. Use Discuss to plan without editing.
              </div>
            ) : null}

            {assistantPending ? (
              <div className="flex items-center gap-2 rounded-2xl border border-border bg-muted/60 px-3 py-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin text-teal-300" />
                {agentActivity}
              </div>
            ) : null}

            {reviewChecks?.status === "warning" ? (
              <p className="px-1 text-[11px] text-amber-700 dark:text-amber-300">{reviewChecks.summary}</p>
            ) : null}
            {executionRuns?.runs?.[0]?.validators?.length ? (
              <div className="rounded-2xl border border-border bg-muted/30 px-3 py-2 text-[11px]">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold uppercase tracking-[0.12em] text-muted-foreground">Scientific checks</span>
                  <Badge variant="outline" className={statusTone(executionRuns.runs[0].status)}>{executionRuns.runs[0].status}</Badge>
                </div>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {executionRuns.runs[0].validators.map((validator) => (
                    <Badge key={validator.step_id} variant="outline" className={statusTone(validator.status)}>
                      {validator.step_id}: {validator.status}
                    </Badge>
                  ))}
                </div>
                {executionRuns.runs[0].target_pages.length ? (
                  <p className="mt-1 text-muted-foreground">Scoped page render; validators were not rerun.</p>
                ) : null}
              </div>
            ) : null}
          </div>
        </ScrollArea>

        {viewMode === "workspace" ? (
        <div className="shrink-0 border-t border-border p-3">
          <WorkspaceComposer
            pendingQuestion={pendingQuestion}
            chatMode={chatMode}
            disabled={assistantPending}
            onSend={(message, mode) => {
              setChatMode(mode);
              void handleSendPrompt(undefined, { message, mode });
            }}
            onAnswer={answerQuestion}
            onModeChange={setChatMode}
          />
        </div>
        ) : null}
          </>
        )}
      </>
    )}
        <div
          className={`absolute inset-y-0 right-[-3px] z-20 w-1.5 touch-none cursor-col-resize transition-colors ${
            isResizingSidebar ? "bg-teal-400/60" : "bg-transparent hover:bg-teal-400/35"
          }`}
          onPointerDown={(event) => {
            event.currentTarget.setPointerCapture(event.pointerId);
            setIsResizingSidebar(true);
          }}
          onPointerMove={(event) => {
            if (!isResizingSidebar) return;
            setSidebarWidth(Math.min(520, Math.max(300, event.clientX)));
          }}
          onPointerUp={(event) => {
            if (event.currentTarget.hasPointerCapture(event.pointerId)) {
              event.currentTarget.releasePointerCapture(event.pointerId);
            }
            setIsResizingSidebar(false);
          }}
          onPointerCancel={() => setIsResizingSidebar(false)}
        />
      </aside>
      ) : (
        null
      )}

      <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-background">
        <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden">
          <div className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-border px-3">
            <div className="flex items-center gap-2">
              {!sidebarOpen ? (
                <button
                  type="button"
                  onClick={() => setSidebarOpen(true)}
                  className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  title="Open sidebar"
                >
                  <ChevronsRight className="h-4 w-4" />
                </button>
              ) : null}

              {hasProjectFiles ? (
                <div className="inline-flex shrink-0 items-center rounded-full border border-border bg-muted/60 p-0.5">
                  <button
                    type="button"
                    onClick={() => setViewMode("chat")}
                    className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                      viewMode === "chat"
                        ? "bg-background text-foreground shadow-sm"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <MessageSquare className="h-3.5 w-3.5" />
                    Chat
                  </button>
                  <button
                    type="button"
                    onClick={() => setViewMode("workspace")}
                    className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                      viewMode === "workspace"
                        ? "bg-background text-foreground shadow-sm"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <Code2 className="h-3.5 w-3.5" />
                    Workspace
                  </button>
                </div>
              ) : null}

              {viewMode === "workspace" ? (
                <div className="ml-2 inline-flex shrink-0 items-center rounded-full border border-border bg-muted p-1">
                  <button
                    type="button"
                    onClick={() => setWorkspaceMode("preview")}
                    className={`inline-flex items-center gap-1.5 rounded-full transition-colors ${
                      workspaceMode === "preview"
                        ? "bg-teal-500 px-3 py-1.5 text-xs font-medium text-zinc-950"
                        : "p-1.5 text-muted-foreground hover:text-foreground"
                    }`}
                    title="Preview"
                  >
                    <Globe className="h-4 w-4" />
                    {workspaceMode === "preview" ? <span>Preview</span> : null}
                  </button>
                  <button
                    type="button"
                    onClick={() => setWorkspaceMode("code")}
                    className={`inline-flex items-center gap-1.5 rounded-full transition-colors ${
                      workspaceMode === "code"
                        ? "bg-teal-500 px-3 py-1.5 text-xs font-medium text-zinc-950"
                        : "p-1.5 text-muted-foreground hover:text-foreground"
                    }`}
                    title="Code"
                  >
                    <Code2 className="h-4 w-4" />
                    {workspaceMode === "code" ? <span>Code</span> : null}
                  </button>
                </div>
              ) : null}
            </div>

            <div className="flex items-center gap-1.5">
              <ThemeToggle />
              <Link
                href={"/projects/" + projectId + "/notes"}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-muted/40 px-2.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                title="Open interactive notes"
              >
                <FileText className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Notes</span>
              </Link>
              {isDirty ? <Badge variant="outline" className="border-amber-500/20 bg-amber-500/10 px-2 py-1 text-[10px] text-amber-700 dark:text-amber-300">unsaved code</Badge> : null}
              {viewMode === "workspace" ? (
                <>
                  <Button variant="ghost" size="sm" onClick={refreshPreview} className="h-8 gap-1.5 px-2 text-muted-foreground hover:text-foreground">
                    <RefreshCw className="h-3.5 w-3.5" />
                    Reload
                  </Button>
                  <a href={downloadUrl} download className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-muted/60 px-3 text-sm text-foreground/90 transition-colors hover:bg-muted">
                    <Download className="h-3.5 w-3.5" />
                    Download ZIP
                  </a>
                  <a
                    href={reportUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-teal-500 text-zinc-950 transition-colors hover:bg-teal-400"
                    title="Open new tab"
                  >
                    <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                </>
              ) : null}
            </div>
          </div>

          {viewMode === "chat" ? (
            <div className="relative flex min-h-0 flex-1 flex-col items-center justify-between p-4 md:p-6 overflow-hidden">
              <div ref={workspaceChatScrollRef} data-thread-column className="w-full max-w-3xl flex-1 overflow-y-auto space-y-4 pr-1 no-scrollbar">
                {displayChatMessages.map((message, index) => (
                  <div
                    key={message.id || `${message.time}-${index}`}
                    className={`flex w-full ${message.role === "user" ? "justify-end" : "justify-start"} py-1`}
                  >
                    {message.role === "user" ? (
                      <div
                        data-overview-block
                        data-overview-type="user"
                        data-overview-id={`ws-msg-${message.id || index}`}
                        className="max-w-[75%] rounded-3xl bg-muted/80 px-4.5 py-3 text-base leading-relaxed text-foreground shadow-sm"
                      >
                        <MessageAttachments attachments={message.attachments} className="mb-2" />
                        {message.content}
                      </div>
                    ) : (
                      <WorkspaceAssistantContent
                        content={message.content}
                        reasoning={
                          message.id === "streaming-assistant" && streamingReasoning
                            ? streamingReasoning
                            : typeof message.metadata?.reasoning === "string"
                              ? message.metadata.reasoning
                              : null
                        }
                        reasoningOpen={assistantPending && (message.id === "streaming-assistant" || Boolean(streamingReasoning))}
                      />
                    )}
                  </div>
                ))}

                {assistantPending ? (
                  <div className="flex justify-start py-2">
                    <div className="flex w-full max-w-3xl flex-col gap-2">
                      {streamingReasoning ? (
                        <WorkspaceAssistantContent content="" reasoning={streamingReasoning} reasoningOpen />
                      ) : null}
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin text-teal-500" />
                        {agentActivity}
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>

              <ThreadOverviewRail
                containerRef={workspaceChatScrollRef}
                refreshKey={displayChatMessages.length + "-" + (assistantPending ? "busy" : "idle")}
              />

              {project?.status === "created" ? (
                <div className="w-full max-w-3xl shrink-0 pb-2">
                  {hasUploadedFiles && !assistantPending ? (
                    <div className="flex flex-col items-center gap-1.5">
                      {project.auto_build && !buildError ? (
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <Loader2 className="h-3.5 w-3.5 animate-spin text-teal-500" />
                          {buildPending ? "Starting the build..." : "Preparing the build..."}
                        </div>
                      ) : (
                        <Button
                          type="button"
                          size="sm"
                          onClick={() => void buildNow()}
                          disabled={buildPending}
                          className="gap-2 rounded-full border border-teal-500/40 bg-teal-500/10 px-4 text-teal-800 hover:bg-teal-500/20 dark:text-teal-100"
                        >
                          {buildPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                          Build the report
                        </Button>
                      )}
                      {buildError ? (
                        <p className="text-xs text-red-600 dark:text-red-300">{buildError}</p>
                      ) : null}
                    </div>
                  ) : !hasUploadedFiles && !assistantPending ? (
                    <p className="text-center text-xs leading-5 text-muted-foreground">
                      {project.auto_build
                        ? "Attach study files with +, or import an example dataset — the build will start automatically."
                        : "Attach study files with +, or ask the agent to import an example dataset. Planning starts when you say go."}
                    </p>
                  ) : null}
                </div>
              ) : null}

              <div className="w-full max-w-3xl shrink-0 pt-4">
                <WorkspaceComposer
                  pendingQuestion={pendingQuestion}
                  chatMode={chatMode}
                  disabled={assistantPending}
                  onSend={(message, mode, files) => {
                    setChatMode(mode);
                    void handleSendPrompt(undefined, { message, mode, files });
                  }}
                  onAnswer={answerQuestion}
                  onModeChange={setChatMode}
                />
              </div>
            </div>
          ) : (
            <WorkspaceEditorPanel
              projectId={projectId}
              project={project}
              files={workspaceFiles}
              latestFailedJob={latestFailedJob}
              workspaceMode={workspaceMode}
              resolvedTheme={resolvedTheme}
              iframeKey={iframeKey}
              reportUrl={reportUrl}
              hasPreview={hasPreview}
              isFailed={isFailed}
              isLive={isLive}
            />
          )}
        </div>
      </main>
    </div>
  );
}
