"use client";

import { Loader2, Table2 } from "lucide-react";

import type { WorkspaceResult } from "@/lib/api/types/projects";

export function NoteResultPicker({
  results,
  onClose,
  onPick,
}: {
  results: WorkspaceResult[] | null;
  onClose: () => void;
  onPick: (path: string) => void;
}) {
  return (
    <>
      <div className="fixed inset-0 z-20" onClick={onClose} />
      <div className="absolute bottom-16 left-2 z-30 max-h-56 w-72 overflow-y-auto rounded-xl border border-border bg-popover p-1.5 text-sm shadow-xl">
        <p className="px-2.5 py-1.5 text-xs font-medium text-muted-foreground">
          Workspace results
        </p>
        {results === null ? (
          <p className="flex items-center gap-2 px-2.5 py-2 text-xs text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" /> Loading...
          </p>
        ) : results.length === 0 ? (
          <p className="px-2.5 py-2 text-xs text-muted-foreground">
            No result tables yet. Build the report or run notebook cells first.
          </p>
        ) : (
          results.map((result) => (
            <button
              key={result.path}
              type="button"
              onClick={() => onPick(result.path)}
              className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-foreground hover:bg-muted"
              title={result.path}
            >
              <Table2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="truncate">{result.name}</span>
            </button>
          ))
        )}
      </div>
    </>
  );
}
