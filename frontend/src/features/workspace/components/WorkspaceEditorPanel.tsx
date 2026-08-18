"use client";

import { useRef, useState } from "react";
import Editor, { type Monaco, type OnMount } from "@monaco-editor/react";
import {
  AlertCircle,
  Check,
  Globe,
  Loader2,
  Lock,
  Play,
  Save,
  Unlock,
  X,
} from "lucide-react";

import { api, type Job, type Project } from "@/lib/api";
import { TabularPreview } from "./TabularPreview";
import { FileTypeIcon } from "./FileTypeIcon";
import { InlineAiWidget } from "./InlineAiWidget";
import { WorkspaceFileTree } from "./WorkspaceFileTree";
import { useWorkspaceFiles } from "../hooks/useWorkspaceFiles";
import { getLanguage, isEditableTabularPath, isImagePath, isTabularPath, isTextPath, tabLabel } from "../utils/filePaths";
import { Button } from "@/components/ui/button";

type WorkspaceFilesState = ReturnType<typeof useWorkspaceFiles>;

interface WorkspaceEditorPanelProps {
  projectId: string;
  project?: Project;
  files: WorkspaceFilesState;
  latestFailedJob?: Job;
  workspaceMode: "preview" | "code";
  resolvedTheme?: string;
  iframeKey: number;
  reportUrl: string;
  hasPreview: boolean;
  isFailed: boolean;
  isLive: boolean;
}

type MonacoEditor = Parameters<OnMount>[0];

interface InlineWidgetState {
  show: boolean;
  top: number;
  left: number;
  selectionText?: string;
  originalCode?: string;
  isGenerating: boolean;
  hasGenerated: boolean;
  diffStats?: { added: number; removed: number };
}

const initialInlineWidget: InlineWidgetState = {
  show: false,
  top: 20,
  left: 40,
  isGenerating: false,
  hasGenerated: false,
};

function SaveConflictDialog({
  message,
  onClose,
  onReload,
}: {
  message: string | null;
  onClose: () => void;
  onReload: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="alertdialog" aria-modal="true" aria-label="Save conflict">
      <div className="w-full max-w-md space-y-4 rounded-xl border border-red-500/30 bg-background p-5 shadow-2xl">
        <div className="flex items-start gap-3">
          <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-red-500" />
          <div>
            <h2 className="text-sm font-semibold">This file changed elsewhere</h2>
            <p className="mt-1 text-xs text-muted-foreground">Reload the latest file before applying your draft. Your unsaved text remains in this editor until you choose an action.</p>
          </div>
        </div>
        <p className="max-h-20 overflow-auto rounded bg-muted p-2 font-mono text-[10px] text-muted-foreground">{message}</p>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>Keep editing</Button>
          <Button type="button" size="sm" onClick={onReload}>Reload latest</Button>
        </div>
      </div>
    </div>
  );
}

