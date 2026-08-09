"use client";

import { useCallback, useMemo } from "react";
import { Download, Files, History, Loader2 } from "lucide-react";

import type { NoteCellExecution, NoteExecutionArtifact } from "@/lib/api/types/notes";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ExecutionBlocks } from "./ExecutionBlocks";
import { ExecutionHistory } from "./ExecutionHistory";
import { ExecutionOutput } from "./ExecutionOutput";
import { NoteExecutionArtifacts } from "./NoteExecutionArtifacts";
import { createNoteScope } from "../api/noteScope";
import { useExecutionArtifacts } from "../hooks/useExecutionArtifacts";

interface ExecutionCardProps {
  execution: NoteCellExecution;
  threadId: string;
  cellId: string;
  executionBusy: boolean;
  workspaceId?: string;
  actions: { history?: boolean; files?: boolean };
  onToggleAction: (action: "history" | "files") => void;
  downloadingArtifactId: string | null;
  onDownloadArtifact: (artifact: NoteExecutionArtifact) => void;
}

interface ExecutionBlock {
  seq: number;
  type: string;
  content?: string;
  path?: string;
  rows?: number;
  cols?: number;
}


export function ExecutionCard({
  execution,
  threadId,
  cellId,
  executionBusy,
  workspaceId,
  actions,
  onToggleAction,
  downloadingArtifactId,
  onDownloadArtifact,
}: ExecutionCardProps) {
  const events = (execution.result_metadata?.events || []) as ExecutionBlock[];
  const hasBlocks = events.some((event) => ["text", "table", "plot", "warning", "error"].includes(String(event.type || "")));
  const inlineKinds = new Set(["table", "image"]);
  const filesArtifacts = (execution.artifacts || []).filter((artifact) => !inlineKinds.has(artifact.artifact_type));
  const imageArtifacts = (execution.artifacts || []).filter(
    (artifact) => artifact.artifact_type === "image" || (artifact.mime_type || "").startsWith("image/"),
  );
  const scope = useMemo(() => createNoteScope({ workspaceId }), [workspaceId]);
  const fetchArtifact = useCallback(
    (artifactId: string) => scope.getArtifactContent(threadId, cellId, execution.id, artifactId),
    [cellId, execution.id, scope, threadId],
  );
  const artifactState = useExecutionArtifacts({
    artifacts: execution.artifacts || [],
    enabled: hasBlocks || Boolean(actions.files),
    fetchArtifact,
  });

  const downloadButton = (artifact: NoteExecutionArtifact) => {
    const name = artifact.relative_path.split("/").filter(Boolean).pop() || "plot.png";
    return (
      <Tooltip key={artifact.id}>
        <TooltipTrigger render={
          <Button
            size="icon-xs"
            variant="ghost"
            aria-label={`Download ${name}`}
            onClick={() => onDownloadArtifact(artifact)}
            disabled={downloadingArtifactId === artifact.id}
          >
            {downloadingArtifactId === artifact.id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
          </Button>
        } />
        <TooltipContent>Download {name}</TooltipContent>
      </Tooltip>
    );
  };

  return (
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
          {hasBlocks ? (
            <ExecutionBlocks
              events={events}
              artifacts={execution.artifacts || []}
              executionId={execution.id}
              artifactUrls={artifactState.urls}
              artifactErrors={artifactState.errors}
            />
          ) : execution.result_metadata?.stdout_preview ? (
            <div data-overview-block data-overview-type="result" data-overview-id={`${execution.id}-result`}>
              <ExecutionOutput
                stdout={String(execution.result_metadata.stdout_preview)}
                truncated={Boolean(execution.result_metadata.output_truncated)}
              />
            </div>
          ) : null}

          <div className="mb-2 flex items-center gap-1">
            <Tooltip>
              <TooltipTrigger render={
                <Button size="icon-xs" variant="ghost" aria-label="Execution history" onClick={() => onToggleAction("history")}>
                  <History className="h-3 w-3" />
                </Button>
              } />
              <TooltipContent>Execution history</TooltipContent>
            </Tooltip>
            {filesArtifacts.length > 0 ? (
              <Tooltip>
                <TooltipTrigger render={
                  <Button size="icon-xs" variant="ghost" aria-label="Output files" onClick={() => onToggleAction("files")}>
                    <Files className="h-3 w-3" />
                  </Button>
                } />
                <TooltipContent>Output files ({filesArtifacts.length})</TooltipContent>
              </Tooltip>
            ) : null}
            {hasBlocks ? imageArtifacts.map(downloadButton) : null}
          </div>

          {actions.history ? (
            <ExecutionHistory
              threadId={threadId}
              cellId={cellId}
              executionId={execution.id}
              workspaceId={workspaceId}
              polling={executionBusy}
              open
            />
          ) : null}
          {actions.files && filesArtifacts.length > 0 ? (
            <NoteExecutionArtifacts
              artifacts={filesArtifacts}
              executionId={execution.id}
              artifactUrls={artifactState.urls}
              artifactErrors={artifactState.errors}
              downloadingArtifactId={downloadingArtifactId}
              onDownloadArtifact={onDownloadArtifact}
              open
            />
          ) : null}
          {execution.error ? <p className="mt-2 whitespace-pre-wrap font-mono text-xs text-red-700 dark:text-red-300">{execution.error}</p> : null}
        </>
      )}
    </div>
  );
}
