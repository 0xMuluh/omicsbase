"use client";

import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export function NotePromotionDialog({
  open,
  name,
  question,
  notes,
  autoBuild,
  pending,
  onNameChange,
  onQuestionChange,
  onNotesChange,
  onAutoBuildChange,
  onClose,
  onSubmit,
}: {
  open: boolean;
  name: string;
  question: string;
  notes: string;
  autoBuild: boolean;
  pending: boolean;
  onNameChange: (value: string) => void;
  onQuestionChange: (value: string) => void;
  onNotesChange: (value: string) => void;
  onAutoBuildChange: (value: boolean) => void;
  onClose: () => void;
  onSubmit: () => void;
}) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-label="Review workspace transfer">
      <div className="w-full max-w-lg space-y-4 rounded-xl border border-border bg-background p-5 shadow-2xl">
        <div>
          <h2 className="text-sm font-semibold">Review workspace transfer</h2>
          <p className="mt-1 text-xs text-muted-foreground">Notebook files, tested cells, findings, and provenance will be carried forward. Review the project context before creating the workspace.</p>
        </div>
        <input value={name} onChange={(event) => onNameChange(event.target.value)} placeholder="Workspace name" className="h-9 w-full rounded-md border border-border bg-muted/30 px-3 text-sm outline-none focus:ring-2 focus:ring-teal-500/40" />
        <Textarea value={question} onChange={(event) => onQuestionChange(event.target.value)} placeholder="Research question (optional)" rows={3} />
        <Textarea value={notes} onChange={(event) => onNotesChange(event.target.value)} placeholder="Planning notes and constraints (optional)" rows={4} />
        <label className="flex items-start gap-2 rounded-lg border border-border/60 bg-muted/30 p-3 text-xs">
          <input type="checkbox" checked={autoBuild} onChange={(event) => onAutoBuildChange(event.target.checked)} className="mt-0.5" />
          <span><strong className="font-medium">Build automatically after transfer</strong><br /><span className="text-muted-foreground">Leave off to inspect the carried-forward inputs and approve a plan first.</span></span>
        </label>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="button" onClick={onSubmit} disabled={pending}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null} Create workspace</Button>
        </div>
      </div>
    </div>
  );
}
