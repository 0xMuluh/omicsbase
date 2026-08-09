"use client";

import type { RefObject } from "react";
import { Check, Code2, Eye, FileText, Loader2, Moon, Pencil, Play, Save, Sparkles, Square, Sun } from "lucide-react";

import type { NoteCell, NoteCellExecution, NoteDataFile, NoteExecutionArtifact, NoteThread } from "@/lib/api/types/notes";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { MarkdownRenderer } from "@/components/MarkdownRenderer";
import { MessageAttachments } from "@/components/MessageAttachments";
import { CodeBlock } from "@/components/CodeBlock";
import { ExecutionCard } from "./ExecutionCard";
import { useCodeTheme } from "@/lib/use-code-theme";
import { cellTypeLabel, getRevisionAttachments, latestRevision } from "../lib/noteCellUtils";

type ResultAction = "history" | "files";
type ResultActions = Record<string, { history?: boolean; files?: boolean }>;

function cellTypeIcon(type: NoteCell["revisions"][number]["cell_type"]) {
  if (type === "code") return <Code2 className="h-3.5 w-3.5" />;
  if (type === "agent") return <Sparkles className="h-3.5 w-3.5" />;
  if (type === "output") return <Check className="h-3.5 w-3.5" />;
  return <FileText className="h-3.5 w-3.5" />;
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

function TypingDots({ className = "" }: { className?: string }) {
  return (
    <span className={"flex items-center gap-1 " + className}>
      <span className="h-1.5 w-1.5 rounded-full bg-current typing-dot" style={{ animationDelay: "0ms" }} />
      <span className="h-1.5 w-1.5 rounded-full bg-current typing-dot" style={{ animationDelay: "180ms" }} />
      <span className="h-1.5 w-1.5 rounded-full bg-current typing-dot" style={{ animationDelay: "360ms" }} />
    </span>
  );
}

export function NoteCellList({
  thread,
  threadFiles,
  selectedThreadId,
  workspaceId,
  requestError,
  drafts,
  editingCellId,
  activeExecution,
  executionData,
  executionLoading,
  resultActions,
  downloadingResult,
  liveTurnText,
  turnStreaming,
  turnStatus,
  threadBottomRef,
  onEditCell,
  onDraftChange,
  onSaveCell,
  savePending,
  onExecuteCell,
  executePending,
  onCancelExecution,
  cancelPending,
  onToggleResultAction,
  onDownloadArtifact,
}: {
  thread: NoteThread;
  threadFiles: NoteDataFile[];
  selectedThreadId: string;
  workspaceId?: string;
  requestError: string | null;
  drafts: Record<string, string>;
  editingCellId: string | null;
  activeExecution: { cellId: string; executionId: string } | null;
  executionData?: NoteCellExecution;
  executionLoading: boolean;
  resultActions: ResultActions;
  downloadingResult: string | null;
  liveTurnText: string;
  turnStreaming: boolean;
  turnStatus: string | null;
  threadBottomRef: RefObject<HTMLDivElement | null>;
  onEditCell: (cellId: string | null) => void;
  onDraftChange: (cellId: string, content: string) => void;
  onSaveCell: (cell: NoteCell) => void;
  savePending: boolean;
  onExecuteCell: (cell: NoteCell) => void;
  executePending: boolean;
  onCancelExecution: () => void;
  cancelPending: boolean;
  onToggleResultAction: (executionId: string, action: ResultAction) => void;
  onDownloadArtifact: (cellId: string, execution: NoteCellExecution, artifact: NoteExecutionArtifact) => void;
}) {
  const [codeTheme, setCodeTheme] = useCodeTheme();
  const liveTurnBlock =
    turnStreaming && liveTurnText ? (
      <article
        data-overview-block
        data-overview-type="assistant"
        data-overview-id={selectedThreadId + "-live-turn"}
        className="relative w-full py-1"
      >
        <MarkdownRenderer content={liveTurnText} className="text-base" />
      </article>
    ) : null;

  return (
    <div data-thread-column className="mx-auto w-full max-w-4xl p-4 pb-8 md:p-6">
      {requestError ? <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-red-700 dark:text-red-300">{requestError}</div> : null}

      <div className="space-y-4">
        {thread.cells.map((cell, index) => {
          const revision = latestRevision(cell);
          if (!revision) return null;
          const editable = ["markdown", "agent", "code"].includes(revision.cell_type);
          const isNarrative = revision.cell_type === "markdown" || revision.cell_type === "agent";
          const isQuestion = revision.cell_type === "agent";
          const isEditing = editingCellId === cell.id;
          const draft = drafts[cell.id] ?? revision.content;
          const changed = draft !== revision.content;
          const revisionAttachments = getRevisionAttachments(revision.metadata);
          const persistedExecution = cell.latest_execution && cell.latest_execution.revision_id === revision.id ? cell.latest_execution : null;
          const execution = activeExecution && activeExecution.cellId === cell.id ? (executionData || persistedExecution) : persistedExecution;
          const executionBusy = Boolean(execution && ["queued", "running", "cancel_requested"].includes(execution.status));
          const canRun = revision.cell_type === "code" && !changed && thread.status === "active";

          return (
            <article
              key={cell.id}
              data-overview-block
              data-overview-type={revision.cell_type === "agent" ? "user" : revision.cell_type === "markdown" ? "assistant" : "code"}
              data-overview-id={cell.id}
              className={isQuestion ? "flex justify-end" : isNarrative ? "group relative" : revision.cell_type === "code" ? executionContainerClass(execution) : "overflow-hidden rounded-xl border border-border bg-card shadow-sm"}
            >
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
                          <Button size="xs" variant="outline" onClick={onCancelExecution} disabled={cancelPending}>
                            {cancelPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Square className="h-3 w-3" />} Cancel
                          </Button>
                        ) : (
                          <Button size="xs" variant="outline" onClick={() => onExecuteCell(cell)} disabled={!canRun || executePending || (activeExecution?.cellId === cell.id && executionLoading)} title={changed ? "Save this revision before running" : "Run this persisted revision"}>
                            {executePending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />} Run
                          </Button>
                        )}
                      </div>
                    ) : null}
                    {isNarrative || revision.cell_type === "code" ? (
                      <Tooltip>
                        <TooltipTrigger render={
                          <Button size="icon-xs" variant="ghost" aria-label={isEditing ? "Preview" : "Edit"} onClick={() => onEditCell(isEditing ? null : cell.id)}>
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
                      <Button size="xs" variant="outline" onClick={() => onSaveCell(cell)} disabled={savePending}>
                        {savePending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />} Save revision
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
                      onChange={(event) => onDraftChange(cell.id, event.target.value)}
                      className="min-h-28 resize-y border-0 bg-transparent p-1 leading-6 shadow-none focus-visible:ring-0"
                      placeholder={revision.cell_type === "agent" ? "Describe what OmicsBase should investigate." : "Write a note..."}
                    />
                  ) : (
                    <>
                      {revisionAttachments.length ? (
                        <MessageAttachments attachments={revisionAttachments} className="mb-2" />
                      ) : index === 0 && threadFiles.length ? (
                        <MessageAttachments attachments={threadFiles} className="mb-2" />
                      ) : null}
                      <MarkdownRenderer content={revision.content || "(empty note)"} className="text-base" />
                    </>
                  )
                ) : revision.cell_type === "code" ? (
                  isEditing ? (
                    <Textarea
                      value={draft}
                      onChange={(event) => onDraftChange(cell.id, event.target.value)}
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
                  <Button size="icon-xs" variant="ghost" className="absolute right-0 top-0 opacity-0 transition-opacity group-hover:opacity-100" aria-label="Edit" onClick={() => onEditCell(cell.id)}>
                    <Pencil className="h-3 w-3" />
                  </Button>
                ) : null}
              </div>
              {activeExecution?.cellId === cell.id && executionLoading && !execution ? (
                <div className="border-t border-border px-3 py-2 text-[11px] text-muted-foreground">Loading execution status...</div>
              ) : null}
              {execution ? (
                <ExecutionCard
                  execution={execution}
                  threadId={selectedThreadId}
                  cellId={cell.id}
                  executionBusy={executionBusy}
                  workspaceId={workspaceId}
                  actions={resultActions[execution.id] || {}}
                  onToggleAction={(action) => onToggleResultAction(execution.id, action)}
                  downloadingArtifactId={downloadingResult}
                  onDownloadArtifact={(artifact) => onDownloadArtifact(cell.id, execution, artifact)}
                />
              ) : null}
              {cell.revisions.length > 1 ? <div className="border-t border-border px-3 py-2 text-[10px] text-muted-foreground">{cell.revisions.length} immutable revisions are retained for provenance.</div> : null}
            </article>
          );
        })}
      </div>
      {liveTurnBlock}
      <div ref={threadBottomRef} className="h-px" />
      {turnStreaming ? (
        <div
          data-overview-block
          data-overview-type="status"
          data-overview-id={selectedThreadId + "-status"}
          className="flex items-center gap-2 px-1 pb-2 text-sm text-muted-foreground"
        >
          <TypingDots className="text-teal-500" />
          <span>{turnStatus || "Thinking"}</span>
        </div>
      ) : null}
    </div>
  );
}
