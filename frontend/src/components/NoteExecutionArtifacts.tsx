"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Download, FileImage, FileText, Loader2 } from "lucide-react";

import { api, NoteExecutionArtifact } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface NoteExecutionArtifactsProps {
  artifacts: NoteExecutionArtifact[];
  threadId: string;
  cellId: string;
  executionId: string;
  workspaceId?: string;
  open: boolean;
}

function artifactFileName(artifact: NoteExecutionArtifact): string {
  const segments = artifact.relative_path.split("/");
  return segments[segments.length - 1] || artifact.artifact_type;
}

function isImageArtifact(artifact: NoteExecutionArtifact): boolean {
  return artifact.artifact_type === "image" || (artifact.mime_type || "").startsWith("image/");
}

function isTableArtifact(artifact: NoteExecutionArtifact): boolean {
  return artifact.artifact_type === "table";
}

function isHtmlArtifact(artifact: NoteExecutionArtifact): boolean {
  return artifact.artifact_type === "html";
}

function isConsoleArtifact(artifact: NoteExecutionArtifact): boolean {
  return artifact.artifact_type === "console";
}

export function parseDelimited(text: string, delimiter: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === delimiter) {
      row.push(field);
      field = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i += 1;
      row.push(field);
      field = "";
      if (row.some((cell) => cell.length)) rows.push(row);
      row = [];
    } else {
      field += ch;
    }
  }
  row.push(field);
  if (row.some((cell) => cell.length)) rows.push(row);
  return rows;
}

const TABLE_PREVIEW_ROWS = 21;

