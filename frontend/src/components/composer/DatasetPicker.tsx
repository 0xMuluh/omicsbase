"use client";

import { Database, Loader2, X } from "lucide-react";

import type { ImportableDataset } from "@/lib/api/types/projects";

export function DatasetPicker({
  datasets,
  onClose,
  onPick,
  pending = false,
  placement = "bottom",
  selected,
}: {
  datasets: ImportableDataset[] | null;
  onClose: () => void;
  onPick: (dataset: ImportableDataset) => void;
  pending?: boolean;
  placement?: "top" | "bottom";
  selected?: ImportableDataset | null;
}) {
  const panelClass = placement === "top"
    ? "absolute left-0 top-[calc(100%+8px)] z-30 w-full overflow-hidden rounded-2xl border border-border bg-[var(--composer-elevated)] p-2 shadow-2xl"
    : "absolute bottom-16 left-2 z-30 max-h-64 w-80 overflow-y-auto rounded-xl border border-border bg-popover p-1.5 text-sm shadow-xl";

  return (
    <>
      <div className="fixed inset-0 z-20" onClick={onClose} />
      <div className={panelClass}>
        <div className="flex items-center justify-between px-2.5 py-1.5">
          <p className="text-xs font-medium text-muted-foreground">Import example dataset</p>
          <button type="button" className="rounded-full p-1 text-muted-foreground hover:bg-muted hover:text-foreground" onClick={onClose} aria-label="Close dataset picker">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
        {datasets === null ? (
          <p className="flex items-center gap-2 px-2.5 py-2 text-xs text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" /> Loading...
          </p>
        ) : datasets.length === 0 ? (
          <p className="px-2.5 py-2 text-xs text-muted-foreground">No example datasets available.</p>
        ) : (
          <div className="max-h-48 space-y-1 overflow-y-auto">
            {datasets.map((dataset) => {
              const isSelected = selected?.package === dataset.package && selected?.dataset === dataset.dataset;
              return (
                <button
                  key={dataset.package + "::" + dataset.dataset}
                  type="button"
                  disabled={pending}
                  onClick={() => onPick(dataset)}
                  className={"flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left text-foreground hover:bg-muted " + (isSelected ? "bg-teal-500/10" : "")}
                >
                  <Database className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="min-w-0">
                    <span className="block truncate text-xs font-medium">{dataset.package}::{dataset.dataset}</span>
                    <span className="block truncate text-[11px] text-muted-foreground">{dataset.description}</span>
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}