export function WorkspaceEditorPanel({
  projectId,
  project,
  files,
  latestFailedJob,
  workspaceMode,
  resolvedTheme,
  iframeKey,
  reportUrl,
  hasPreview,
  isFailed,
  isLive,
}: WorkspaceEditorPanelProps) {
  const {
    activeFileEditable,
    activeFileReadOnly,
    activeTab,
    closeConflictDialog,
    contentLoading,
    dataViewMode,
    dirtyTabs,
    discardDraft,
    editorValue,
    fileContent,
    filePreview,
    fileSaveError,
    isDirty,
    lockedPaths,
    locksMutation,
    openTabs,
    previewLoading,
    runChunkMutation,
    saveMutation,
    saveMutationRef,
    selectTab,
    setDataViewMode,
    showConflictDialog,
    showTableView,
    toggleActiveLock,
    updateActiveDraft,
  } = files;
  const monacoEditorRef = useRef<MonacoEditor | null>(null);
  const monacoRef = useRef<Monaco | null>(null);
  const inlineDecorationsRef = useRef<string[]>([]);
  const [inlineWidget, setInlineWidget] = useState<InlineWidgetState>(initialInlineWidget);

  const clearInlineDiffDecorations = () => {
    const editor = monacoEditorRef.current;
    if (editor && inlineDecorationsRef.current.length) {
      inlineDecorationsRef.current = editor.deltaDecorations(inlineDecorationsRef.current, []);
    }
  };

  const triggerInlineAi = () => {
    const editor = monacoEditorRef.current;
    if (!editor) return;
    const position = editor.getPosition();
    const pos = position ? editor.getScrolledVisiblePosition(position) : null;
    const model = editor.getModel();
    const editorSelection = editor.getSelection();
    const selection = model && editorSelection ? model.getValueInRange(editorSelection) : "";
    const fullContent = editor.getValue();

    clearInlineDiffDecorations();
    setInlineWidget({
      show: true,
      top: (pos?.top ?? 40) + 30,
      left: Math.min((pos?.left ?? 40) + 40, 450),
      selectionText: selection,
      originalCode: fullContent,
      isGenerating: false,
      hasGenerated: false,
    });
  };

  const handleInlineGenerate = async (prompt: string) => {
    const editor = monacoEditorRef.current;
    if (!editor || !activeTab) return;
    const model = editor.getModel();
    const selection = editor.getSelection();
    if (!model) return;
    const selectedText = selection ? model.getValueInRange(selection) : "";
    const fullContent = editor.getValue();
    setInlineWidget((prev) => ({ ...prev, isGenerating: true }));

    const projectCtx = project
      ? "Project: " + project.name + " | Question: " + (project.question || "") + " | Dataset: " + (project.agent_memory?.summary || "")
      : undefined;
    const errorCtx = latestFailedJob ? "Error detail: " + (latestFailedJob.error || "") : undefined;
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const response = await fetch(baseUrl + "/api/inline-edit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          path: activeTab,
          prompt,
          selection: selectedText || null,
          content: fullContent,
          base_sha256: fileContent?.sha256,
          project_context: projectCtx,
          error_context: errorCtx,
        }),
      });
      if (!response.ok || !response.body) throw new Error("Failed to start inline edit stream");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let streamedTokens = "";
      let buffer = "";
      const startLine = selection ? selection.startLineNumber : 1;
      const consumeLine = (line: string) => {
        if (!line.trim()) return;
        try {
          const data = JSON.parse(line) as { type?: string; token?: string };
          if (data.type === "token" && data.token) streamedTokens += data.token;
        } catch {
          // Ignore malformed stream fragments.
        }
      };

      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = buffer.split(String.fromCharCode(10));
        buffer = lines.pop() || "";
        for (const line of lines) consumeLine(line);
        if (done) break;
      }
      if (buffer.trim()) consumeLine(buffer);

      if (!streamedTokens) throw new Error("Inline edit provider returned an empty preview");
      editor.executeEdits("inline-ai-preview", [{
        range: selectedText && selection ? selection : model.getFullModelRange(),
        text: streamedTokens,
        forceMoveMarkers: true,
      }]);

      const originalLines = (selectedText || fullContent).split(String.fromCharCode(10)).length;
      const streamedLines = streamedTokens.split(String.fromCharCode(10)).length;
      const monacoWindow = monacoRef.current;
      if (monacoWindow) {
        const endLine = startLine + streamedLines - 1;
        inlineDecorationsRef.current = editor.deltaDecorations(
          inlineDecorationsRef.current,
          [{
            range: new monacoWindow.Range(startLine, 1, Math.max(startLine, endLine), 1),
            options: {
              isWholeLine: true,
              className: "bg-emerald-500/15 border-l-2 border-emerald-400",
            },
          }],
        );
      }
      setInlineWidget((prev) => ({
        ...prev,
        isGenerating: false,
        hasGenerated: true,
        diffStats: {
          added: Math.max(0, streamedLines - originalLines),
          removed: Math.max(0, originalLines - streamedLines),
        },
      }));
    } catch (error) {
      console.error("Inline AI edit failed:", error);
      setInlineWidget((prev) => ({ ...prev, isGenerating: false }));
    }
  };

  const handleInlineAccept = () => {
    clearInlineDiffDecorations();
    if (monacoEditorRef.current) updateActiveDraft(monacoEditorRef.current.getValue() || "");
    setInlineWidget(initialInlineWidget);
  };

  const handleInlineReject = () => {
    clearInlineDiffDecorations();
    if (inlineWidget.originalCode !== undefined && monacoEditorRef.current) {
      monacoEditorRef.current.setValue(inlineWidget.originalCode);
    }
    setInlineWidget(initialInlineWidget);
  };

  const handleConflictReload = () => {
    if (activeTab) discardDraft(activeTab);
    closeConflictDialog();
  };

  return (
    <>
      {workspaceMode === "preview" ? (
    <div className="min-h-0 flex-1 overflow-hidden bg-white">
      {hasPreview ? (
        <iframe key={iframeKey} src={reportUrl} className="h-full w-full border-0 bg-white" title="Quarto Report Preview" />
      ) : (
        <div className="flex h-full flex-col items-center justify-center bg-background px-8 text-center">
          {isFailed ? <AlertCircle className="mb-4 h-10 w-10 text-red-400" /> : <Globe className="mb-4 h-10 w-10 text-teal-300" />}
          <h2 className="text-xl font-semibold text-foreground">
            {isFailed
              ? "Preview was not produced"
              : isLive
                ? "The report is being assembled"
                : "No report yet"}
          </h2>
          <p className="mt-3 max-w-xl text-sm leading-6 text-muted-foreground">
            {isFailed
              ? "Use Ask agent to fix in the left sidebar. Generated files stay visible, so the build can be inspected and fixed."
              : isLive
                ? "As soon as OmicsBase renders the first valid page, it will appear here as the primary canvas."
                : "Attach data, or ask the agent to import an example dataset (e.g. GlobalPatterns) and build a report."}
          </p>
        </div>
      )}
    </div>
  ) : (
    <section className="grid min-h-0 flex-1 grid-cols-[minmax(240px,300px)_minmax(0,1fr)] overflow-hidden rounded-[28px] border border-border bg-card shadow-sm dark:bg-[#0b0d15]/95 dark:shadow-[0_20px_80px_rgba(0,0,0,0.35)]">
      <WorkspaceFileTree files={files} />
      <div className="flex min-h-0 flex-col overflow-hidden bg-background">
        <div className="flex shrink-0 items-center gap-2 border-b border-border bg-muted/40">
          <div className="flex min-w-0 flex-1 items-stretch overflow-x-auto">
            {openTabs.length ? (
              openTabs.map((path) => {
                const active = path === activeTab;
                const dirty = dirtyTabs.has(path);
                return (
                  <div
                    key={path}
                    className={"group inline-flex max-w-[12rem] items-center gap-1 border-r border-border px-1 py-1 text-[12px] transition-colors " + (
                      active
                        ? "bg-background text-foreground"
                        : "bg-transparent text-muted-foreground hover:bg-muted/70 hover:text-foreground"
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => selectTab(path)}
                      className="inline-flex min-w-0 flex-1 items-center gap-1.5 px-2 py-1 text-left"
                      title={path}
                    >
                      <FileTypeIcon name={tabLabel(path)} isDir={false} />
                      <span className="truncate">{tabLabel(path)}</span>
                      {dirty ? <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" aria-label="Unsaved" /> : null}
                    </button>
                    <button
                      type="button"
                      onClick={() => files.closeTab(path)}
                      className="rounded p-1 text-muted-foreground opacity-70 transition hover:bg-muted hover:text-foreground group-hover:opacity-100"
                      title="Close tab"
                      aria-label={"Close " + tabLabel(path)}
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                );
              })
            ) : (
              <div className="px-3 py-2 text-[12px] text-muted-foreground">No open files</div>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2 px-2 py-1.5">
            {fileSaveError ? (
              <span className="max-w-[260px] truncate text-[11px] text-red-600 dark:text-red-300" title={fileSaveError}>
                Save conflict — reload before retrying
              </span>
            ) : null}
            {saveMutation.isSuccess && !isDirty && !fileSaveError ? (
              <span className="inline-flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
                <Check className="h-3 w-3" />
                Saved
              </span>
            ) : null}
            {isTabularPath(activeTab) ? (
              <div className="inline-flex overflow-hidden rounded-md border border-border">
                <button
                  type="button"
                  onClick={() => setDataViewMode("table")}
                  className={"px-2 py-1 text-[11px] " + (
                    dataViewMode === "table" ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground"
                  )}
                >
                  Table
                </button>
                {isEditableTabularPath(activeTab) ? (
                  <button
                    type="button"
                    onClick={() => setDataViewMode("source")}
                    className={"border-l border-border px-2 py-1 text-[11px] " + (
                      dataViewMode === "source" ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground"
                    )}
                  >
                    Source
                  </button>
                ) : null}
              </div>
            ) : null}
            {(activeTab?.endsWith(".R") || activeTab?.endsWith(".qmd")) ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => runChunkMutation.mutate()}
                disabled={runChunkMutation.isPending}
                className="h-7 gap-1 bg-blue-600/80 px-2 text-[11px] text-white hover:bg-blue-500"
              >
                {runChunkMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
                Run
              </Button>
            ) : null}
            {activeTab ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={toggleActiveLock}
                disabled={locksMutation.isPending || !project?.project_dir}
                className="h-7 gap-1 px-2 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
                title={lockedPaths.includes(activeTab) ? "Unlock for agent edits" : "Lock against agent edits"}
              >
                {lockedPaths.includes(activeTab) ? <Lock className="h-3 w-3 text-amber-500" /> : <Unlock className="h-3 w-3" />}
                {lockedPaths.includes(activeTab) ? "Locked" : "Lock"}
              </Button>
            ) : null}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => saveMutation.mutate()}
              disabled={!isDirty || saveMutation.isPending || !activeFileEditable || isImagePath(activeTab) || (isTabularPath(activeTab) && !isEditableTabularPath(activeTab))}
              className="h-7 gap-1 bg-teal-600/80 px-2 text-[11px] text-white hover:bg-teal-500 disabled:opacity-40"
            >
              {saveMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
              Save
            </Button>
          </div>
        </div>

        <div className="min-h-0 flex-1 bg-muted/40 dark:bg-[#1e1e1e]">
          {!activeTab ? (
            <div className="flex h-full items-center justify-center p-4 text-center text-xs text-muted-foreground">
              Select a file to inspect or edit the project source.
            </div>
          ) : isImagePath(activeTab) ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 p-6">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={api.getRawFileUrl(projectId, activeTab)}
                alt={tabLabel(activeTab)}
                className="max-h-full max-w-full rounded-lg border border-border object-contain shadow-sm"
              />
              <p className="font-mono text-[11px] text-muted-foreground">{activeTab}</p>
            </div>
          ) : showTableView ? (
            previewLoading ? (
              <div className="flex h-full items-center justify-center">
                <Loader2 className="h-5 w-5 animate-spin text-teal-400" />
              </div>
            ) : filePreview ? (
              <TabularPreview preview={filePreview} />
            ) : (
              <div className="flex h-full items-center justify-center p-4 text-center text-xs text-muted-foreground">
                Could not preview this table.
              </div>
            )
          ) : !isTextPath(activeTab) ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
              <FileTypeIcon name={tabLabel(activeTab)} isDir={false} />
              <div>
                <p className="text-sm font-medium text-foreground">Binary project file</p>
                <p className="mt-1 max-w-md text-xs leading-5 text-muted-foreground">
                  This file is visible in the project tree but cannot be rendered as text in Monaco.
                  {activeFileReadOnly ? " It is also protected from browser edits." : " Download it to inspect the original bytes."}
                </p>
              </div>
              <a
                href={api.getRawFileUrl(projectId, activeTab)}
                download
                className="rounded-md border border-border px-3 py-1.5 text-xs text-foreground transition hover:bg-muted"
              >
                Download {tabLabel(activeTab)}
              </a>
            </div>
          ) : contentLoading ? (
            <div className="flex h-full items-center justify-center">
              <Loader2 className="h-5 w-5 animate-spin text-teal-400" />
            </div>
          ) : (
            <div className="relative h-full w-full">
              {inlineWidget.show ? (
                <InlineAiWidget
                  top={inlineWidget.top}
                  left={inlineWidget.left}
                  selectedText={inlineWidget.selectionText}
                  onGenerate={handleInlineGenerate}
                  onAccept={handleInlineAccept}
                  onReject={handleInlineReject}
                  onClose={handleInlineReject}
                  isGenerating={inlineWidget.isGenerating}
                  hasGenerated={inlineWidget.hasGenerated}
                  diffStats={inlineWidget.diffStats}
                />
              ) : null}
              <Editor
                key={activeTab}
                height="100%"
                language={getLanguage(activeTab)}
                theme={resolvedTheme === "light" ? "light" : "vs-dark"}
                value={editorValue}
                onChange={(value) => updateActiveDraft(value ?? "")}
                onMount={(editor, monaco) => {
                  monacoEditorRef.current = editor;
                  monacoRef.current = monaco;
                  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => saveMutationRef.current());
                  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyK, triggerInlineAi);
                  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyI, triggerInlineAi);
                }}
                options={{
                  fontSize: 13,
                  readOnly: !activeFileEditable,
                  minimap: { enabled: false },
                  scrollBeyondLastLine: false,
                  wordWrap: "on",
                  automaticLayout: true,
                  padding: { top: 8, bottom: 8 },
                  tabSize: 2,
                  lineNumbersMinChars: 3,
                }}
              />
            </div>
          )}
        </div>
      </div>
    </section>)}
      {showConflictDialog ? (
        <SaveConflictDialog
          message={fileSaveError}
          onClose={closeConflictDialog}
          onReload={handleConflictReload}
        />
      ) : null}
    </>
  );
}