export function TablePreview({ url, mimeType }: { url: string; mimeType: string }) {
  const [rows, setRows] = useState<string[][] | null>(null);
  const [totalRows, setTotalRows] = useState(0);
  useEffect(() => {
    let cancelled = false;
    void fetch(url)
      .then((response) => response.text())
      .then((text) => {
        if (cancelled) return;
        const delimiter = (mimeType || "").includes("tab") ? "\t" : ",";
        const all = parseDelimited(text, delimiter);
        setTotalRows(all.length);
        setRows(all.slice(0, TABLE_PREVIEW_ROWS));
      })
      .catch(() => {
        if (!cancelled) setRows([]);
      });
    return () => {
      cancelled = true;
    };
  }, [url, mimeType]);
  if (!rows) {
    return <span className="inline-flex items-center gap-1.5"><Loader2 className="h-3 w-3 animate-spin" /> Loading preview…</span>;
  }
  if (!rows.length) return null;
  const header = rows[0];
  const body = rows.slice(1);
  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-background">
      <table className="w-full border-collapse text-left text-xs">
        <thead>
          <tr className="border-b border-border bg-muted/80">
            {header.map((cell, index) => (
              <th key={index} className="px-2.5 py-1.5 font-semibold text-foreground">{cell}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border/50">
          {body.map((cells, rowIndex) => (
            <tr key={rowIndex} className="transition-colors hover:bg-muted/40">
              {cells.map((cell, cellIndex) => (
                <td key={cellIndex} className="max-w-52 truncate px-2.5 py-1.5 text-foreground/90">{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {totalRows > body.length ? (
        <div className="border-t border-border px-2.5 py-1 text-[11px] text-muted-foreground">Showing first {body.length} of {totalRows} rows</div>
      ) : null}
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function fetchArtifactBlob(props: {
  workspaceId?: string;
  threadId: string;
  cellId: string;
  executionId: string;
  artifactId: string;
}): Promise<Blob> {
  return props.workspaceId
    ? api.getNoteExecutionArtifactContent(props.workspaceId, props.threadId, props.cellId, props.executionId, props.artifactId)
    : api.getStandaloneNoteExecutionArtifactContent(props.threadId, props.cellId, props.executionId, props.artifactId);
}

export function NoteExecutionArtifacts({ artifacts, threadId, cellId, executionId, workspaceId, open }: NoteExecutionArtifactsProps) {
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [downloading, setDownloading] = useState<string | null>(null);
  const revokeRef = useRef<string[]>([]);

  const eagerArtifacts = useMemo(
    () => artifacts.filter((artifact) => !isConsoleArtifact(artifact)),
    [artifacts],
  );

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const current: string[] = [];
    void Promise.all(
      eagerArtifacts.map(async (artifact) => {
        try {
          const blob = await fetchArtifactBlob({ workspaceId, threadId, cellId, executionId, artifactId: artifact.id });
          if (cancelled) return;
          const url = URL.createObjectURL(blob);
          current.push(url);
          setUrls((prev) => ({ ...prev, [artifact.id]: url }));
        } catch (error) {
          if (cancelled) return;
          setErrors((prev) => ({ ...prev, [artifact.id]: error instanceof Error ? error.message : "Unavailable" }));
        }
      }),
    );
    return () => {
      cancelled = true;
      revokeRef.current.forEach((url) => URL.revokeObjectURL(url));
      revokeRef.current = [];
    };
  }, [open, eagerArtifacts, threadId, cellId, executionId, workspaceId]);

  useEffect(() => {
    revokeRef.current = Object.values(urls);
  }, [urls]);

  const downloadArtifact = async (artifact: NoteExecutionArtifact) => {
    if (downloading) return;
    setDownloading(artifact.id);
    try {
      const blob = await fetchArtifactBlob({ workspaceId, threadId, cellId, executionId, artifactId: artifact.id });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = artifactFileName(artifact);
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (error) {
      setErrors((prev) => ({ ...prev, [artifact.id]: error instanceof Error ? error.message : "Unavailable" }));
    } finally {
      setDownloading(null);
    }
  };

  if (!artifacts.length) return null;

  const downloadIcon = (artifact: NoteExecutionArtifact) => (
    <Tooltip>
      <TooltipTrigger render={
        <Button size="icon-xs" variant="ghost" aria-label={"Download " + artifactFileName(artifact)} onClick={() => void downloadArtifact(artifact)} disabled={downloading === artifact.id}>
          {downloading === artifact.id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
        </Button>
      } />
      <TooltipContent>Download {artifactFileName(artifact)}</TooltipContent>
    </Tooltip>
  );

  if (!open) return null;

  return (
    <div
      data-overview-block
      data-overview-type="file"
      data-overview-id={`${executionId}-files`}
      className="mb-2"
    >
        <div className="rounded-lg border border-border bg-background/60 px-2.5 py-2 text-xs text-muted-foreground">
      <div className="space-y-1">
        {artifacts.map((artifact) => {
          const url = urls[artifact.id];
          const error = errors[artifact.id];
          const name = artifactFileName(artifact);
          if (isImageArtifact(artifact)) {
            return (
              <div key={artifact.id} className="flex flex-col gap-1.5">
                {url ? (
                  <a href={url} target="_blank" rel="noreferrer" title={name}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={url} alt={name} className="h-auto w-full rounded-lg border border-border bg-background object-contain" />
                  </a>
                ) : error ? (
                  <span className="text-red-700 dark:text-red-300">{name} · {error}</span>
                ) : (
                  <span className="inline-flex items-center gap-1.5"><Loader2 className="h-3 w-3 animate-spin" /> {name}</span>
                )}
                <div className="flex items-center gap-x-2 gap-y-0.5">
                  <FileImage className="h-3 w-3" />
                  <span className="truncate">{name}</span>
                  <span>{formatBytes(artifact.byte_size)}</span>
                  {url ? downloadIcon(artifact) : null}
                </div>
              </div>
            );
          }
          if (isTableArtifact(artifact)) {
            return (
              <div key={artifact.id} className="flex flex-col gap-1.5">
                {url ? (
                  <TablePreview url={url} mimeType={artifact.mime_type || ""} />
                ) : error ? (
                  <span className="text-red-700 dark:text-red-300">{name} · {error}</span>
                ) : (
                  <span className="inline-flex items-center gap-1.5"><Loader2 className="h-3 w-3 animate-spin" /> {name}</span>
                )}
                <div className="flex items-center gap-x-2 gap-y-0.5">
                  <FileText className="h-3 w-3" />
                  <span className="truncate">{name}</span>
                  <span>{formatBytes(artifact.byte_size)}</span>
                  {url ? downloadIcon(artifact) : null}
                </div>
              </div>
            );
          }
          if (isHtmlArtifact(artifact)) {
            return (
              <div key={artifact.id} className="flex flex-col gap-1.5">
                {url ? (
                  <iframe src={url} title={name} className="h-64 w-full rounded-lg border border-border bg-white" sandbox="" />
                ) : error ? (
                  <span className="text-red-700 dark:text-red-300">{name} · {error}</span>
                ) : (
                  <span className="inline-flex items-center gap-1.5"><Loader2 className="h-3 w-3 animate-spin" /> {name}</span>
                )}
                <div className="flex items-center gap-x-2 gap-y-0.5">
                  <FileText className="h-3 w-3" />
                  <span className="truncate">{name}</span>
                  <span>{formatBytes(artifact.byte_size)}</span>
                  {url ? downloadIcon(artifact) : null}
                </div>
              </div>
            );
          }
          return (
            <div key={artifact.id} className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
              <FileText className="h-3 w-3" />
              <span className="truncate">{name}</span>
              <span>{formatBytes(artifact.byte_size)}</span>
              {isConsoleArtifact(artifact) ? (
                error ? <span className="text-red-700 dark:text-red-300">{error}</span> : downloadIcon(artifact)
              ) : url ? (
                downloadIcon(artifact)
              ) : error ? (
                <span className="text-red-700 dark:text-red-300">{error}</span>
              ) : (
                <Loader2 className="h-3 w-3 animate-spin" />
              )}
            </div>
          );
        })}
      </div>
      </div>
    </div>
  );
}
