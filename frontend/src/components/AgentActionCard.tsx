"use client";

import { AlertCircle, CheckCircle2, FileCode, Loader2, MessageSquare, Wrench } from "lucide-react";
import { Button } from "@/components/ui/button";

export type ActionEventStatus = "pending" | "running" | "ok" | "error";

export interface ActionEvent {
  id: string;
  kind: "tool" | "action" | "job" | "apply" | "error";
  status: ActionEventStatus;
  title: string;
  summary: string;
  target?: {
    path?: string | null;
    job_id?: string | null;
    recipe_id?: string | null;
    tool?: string | null;
    action?: string | null;
  };
  log_excerpt?: string | null;
  diff?: string | null;
  cta?: {
    label: string;
    prompt: string;
  } | null;
}

export function AgentActionCard({
  event,
  onAskAgent,
  onOpenPath,
}: {
  event: ActionEvent;
  onAskAgent?: (prompt: string) => void;
  onOpenPath?: (path: string) => void;
}) {
  const tone =
    event.status === "error"
      ? "border-red-500/30 bg-red-500/5 text-red-800 dark:text-red-100"
      : event.status === "ok"
        ? "border-emerald-500/25 bg-emerald-500/5 text-foreground"
        : event.status === "running" || event.status === "pending"
          ? "border-teal-500/25 bg-teal-500/5 text-foreground"
          : "border-border bg-muted/60 text-foreground";

  const Icon =
    event.status === "error"
      ? AlertCircle
      : event.status === "running" || event.status === "pending"
        ? Loader2
        : event.kind === "apply"
          ? FileCode
          : event.kind === "action"
            ? Wrench
            : CheckCircle2;

  return (
    <div className={`rounded-2xl border px-3 py-2 text-xs leading-5 ${tone}`}>
      <div className="flex items-start gap-2">
        <Icon
          className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${
            event.status === "running" || event.status === "pending" ? "animate-spin text-teal-400" : ""
          }`}
        />
        <div className="min-w-0 flex-1">
          <div className="font-medium">{event.title}</div>
          <div className="mt-0.5 text-muted-foreground">{event.summary}</div>
          {event.target?.path ? (
            <button
              type="button"
              className="mt-1 text-[11px] text-teal-600 underline-offset-2 hover:underline dark:text-teal-300"
              onClick={() => onOpenPath?.(event.target!.path!)}
            >
              {event.target.path}
            </button>
          ) : null}
          {event.log_excerpt ? (
            <pre className="mt-2 max-h-24 overflow-auto whitespace-pre-wrap rounded-lg bg-black/5 p-2 text-[10px] text-muted-foreground dark:bg-white/5">
              {event.log_excerpt}
            </pre>
          ) : null}
          {event.diff ? (
            <pre className="mt-2 max-h-40 overflow-auto whitespace-pre rounded-lg bg-zinc-950/90 p-2 font-mono text-[10px] text-zinc-100">
              {event.diff}
            </pre>
          ) : null}
          {event.cta && onAskAgent ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="mt-2 h-7 gap-1.5 rounded-lg border-red-400/40 bg-white/80 px-2 text-[11px] text-red-800 hover:bg-red-50 dark:bg-zinc-950/40 dark:text-red-100"
              onClick={() => onAskAgent(event.cta!.prompt)}
            >
              <MessageSquare className="h-3 w-3" />
              {event.cta.label}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export function jobFailureToActionEvent(job: {
  id: string;
  job_type?: string | null;
  error?: string | null;
}): ActionEvent {
  const excerpt = (job.error || "The job failed without an error message.").slice(0, 2500);
  return {
    id: `job-fail-${job.id}`,
    kind: "error",
    status: "error",
    title: `${job.job_type || "job"} failed`,
    summary: "Ask the agent to inspect and repair this failure.",
    target: { job_id: job.id },
    log_excerpt: excerpt,
    cta: {
      label: "Ask agent to fix",
      prompt: `Fix this ${job.job_type || "job"} failure:\n\n\`\`\`\n${excerpt}\n\`\`\``,
    },
  };
}

export interface ApplyResult {
  ok?: boolean;
  strategy?: string;
  reason?: string;
  diagnostics?: string[];
  path?: string;
  diff?: string | null;
  hint?: string | null;
}

export function applyResultsToActionEvents(
  applyResults: ApplyResult[],
  sourceId: string,
): ActionEvent[] {
  return applyResults.map((item, index) => ({
    id: `${sourceId}-apply-${index}`,
    kind: "apply",
    status: item.ok ? "ok" : "error",
    title: item.ok ? `Applied ${item.strategy || "edit"}` : `Apply failed (${item.strategy || "none"})`,
    summary: item.reason || item.diagnostics?.[0] || item.path || "File edit",
    target: { path: item.path },
    diff: item.diff || null,
    log_excerpt: item.hint || (item.diagnostics || []).join("\n") || null,
  }));
}
