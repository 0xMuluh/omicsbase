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
  FileText,
  Loader2,
  Mic,
  Plus,
  X,
} from "lucide-react";

import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";

type LaunchMode = "build" | "plan";

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

const DESIGN_SUGGESTIONS = [
  "Compare two groups",
  "More than two groups",
  "Longitudinal samples",
  "Include covariates",
];

async function launchStudy(options: {
  text: string;
  files: AttachedFile[];
  mode: LaunchMode;
  name?: string;
  question?: string;
  onStep: (step: "uploading" | "planning") => void;
}): Promise<string> {
  const hasDataFile = options.files.some((item) => item.role !== "analysis_plan");
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

  if (hasDataFile) {
    options.onStep("planning");
    await api.startPlanning(projectId);
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
  const [mode, setMode] = useState<LaunchMode>("build");
  const [modeOpen, setModeOpen] = useState(false);
  const [files, setFiles] = useState<AttachedFile[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [step, setStep] = useState<"idle" | "uploading" | "planning">("idle");

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

      // Immediately create project and navigate to workspace
      const projectId = await launchStudy({
        text: text || "Analyze the study.",
        files,
        mode,
        onStep: setStep,
      });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      router.push(`/projects/${projectId}/workspace`);
      return { kind: "project" as const, id: projectId };
    },
    onError: () => {
      setStep("idle");
    },
    onSettled: () => setStep("idle"),
  });

  const createNoteMutation = useMutation({
    mutationFn: async () => {
      const thread = await api.createStandaloneNoteThread({
        title: prompt.trim().slice(0, 72) || "Untitled note",
      });
      queryClient.invalidateQueries({ queryKey: ["note-threads"] });
      router.push(`/notes?thread=${thread.id}`);
      return { kind: "note" as const, id: thread.id };
    },
  });

  const canSubmit = Boolean(prompt.trim() || files.length) && !createMutation.isPending;
  const statusLabel =
    step === "uploading"
        ? "Uploading study files..."
        : step === "planning"
          ? mode === "build"
            ? "Agent is taking control and building..."
            : "Drafting the analysis plan..."
          : null;

  return (
    <div className={variant === "hero" ? "w-full" : "mx-auto w-full max-w-3xl"}>
      {variant === "page" ? (
        <div className="mb-10 text-center">
          <h1 className="font-display text-4xl font-medium tracking-tight text-foreground sm:text-5xl">
            See beyond the counts.
          </h1>
          <p className="mx-auto mt-3 max-w-xl text-sm leading-6 text-muted-foreground">
            Describe the study, attach data with +, choose Build or Plan, and let the agent take control.
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
        <AnimatePresence>
          {files.length > 0 ? (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              className="mb-1.5 flex flex-wrap gap-2 px-1"
            >
              {files.map((item, index) => (
                <div
                  key={`${item.file.name}-${index}`}
                  className="inline-flex max-w-full items-center gap-2 rounded-full border border-border bg-muted/50 py-1 pl-2.5 pr-1 text-xs text-foreground"
                >
                  <FileText className="h-3.5 w-3.5 shrink-0 text-teal-600 dark:text-teal-300" />
                  <span className="truncate">{item.file.name}</span>
                  <button
                    type="button"
                    className="rounded-full p-1 text-muted-foreground hover:bg-background hover:text-foreground"
                    onClick={() => setFiles((prev) => prev.filter((_, i) => i !== index))}
                    aria-label={`Remove ${item.file.name}`}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </motion.div>
          ) : null}
        </AnimatePresence>

        {prompt === "" && files.length === 0 && !createMutation.isPending ? (
          <div className="mb-1.5 flex flex-wrap gap-1.5 px-1">
            {DESIGN_SUGGESTIONS.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                onClick={() => setPrompt(suggestion)}
                className="rounded-full border border-border bg-muted/40 px-3 py-1 text-xs text-muted-foreground transition hover:bg-muted hover:text-foreground"
              >
                {suggestion}
              </button>
            ))}
          </div>
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
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => fileInputRef.current?.click()}
            disabled={createMutation.isPending}
            className="h-10 w-10 shrink-0 rounded-full border border-border bg-muted/40 p-0 text-muted-foreground hover:bg-muted hover:text-foreground"
            title="Add files"
          >
            <Plus className="h-4 w-4" />
          </Button>

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
                {mode === "build" ? "Build" : "Plan"}
                <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
              </button>
              <AnimatePresence>
                {modeOpen ? (
                  <motion.div
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: 6 }}
                    className="absolute right-0 bottom-[calc(100%+8px)] z-20 w-64 overflow-hidden rounded-2xl border border-border bg-[var(--composer-elevated)] p-1 shadow-2xl"
                  >
                    <ModeOption
                      title="Build"
                      description="When a study starts, the agent builds the report."
                      active={mode === "build"}
                      onClick={() => {
                        setMode("build");
                        setModeOpen(false);
                      }}
                    />
                    <ModeOption
                      title="Plan"
                      description="When a study starts, pause for plan approval."
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
            ? "Build mode lets the AI take control when the study plan is clear."
            : "Plan mode pauses for your approval before generation."}
        </p>
      )}

      {createMutation.isError ? (
        <p className="mt-3 text-center text-sm text-red-600 dark:text-red-300">
          {(createMutation.error as Error).message}
        </p>
      ) : null}

      <button
        type="button"
        onClick={() => createNoteMutation.mutate()}
        disabled={createNoteMutation.isPending || createMutation.isPending}
        className="mx-auto mt-5 flex items-center gap-1.5 rounded-full border border-border bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-50"
        title="Start a lightweight notebook without a project"
      >
        {createNoteMutation.isPending ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <FileText className="h-3.5 w-3.5" />
        )}
        Start a notebook instead — New note
      </button>
    </div>
  );
}

function ModeOption({
  title,
  description,
  active,
  onClick,
}: {
  title: string;
  description: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full rounded-xl px-3 py-2.5 text-left transition ${
        active
          ? "bg-teal-500/10 text-teal-800 dark:bg-teal-400/15 dark:text-teal-100"
          : "text-foreground hover:bg-muted"
      }`}
    >
      <div className="text-sm font-medium">{title}</div>
      <div className="mt-0.5 text-xs leading-4 text-muted-foreground">{description}</div>
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
