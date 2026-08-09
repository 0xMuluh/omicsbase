"use client";

import { useMemo } from "react";
import { AlertTriangle, FileImage, FileText, Loader2, XCircle } from "lucide-react";

import type { NoteExecutionArtifact } from "@/lib/api/types/notes";
import { TablePreview } from "./NoteExecutionArtifacts";

interface ExecutionBlock {
  seq: number;
  type: string;
  content?: string;
  path?: string;
  rows?: number;
  cols?: number;
}

interface ExecutionBlocksProps {
  events: ExecutionBlock[];
  artifacts: NoteExecutionArtifact[];
  executionId: string;
  artifactUrls: Record<string, string>;
  artifactErrors: Record<string, string>;
}

export function ExecutionBlocks({ events, artifacts, executionId, artifactUrls, artifactErrors }: ExecutionBlocksProps) {
  const artifactsByPath = useMemo(() => {
    const map: Record<string, NoteExecutionArtifact> = {};
    for (const artifact of artifacts) {
      map[artifact.relative_path] = artifact;
    }
    return map;
  }, [artifacts]);

  const blocks = useMemo(() => {
    const grouped: ExecutionBlock[] = [];
    for (const event of [...events].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0))) {
      if (event.type === "text") {
        const last = grouped[grouped.length - 1];
        if (last && last.type === "text") {
          last.content = (last.content || "") + (event.content || "");
        } else {
          grouped.push({ ...event });
        }
      } else {
        grouped.push({ ...event });
      }
    }
    return grouped;
  }, [events]);

  const artifactName = (path: string) => path.split("/").filter(Boolean).pop() || "file";

  const formatBytes = (bytes: number) => {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  };

  return (
    <div className="space-y-2">
      {blocks.map((block) => {
        if (block.type === "text") {
          return (
            <pre key={block.seq} className="whitespace-pre-wrap rounded-lg border border-border bg-background p-2 font-mono text-sm leading-6 text-muted-foreground">
              {block.content}
            </pre>
          );
        }
        if (block.type === "warning") {
          return (
            <div
              key={block.seq}
              data-overview-block
              data-overview-type="warning"
              data-overview-id={`${executionId}-${block.seq}-warning`}
              className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-2.5 py-2 font-mono text-xs leading-5 text-amber-700 dark:text-amber-300"
            >
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span className="whitespace-pre-wrap">{block.content}</span>
            </div>
          );
        }
        if (block.type === "error") {
          return (
            <div
              key={block.seq}
              data-overview-block
              data-overview-type="error"
              data-overview-id={`${executionId}-${block.seq}-error`}
              className="flex items-start gap-2 rounded-lg border border-rose-500/40 bg-rose-500/10 px-2.5 py-2 font-mono text-xs leading-5 text-red-700 dark:text-red-300"
            >
              <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span className="whitespace-pre-wrap">{block.content}</span>
            </div>
          );
        }
        if (block.type === "table" && block.path) {
          const artifact = artifactsByPath[block.path];
          const url = artifact ? artifactUrls[artifact.id] : undefined;
          const error = artifact ? artifactErrors[artifact.id] : undefined;
          return (
            <div
              key={block.seq}
              data-overview-block
              data-overview-type="table"
              data-overview-id={`${executionId}-${block.seq}-table`}
              className="space-y-1"
            >
              {url ? (
                <TablePreview url={url} mimeType={artifact?.mime_type || ""} />
              ) : error ? (
                <span className="text-xs text-red-700 dark:text-red-300">{error}</span>
              ) : (
                <div className="flex items-center gap-2 rounded-lg border border-border bg-background/60 px-2.5 py-2 text-xs text-muted-foreground">
                  <FileText className="h-3.5 w-3.5" />
                  <span>Table{block.rows != null ? ` · ${block.rows} rows × ${block.cols ?? "?"} cols` : ""}</span>
                  {artifact ? <span className="truncate">{artifactName(artifact.relative_path)}</span> : null}
                  {!url ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
                </div>
              )}
            </div>
          );
        }
        if (block.type === "plot" && block.path) {
          const artifact = artifactsByPath[block.path];
          const url = artifact ? artifactUrls[artifact.id] : undefined;
          const error = artifact ? artifactErrors[artifact.id] : undefined;
          return (
            <div
              key={block.seq}
              data-overview-block
              data-overview-type="plot"
              data-overview-id={`${executionId}-${block.seq}-plot`}
              className="space-y-1"
            >
              {url ? (
                <>
                  <a href={url} target="_blank" rel="noreferrer" title={artifactName(block.path)}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={url} alt={artifactName(block.path)} className="h-auto w-full rounded-lg border border-border bg-background object-contain" />
                  </a>
                  <div className="flex items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground">
                    <FileImage className="h-3 w-3" />
                    <span className="truncate">{artifactName(block.path)}</span>
                    {artifact ? <span>{formatBytes(artifact.byte_size)}</span> : null}
                  </div>
                </>
              ) : error ? (
                <span className="text-xs text-red-700 dark:text-red-300">{error}</span>
              ) : (
                <div className="flex items-center gap-2 rounded-lg border border-border bg-background/60 px-2.5 py-2 text-xs text-muted-foreground">
                  <FileImage className="h-3.5 w-3.5" />
                  <span className="truncate">{artifactName(block.path)}</span>
                  {!url ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
                </div>
              )}
            </div>
          );
        }
        return null;
      })}
    </div>
  );
}
