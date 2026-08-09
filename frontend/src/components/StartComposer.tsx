"use client";

import {
  forwardRef,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowUp,
  ChevronDown,
  Database,
  Loader2,
  Mic,  X,
} from "lucide-react";

import { api } from "@/lib/api";
import type { ImportableDataset } from "@/lib/api/types/projects";
import { FileChips } from "@/components/composer/FileChips";
import { ComposerAddMenu } from "@/components/composer/ComposerAddMenu";
import { DatasetPicker } from "@/components/composer/DatasetPicker";
import { Button } from "@/components/ui/button";

type LaunchMode = "notes" | "build" | "plan";

interface AttachedFile {
  file: File;
  role: string;
}

function inferRole(file: File): string {
  const lower = file.name.toLowerCase();
  if (
    lower.includes("plan")
    || lower.includes("protocol")
    || lower.includes("workflow")
    || lower.endsWith(".md")
    || lower.endsWith(".docx")
    || lower.endsWith(".txt")
  ) {
    return "analysis_plan";
  }
  return "auto";
}



function splitPrompt(prompt: string): { question: string; customPlanText?: string } {
  const trimmed = prompt.trim();
  if (!trimmed) {
    return {
      question: "Build a reproducible downstream analysis report from the uploaded study files.",
    };
  }
  if (trimmed.length > 500) {
    const firstBreak = trimmed.indexOf("\n\n");
    if (firstBreak > 40 && firstBreak < 280) {
      return {
        question: trimmed.slice(0, firstBreak).trim(),
        customPlanText: trimmed.slice(firstBreak).trim(),
      };
    }
    return {
      question: trimmed.slice(0, 280).trim(),
      customPlanText: trimmed,
    };
  }
  return { question: trimmed };
}

async function launchStudy(options: {
  text: string;
  files: AttachedFile[];
  mode: LaunchMode;
  name?: string;
  question?: string;
  onStep: (step: "uploading") => void;
}): Promise<string> {
  const { question, customPlanText } = splitPrompt(options.question || options.text);
  options.onStep("uploading");

  const project = await api.createProject({
    name: options.name || undefined,
    question,
    custom_plan_text: customPlanText,
    auto_build: options.mode === "build",
  });
  const projectId = project.id;

  const uploadFailures: string[] = [];
  for (const item of options.files) {
    try {
      await api.uploadFile(projectId, item.file, item.role);
    } catch (error) {
      uploadFailures.push(
        `${item.file.name}: ${error instanceof Error ? error.message : "upload failed"}`
      );
    }
  }
  if (uploadFailures.length) {
    await api.deleteProject(projectId).catch(() => undefined);
    throw new Error(`Uploads failed: ${uploadFailures.join("; ")}`);
  }

  return projectId;
}

