"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { createNoteScope } from "@/features/notes/api/noteScope";

interface ExecutionHistoryProps {
  threadId: string;
  cellId: string;
  executionId: string;
  workspaceId?: string;
  polling?: boolean;
  open: boolean;
}

export function ExecutionHistory({ threadId, cellId, executionId, workspaceId, polling, open }: ExecutionHistoryProps) {
  const scope = useMemo(() => createNoteScope({ workspaceId }), [workspaceId]);
  const eventsQuery = useQuery({
    queryKey: ["note-cell-execution-events", workspaceId || "standalone", threadId, cellId, executionId],
    queryFn: () => scope.listExecutionEvents(threadId, cellId, executionId),
    enabled: Boolean(threadId && cellId && executionId),
    refetchInterval: polling ? 1500 : false,
  });
  const events = eventsQuery.data || [];
  if (!open || !events.length) return null;
  return (
    <div className="mb-2 rounded-lg border border-border bg-background/60 px-2.5 py-2 text-xs text-muted-foreground">
      <div className="space-y-1">
        {events.map((event) => (
          <div key={event.id} className="flex flex-wrap items-center justify-between gap-2">
            <span>#{event.sequence} {event.event_type.replace("note_execution_", "").replace(/_/g, " ")}</span>
            <span className="capitalize">{event.status.replace(/_/g, " ")} · {event.created_at.slice(11, 19)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
