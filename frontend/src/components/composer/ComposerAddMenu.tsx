"use client";

import { AnimatePresence, motion } from "framer-motion";
import { Database, Plus, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";

export function ComposerAddMenu({
  open,
  onToggle,
  onAddFiles,
  onImportDataset,
  disabled = false,
  placement = "bottom",
}: {
  open: boolean;
  onToggle: () => void;
  onAddFiles: () => void;
  onImportDataset: () => void;
  disabled?: boolean;
  placement?: "top" | "bottom";
}) {
  const menuClass = placement === "top"
    ? "absolute left-0 top-[calc(100%+8px)] z-30 w-56 overflow-hidden rounded-2xl border border-border bg-[var(--composer-elevated)] p-1 shadow-2xl"
    : "absolute bottom-11 left-0 z-30 min-w-56 rounded-xl border border-border bg-popover p-1.5 text-sm shadow-xl";

  return (
    <div className="relative shrink-0">
      <Button
        type="button"
        size="sm"
        variant="ghost"
        onClick={onToggle}
        disabled={disabled}
        className="h-8 w-8 rounded-full p-0 text-muted-foreground hover:bg-muted hover:text-foreground"
        title="Add files or an example dataset"
        aria-label="Add files or an example dataset"
      >
        <Plus className="h-3.5 w-3.5" />
      </Button>
      <AnimatePresence>
        {open ? (
          <>
            <div className="fixed inset-0 z-20" onClick={onToggle} />
            <motion.div
              initial={{ opacity: 0, scale: 0.96, y: placement === "top" ? -6 : 6 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96, y: placement === "top" ? -6 : 6 }}
              transition={{ type: "spring", stiffness: 420, damping: 32, mass: 0.9 }}
              style={{ transformOrigin: placement === "top" ? "top left" : "bottom left" }}
              className={menuClass}
            >
              <button
                type="button"
                onClick={onAddFiles}
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-foreground hover:bg-muted"
              >
                <Upload className="h-4 w-4 text-muted-foreground" /> Add files
              </button>
              <button
                type="button"
                onClick={onImportDataset}
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-foreground hover:bg-muted"
              >
                <Database className="h-4 w-4 text-muted-foreground" /> Import example dataset
              </button>
            </motion.div>
          </>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