export function StartComposer({
  variant = "page",
}: {
  variant?: "page" | "hero";
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const modeMenuRef = useRef<HTMLDivElement>(null);

  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState<LaunchMode>("notes");
  const [modeOpen, setModeOpen] = useState(false);
  const [files, setFiles] = useState<AttachedFile[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [step, setStep] = useState<"idle" | "uploading">("idle");
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  const [datasets, setDatasets] = useState<ImportableDataset[] | null>(null);
  const [datasetOpen, setDatasetOpen] = useState(false);
  const [selectedDataset, setSelectedDataset] = useState<ImportableDataset | null>(null);

  const openDatasetPicker = useCallback(async () => {
    setAddMenuOpen(false);
    setDatasetOpen(true);
    if (datasets === null) {
      try {
        const result = await api.listImportableDatasets();
        setDatasets(result.datasets);
      } catch {
        setDatasets([]);
      }
    }
  }, [datasets]);

  const addFiles = useCallback((incoming: FileList | File[]) => {
    const next = Array.from(incoming).map((file) => ({
      file,
      role: inferRole(file),
    }));
    setFiles((prev) => [...prev, ...next]);
  }, []);

  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      if (!modeMenuRef.current?.contains(event.target as Node)) {
        setModeOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, []);

  useEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.style.height = "0px";
    node.style.height = `${Math.min(node.scrollHeight, 220)}px`;
  }, [prompt]);

  const createMutation = useMutation({
    mutationFn: async () => {
      const text = prompt.trim();
      if (!text && files.length === 0) {
        throw new Error("Ask a question, describe an analysis, or attach study files.");
      }

      if (mode === "notes") {
        const thread = await api.createStandaloneNoteThread({
          title: text.slice(0, 72) || "Untitled note",
        });
        queryClient.invalidateQueries({ queryKey: ["note-threads"] });
        if (selectedDataset) {
          await api.importStandaloneNoteDataset(thread.id, selectedDataset.package, selectedDataset.dataset);
        }
        for (const item of files) {
          await api.uploadStandaloneNoteFile(thread.id, item.file);
        }
        router.push(`/notes?thread=${thread.id}&prompt=${encodeURIComponent(text)}`);
        return { kind: "note" as const, id: thread.id };
      }

      // Immediately create project and navigate to workspace
      const projectId = await launchStudy({
        text: text || "Analyze the study.",
        files,
        mode,
        onStep: setStep,
      });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      if (selectedDataset) {
        await api.importProjectDataset(projectId, selectedDataset.package, selectedDataset.dataset);
      }
      router.push(`/projects/${projectId}/workspace`);
      return { kind: "project" as const, id: projectId };
    },
    onError: () => {
      setStep("idle");
    },
    onSettled: () => setStep("idle"),
  });

  const canSubmit = Boolean(prompt.trim() || files.length) && !createMutation.isPending;
  const statusLabel = step === "uploading" ? "Uploading study files..." : null;

  return (
    <div className={variant === "hero" ? "w-full" : "mx-auto w-full max-w-3xl"}>
      {variant === "page" ? (
        <div className="mb-10 text-center">
          <h1 className="font-display text-4xl font-medium tracking-tight text-foreground sm:text-5xl">
            See beyond the counts.
          </h1>
          <p className="mx-auto mt-3 max-w-xl text-sm leading-6 text-muted-foreground">
            Ask a question, attach data with +, and choose Notes, Build, or Plan.
          </p>
        </div>
      ) : null}

      <div
        className={`relative rounded-[28px] border bg-[var(--composer-surface)] p-1.5 shadow-[0_18px_50px_rgba(15,23,42,0.08)] backdrop-blur transition-colors dark:shadow-[0_30px_80px_rgba(0,0,0,0.35)] ${
          dragOver ? "border-teal-500/50" : "border-border"
        }`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragOver(false);
          if (event.dataTransfer.files?.length) addFiles(event.dataTransfer.files);
        }}
      >
        <FileChips
          files={files.map((item) => item.file)}
          onRemove={(index) => setFiles((prev) => prev.filter((_, itemIndex) => itemIndex !== index))}
          className="mb-1.5 flex flex-wrap gap-2 px-1"
        />

        {selectedDataset ? (
          <div className="mb-1.5 inline-flex max-w-full items-center gap-2 rounded-full border border-border bg-muted/50 py-1 pl-2.5 pr-1 text-xs text-foreground">
            <Database className="h-3.5 w-3.5 shrink-0 text-teal-600 dark:text-teal-300" />
            <span className="truncate">{selectedDataset.package}::{selectedDataset.dataset}</span>
            <button
              type="button"
              className="rounded-full p-1 text-muted-foreground hover:bg-background hover:text-foreground"
              onClick={() => setSelectedDataset(null)}
              aria-label="Remove example dataset"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ) : null}

        {datasetOpen ? (
          <DatasetPicker
            datasets={datasets}
            onClose={() => setDatasetOpen(false)}
            onPick={(dataset) => {
              setSelectedDataset(dataset);
              setDatasetOpen(false);
            }}
            placement="top"
            selected={selectedDataset}
          />
        ) : null}

        <div className="flex items-end gap-1.5">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(event) => {
              if (event.target.files?.length) addFiles(event.target.files);
              event.target.value = "";
            }}
          />
          <ComposerAddMenu
            open={addMenuOpen}
            onToggle={() => setAddMenuOpen((open) => !open)}
            onAddFiles={() => {
              setAddMenuOpen(false);
              fileInputRef.current?.click();
            }}
            onImportDataset={openDatasetPicker}
            disabled={createMutation.isPending}
            placement="top"
          />

          <div className="min-w-0 flex-1">
            <ComposerTextarea
              ref={textareaRef}
              value={prompt}
              disabled={createMutation.isPending}
              onChange={setPrompt}
              onSubmit={() => {
                if (canSubmit) createMutation.mutate();
              }}
            />
          </div>

          <div className="flex shrink-0 items-center gap-1.5">
            <div ref={modeMenuRef} className="relative">
              <button
                type="button"
                onClick={() => setModeOpen((open) => !open)}
                disabled={createMutation.isPending}
                className="inline-flex h-10 items-center gap-1.5 rounded-full border border-border bg-muted/40 px-3 text-sm font-medium text-foreground transition hover:bg-muted"
              >
                {mode === "notes" ? "Notes" : mode === "build" ? "Build" : "Plan"}
                <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
              </button>
              <AnimatePresence>
                {modeOpen ? (
                  <motion.div
                    initial={{ opacity: 0, scale: 0.96, y: -6 }}
                    animate={{ opacity: 1, scale: 1, y: 0 }}
                    exit={{ opacity: 0, scale: 0.96, y: -6 }}
                    transition={{ type: "spring", stiffness: 420, damping: 32, mass: 0.9 }}
                    style={{ transformOrigin: "top right" }}
                    className="absolute right-0 top-[calc(100%+8px)] z-20 w-40 overflow-hidden rounded-2xl border border-border bg-[var(--composer-elevated)] p-1 shadow-2xl"
                  >
                    <ModeOption
                      title="Notes"
                      active={mode === "notes"}
                      onClick={() => {
                        setMode("notes");
                        setModeOpen(false);
                      }}
                    />
                    <ModeOption
                      title="Build"
                      active={mode === "build"}
                      onClick={() => {
                        setMode("build");
                        setModeOpen(false);
                      }}
                    />
                    <ModeOption
                      title="Plan"
                      active={mode === "plan"}
                      onClick={() => {
                        setMode("plan");
                        setModeOpen(false);
                      }}
                    />
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
              type="button"
              size="sm"
              disabled={!canSubmit}
              onClick={() => createMutation.mutate()}
              className="h-10 w-10 rounded-full bg-teal-600 p-0 text-white hover:bg-teal-500 disabled:bg-muted disabled:text-muted-foreground dark:bg-teal-400 dark:text-zinc-950 dark:hover:bg-teal-300 dark:disabled:bg-white/10 dark:disabled:text-zinc-500"
              title="Send"
            >
              {createMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <ArrowUp className="h-4 w-4" />
              )}
            </Button>
          </div>
        </div>
      </div>

      {statusLabel ? (
        <p className="mt-4 flex items-center justify-center gap-2 text-sm text-teal-700 dark:text-teal-200/90">
          <Loader2 className="h-4 w-4 animate-spin" />
          {statusLabel}
        </p>
      ) : (
        <p className="mt-4 text-center text-sm text-muted-foreground">
          {mode === "build"
            ? "Build mode lets OmicsBase take control when the study plan is clear."
            : mode === "plan"
              ? "Plan mode pauses for your approval before generation."
              : "Notes opens a lightweight notebook."}
        </p>
      )}

      {createMutation.isError ? (
        <p className="mt-3 text-center text-sm text-red-600 dark:text-red-300">
          {(createMutation.error as Error).message}
        </p>
      ) : null}
    </div>
  );
}

function ModeOption({
  title,
  active,
  onClick,
}: {
  title: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full rounded-xl px-3 py-2.5 text-left text-sm font-medium transition ${
        active
          ? "bg-teal-500/10 text-teal-800 dark:bg-teal-400/15 dark:text-teal-100"
          : "text-foreground hover:bg-muted"
      }`}
    >
      {title}
    </button>
  );
}

const ComposerTextarea = forwardRef<
  HTMLTextAreaElement,
  {
    value: string;
    disabled?: boolean;
    onChange: (value: string) => void;
    onSubmit: () => void;
  }
>(function ComposerTextarea({ value, disabled, onChange, onSubmit }, ref) {
  return (
    <textarea
      ref={ref}
      value={value}
      disabled={disabled}
      rows={1}
      placeholder="Ask OmicsBase..."
      onChange={(event) => onChange(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          onSubmit();
        }
      }}
      className="max-h-52 min-h-[40px] w-full resize-none border-0 bg-transparent px-2.5 py-1.5 text-[17px] leading-6 text-foreground outline-none placeholder:text-muted-foreground disabled:opacity-60"
    />
  );
});
