"use client";

import type { ReactNode } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowUp, Code2, Database, FileText, Loader2, Plus, Table2, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { FileChips } from "@/components/composer/FileChips";

function ComposerAddButton({
  open,
  onToggle,
  disabled,
  onAddData,
  onAddNote,
  onAddCode,
  onExport,
  onInsertResult,
  onImportDataset,
  exportPending,
  exported,
  showExport,
}: {
  open: boolean;
  onToggle: () => void;
  disabled?: boolean;
  onAddData: () => void;
  onAddNote: () => void;
  onAddCode: () => void;
  onExport?: () => void;
  onInsertResult?: () => void;
  onImportDataset?: () => void;
  exportPending?: boolean;
  exported?: boolean;
  showExport?: boolean;
}) {
  return (
    <div className="relative shrink-0">
      <Button
        type="button"
        size="sm"
        variant="ghost"
        onClick={onToggle}
        disabled={disabled}
        className="h-8 w-8 rounded-full p-0 text-muted-foreground hover:bg-muted hover:text-foreground"
        title="Add to this note"
        aria-label="Add to this note"
      >
        <Plus className="h-3.5 w-3.5" />
      </Button>
      <AnimatePresence>
        {open ? (
          <motion.div
            initial={{ opacity: 0, scale: 0.96, y: 6 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 6 }}
            transition={{ type: "spring", stiffness: 420, damping: 32, mass: 0.9 }}
            style={{ transformOrigin: "bottom left" }}
            className="absolute bottom-11 left-0 z-20 min-w-48 rounded-xl border border-border bg-popover p-1.5 text-sm shadow-xl"
          >
            <button type="button" className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-foreground hover:bg-muted" onClick={onAddData}>
              <Upload className="h-4 w-4 text-muted-foreground" /> Add data
            </button>
            {onImportDataset ? (
              <button type="button" className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-foreground hover:bg-muted" onClick={onImportDataset}>
                <Database className="h-4 w-4 text-muted-foreground" /> Import example dataset
              </button>
            ) : null}
            <button type="button" className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-foreground hover:bg-muted" onClick={onAddNote}>
              <FileText className="h-4 w-4 text-muted-foreground" /> Add note
            </button>
            <button type="button" className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-foreground hover:bg-muted" onClick={onAddCode}>
              <Code2 className="h-4 w-4 text-muted-foreground" /> Add R code
            </button>
            {onInsertResult ? (
              <button type="button" className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-foreground hover:bg-muted" onClick={onInsertResult}>
                <Table2 className="h-4 w-4 text-muted-foreground" /> Insert workspace result
              </button>
            ) : null}
            {showExport && onExport ? (
              <button
                type="button"
                className="mt-1 flex w-full items-center gap-2 rounded-lg border-t border-border/40 px-2.5 py-2 pt-2 text-left text-foreground hover:bg-muted"
                onClick={onExport}
                disabled={exportPending}
              >
                {exportPending ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : <FileText className="h-4 w-4 text-muted-foreground" />}
                {exported ? "Exported QMD" : "Export draft QMD"}
              </button>
            ) : null}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

export function NoteComposer({
  prompt,
  onPromptChange,
  onSubmit,
  composerMenuOpen,
  onToggleComposerMenu,
  addMenuDisabled = false,
  inputDisabled = false,
  submitDisabled = false,
  submitPending = false,
  onAddData,
  onImportDataset,
  onAddNote,
  onAddCode,
  onExport,
  onInsertResult,
  exportPending = false,
  exported = false,
  showExport = false,
  beforeComposer,
  stagedFiles,
  onRemoveFile,
}: {
  prompt: string;
  onPromptChange: (value: string) => void;
  onSubmit: () => void;
  composerMenuOpen: boolean;
  onToggleComposerMenu: () => void;
  addMenuDisabled?: boolean;
  inputDisabled?: boolean;
  submitDisabled?: boolean;
  submitPending?: boolean;
  onAddData: () => void;
  onImportDataset?: () => void;
  onAddNote: () => void;
  onAddCode: () => void;
  onExport?: () => void;
  onInsertResult?: () => void;
  exportPending?: boolean;
  exported?: boolean;
  showExport?: boolean;
  beforeComposer?: ReactNode;
  stagedFiles?: File[];
  onRemoveFile?: (index: number) => void;
}) {
  return (
    <div className="relative rounded-[25px] border border-border bg-[var(--composer-surface)] p-1 shadow-[0_18px_50px_rgba(15,23,42,0.08)] backdrop-blur transition-colors dark:shadow-[0_30px_80px_rgba(0,0,0,0.35)]">
      {beforeComposer}
      {stagedFiles && onRemoveFile ? (
        <FileChips files={stagedFiles} onRemove={onRemoveFile} className="mb-1.5 flex flex-wrap gap-2 px-1" />
      ) : null}
      <div className="flex items-center gap-1">
        <ComposerAddButton
          open={composerMenuOpen}
          onToggle={onToggleComposerMenu}
          disabled={addMenuDisabled}
          onAddData={onAddData}
          onImportDataset={onImportDataset}
          onAddNote={onAddNote}
          onAddCode={onAddCode}
          onExport={onExport}
          onInsertResult={onInsertResult}
          exportPending={exportPending}
          exported={exported}
          showExport={showExport}
        />
        <Textarea
          value={prompt}
          onChange={(event) => onPromptChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void onSubmit();
            }
          }}
          disabled={inputDisabled}
          rows={1}
          placeholder="Ask OmicsBase..."
          className={"max-h-52 min-h-[36px] min-w-0 flex-1 resize-none border-0 bg-transparent px-2.5 py-1.5 text-[17px] leading-6 text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-0" + (inputDisabled ? " disabled:opacity-60" : "")}
        />
        <Button type="button" size="icon" className="h-9 w-9 shrink-0 rounded-full" onClick={() => void onSubmit()} disabled={submitDisabled} title="Send" aria-label="Send">
          {submitPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ArrowUp className="h-3.5 w-3.5" />}
        </Button>
      </div>
    </div>
  );
}
