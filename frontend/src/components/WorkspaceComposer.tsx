"use client";

import { useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowUp, ChevronDown, Database, FileImage, FileText, Loader2, Mic, Plus, Upload, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { PendingQuestion } from "@/lib/api";

function formatBytes(bytes: number | null | undefined): string | null {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return null;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function isImageFile(file: File): boolean {
  if (file.type.startsWith("image/")) return true;
  const ext = file.name.split(".").pop()?.toLowerCase() || "";
  return ["png", "jpg", "jpeg", "gif", "webp", "tif", "tiff", "svg"].includes(ext);
}

export function WorkspaceComposer({
  pendingQuestion,
  chatMode,
  disabled,
  onSend,
  onAnswer,
  onModeChange,
}: {
  pendingQuestion: PendingQuestion | null;
  chatMode: "build" | "discuss";
  disabled: boolean;
  onSend: (message: string, mode: "build" | "discuss", files?: File[]) => void;
  onAnswer: (answer: string) => void;
  onModeChange: (mode: "build" | "discuss") => void;
  onAddFiles?: (files: File[]) => void;
}) {
  const [promptText, setPromptText] = useState("");
  const [stagedFiles, setStagedFiles] = useState<File[]>([]);
  const [modeOpen, setModeOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const modeMenuRef = useRef<HTMLDivElement>(null);

  const pickMode = (mode: "build" | "discuss") => {
    setModeOpen(false);
    onModeChange(mode);
  };

  const removeStagedFile = (indexToRemove: number) => {
    setStagedFiles((prev) => prev.filter((_, idx) => idx !== indexToRemove));
  };

  const submit = (event?: React.FormEvent) => {
    event?.preventDefault();
    const message = promptText.trim();
    const hasFiles = stagedFiles.length > 0;
    if ((!message && !hasFiles) || disabled) return;
    const finalFiles = [...stagedFiles];
    setPromptText("");
    setStagedFiles([]);
    onSend(message, chatMode, finalFiles.length ? finalFiles : undefined);
  };

  return (
    <form
      onSubmit={submit}
      className="relative rounded-[28px] border border-border bg-[var(--composer-surface)] p-1.5 shadow-[0_18px_50px_rgba(15,23,42,0.08)] backdrop-blur transition-colors dark:shadow-[0_30px_80px_rgba(0,0,0,0.35)]"
    >
      {pendingQuestion ? (
        <div className="mb-2 rounded-2xl border border-teal-500/30 bg-teal-500/5 px-4 py-3">
          <p className="text-sm font-medium leading-5 text-foreground">
            {pendingQuestion.question}
          </p>
          {pendingQuestion.options.length > 0 ? (
            <div className="mt-2.5 flex flex-wrap gap-2">
              {pendingQuestion.options.map((option) => (
                <Button
                  key={option}
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={disabled}
                  onClick={() => onAnswer(option)}
                  className="border-teal-500/40 text-teal-800 hover:bg-teal-500/10 dark:text-teal-100"
                >
                  {option}
                </Button>
              ))}
            </div>
          ) : (
            <p className="mt-2 text-xs text-muted-foreground">
              The agent is waiting for your answer...
            </p>
          )}
        </div>
      ) : null}

      {/* Staged File Attachments Preview (ChatGPT style pre-flight draft) */}
      {stagedFiles.length > 0 ? (
        <div className="mb-1.5 flex flex-wrap gap-1.5 px-2 pt-1">
          {stagedFiles.map((file, idx) => {
            const Icon = isImageFile(file) ? FileImage : FileText;
            const sizeStr = formatBytes(file.size);
            return (
              <div
                key={`${file.name}-${idx}`}
                className="group flex min-w-0 max-w-60 items-center gap-2 rounded-xl border border-border bg-background/80 px-2.5 py-1.5 text-left shadow-sm backdrop-blur"
                title={file.name}
              >
                <Icon className="h-4 w-4 shrink-0 text-red-500" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-medium text-foreground">{file.name}</span>
                  {sizeStr ? <span className="block text-[10px] uppercase text-muted-foreground">{sizeStr}</span> : null}
                </span>
                <button
                  type="button"
                  onClick={() => removeStagedFile(idx)}
                  className="rounded-full p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
                  title="Remove attachment"
                  aria-label={`Remove ${file.name}`}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            );
          })}
        </div>
      ) : null}

      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(event) => {
          const files = Array.from(event.target.files || []);
          event.target.value = "";
          setAddOpen(false);
          if (files.length) {
            setStagedFiles((prev) => [...prev, ...files]);
          }
        }}
      />
      <div className="flex items-end gap-1.5">
        <div className="relative shrink-0">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => setAddOpen((open) => !open)}
            disabled={disabled}
            className="h-10 w-10 rounded-full border border-border bg-muted/40 p-0 text-muted-foreground hover:bg-muted hover:text-foreground"
            title="Add files or an example dataset"
            aria-label="Add files or an example dataset"
          >
            <Plus className="h-4 w-4" />
          </Button>
          {addOpen ? (
            <>
              <div className="fixed inset-0 z-20" onClick={() => setAddOpen(false)} />
              <div className="absolute bottom-11 left-0 z-30 min-w-56 rounded-xl border border-border bg-popover p-1.5 text-sm shadow-xl">
                <button
                  type="button"
                  onClick={() => {
                    setAddOpen(false);
                    fileInputRef.current?.click();
                  }}
                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-foreground hover:bg-muted"
                >
                  <Upload className="h-4 w-4 text-muted-foreground" /> Add files
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setAddOpen(false);
                    onSend("Import the GlobalPatterns example dataset and build a report.", chatMode);
                  }}
                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-foreground hover:bg-muted"
                >
                  <Database className="h-4 w-4 text-muted-foreground" /> Import example dataset
                </button>
              </div>
            </>
          ) : null}
        </div>
        <Textarea
          placeholder={chatMode === "discuss" ? "Discuss methods or plan a change..." : "Ask OmicsBase..."}
          value={promptText}
          onChange={(event) => setPromptText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          disabled={disabled}
          rows={1}
          className="max-h-52 min-h-[40px] min-w-0 flex-1 resize-none border-0 bg-transparent px-2.5 py-1.5 text-[17px] leading-6 text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-0 disabled:opacity-60"
        />

        <div className="flex shrink-0 items-center gap-1.5">
          <div ref={modeMenuRef} className="relative">
            <button
              type="button"
              onClick={() => setModeOpen((open) => !open)}
              disabled={disabled}
              className="inline-flex h-10 items-center gap-1.5 rounded-full border border-border bg-muted/40 px-3 text-sm font-medium text-foreground transition hover:bg-muted"
            >
              {chatMode === "build" ? "Build" : "Discuss"}
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
            </button>
            <AnimatePresence>
              {modeOpen ? (
                <motion.div
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: 6 }}
                  className="absolute right-0 bottom-[calc(100%+8px)] z-30 w-64 overflow-hidden rounded-2xl border border-border bg-[var(--composer-elevated)] p-1 shadow-2xl"
                >
                  <button
                    type="button"
                    onClick={() => pickMode("build")}
                    className={`w-full rounded-xl px-3 py-2.5 text-left transition ${
                      chatMode === "build"
                        ? "bg-teal-500/10 text-teal-800 dark:bg-teal-400/15 dark:text-teal-100 font-medium"
                        : "text-foreground hover:bg-muted"
                    }`}
                  >
                    <div className="text-sm font-medium">Build</div>
                    <div className="mt-0.5 text-xs leading-4 text-muted-foreground">The agent builds and repairs report code.</div>
                  </button>
                  <button
                    type="button"
                    onClick={() => pickMode("discuss")}
                    className={`w-full rounded-xl px-3 py-2.5 text-left transition ${
                      chatMode === "discuss"
                        ? "bg-teal-500/10 text-teal-800 dark:bg-teal-400/15 dark:text-teal-100 font-medium"
                        : "text-foreground hover:bg-muted"
                    }`}
                  >
                    <div className="text-sm font-medium">Discuss</div>
                    <div className="mt-0.5 text-xs leading-4 text-muted-foreground">Read-only scientific planning without edits.</div>
                  </button>
                </motion.div>
              ) : null}
            </AnimatePresence>
          </div>

          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled
            className="h-10 w-10 rounded-full border border-border bg-muted/40 p-0 text-muted-foreground opacity-50"
            title="Voice input coming soon"
          >
            <Mic className="h-4 w-4" />
          </Button>

          <Button
            type="submit"
            size="sm"
            disabled={(!promptText.trim() && !stagedFiles.length) || disabled}
            className="h-10 w-10 rounded-full bg-teal-600 p-0 text-white hover:bg-teal-500 disabled:bg-muted disabled:text-muted-foreground dark:bg-teal-400 dark:text-zinc-950 dark:hover:bg-teal-300 dark:disabled:bg-white/10 dark:disabled:text-zinc-500"
            title="Send"
          >
            {disabled ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowUp className="h-4 w-4" />}
          </Button>
        </div>
      </div>
    </form>
  );
}
