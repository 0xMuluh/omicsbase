"use client";

import Editor from "@monaco-editor/react";
import { FilePreview } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { InlineAiWidget } from "@/components/InlineAiWidget";
import { TabularPreview } from "./TabularPreview";
import {
  Code2,
  FileCode,
  FileText,
  FlaskConical,
  Play,
  Save,
  Table2,
  X,
} from "lucide-react";

interface WorkspaceEditorPanelProps {
  openTabs: string[];
  activeTab: string | null;
  setActiveTab: (path: string) => void;
  onCloseTab: (path: string) => void;
  editorValue: string;
  onEditorChange: (val: string | undefined) => void;
  isDirty: boolean;
  onSaveFile: () => void;
  showTableView: boolean;
  filePreview?: FilePreview | null;
  dataViewMode: "table" | "source";
  setDataViewMode: (mode: "table" | "source") => void;
  workspaceMode: "preview" | "code";
  setWorkspaceMode: (mode: "preview" | "code") => void;
  reportUrl: string;
  iframeKey: number;
  monacoTheme: string;
  monacoEditorRef: React.MutableRefObject<any>;
  onTriggerInlineAi: () => void;
  inlineWidget: {
    show: boolean;
    top: number;
    left: number;
    selectionText?: string;
    range?: any;
    originalCode?: string;
    isGenerating: boolean;
    hasGenerated: boolean;
    diffStats?: { added: number; removed: number };
  };
  onInlineGenerate: (prompt: string) => void;
  onInlineAccept: () => void;
  onInlineReject: () => void;
}

export function WorkspaceEditorPanel({
  openTabs,
  activeTab,
  setActiveTab,
  onCloseTab,
  editorValue,
  onEditorChange,
  isDirty,
  onSaveFile,
  showTableView,
  filePreview,
  dataViewMode,
  setDataViewMode,
  workspaceMode,
  setWorkspaceMode,
  reportUrl,
  iframeKey,
  monacoTheme,
  monacoEditorRef,
  onTriggerInlineAi,
  inlineWidget,
  onInlineGenerate,
  onInlineAccept,
  onInlineReject,
}: WorkspaceEditorPanelProps) {
  const getLanguage = (path: string | null) => {
    if (!path) return "plaintext";
    const ext = path.split(".").pop()?.toLowerCase();
    switch (ext) {
      case "r":
        return "r";
      case "qmd":
      case "md":
        return "markdown";
      case "yml":
      case "yaml":
        return "yaml";
      case "json":
        return "json";
      case "html":
        return "html";
      case "css":
        return "css";
      case "js":
      case "ts":
      case "tsx":
        return "typescript";
      default:
        return "plaintext";
    }
  };

  const isTabular = activeTab && /\.(csv|tsv|xlsx|xls|sav)$/i.test(activeTab);

  return (
    <div className="relative flex h-full flex-col bg-background">
      {/* Single Unified Tab Bar */}
      <div className="flex h-9 shrink-0 items-center justify-between border-b border-border bg-muted/40 px-2">
        {/* Open File Tabs or 'No open files' label */}
        <div className="flex items-center gap-1 overflow-x-auto">
          {openTabs.length > 0 ? (
            openTabs.map((path) => {
              const label = path.split("/").pop() || path;
              const isActive = activeTab === path;
              return (
                <div
                  key={path}
                  onClick={() => setActiveTab(path)}
                  className={`group flex cursor-pointer items-center gap-1.5 border-r border-border px-3 py-1 text-xs transition-colors ${
                    isActive
                      ? "border-b-2 border-b-teal-500 bg-background font-medium text-foreground"
                      : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                  }`}
                >
                  <span className="max-w-[140px] truncate">{label}</span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onCloseTab(path);
                    }}
                    className="rounded p-0.5 opacity-60 hover:bg-muted hover:opacity-100"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              );
            })
          ) : (
            <span className="text-xs text-muted-foreground/80 px-2">No open files</span>
          )}
        </div>

        {/* Right side controls: Tabular View Mode & Save Button */}
        <div className="flex items-center gap-2">
          {isTabular && (
            <div className="flex items-center gap-1 rounded bg-muted/60 p-0.5 text-[11px]">
              <button
                onClick={() => setDataViewMode("table")}
                className={`rounded px-2 py-0.5 ${
                  dataViewMode === "table" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"
                }`}
              >
                Table
              </button>
              <button
                onClick={() => setDataViewMode("source")}
                className={`rounded px-2 py-0.5 ${
                  dataViewMode === "source" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"
                }`}
              >
                Source
              </button>
            </div>
          )}

          <Button
            size="sm"
            variant="outline"
            onClick={onSaveFile}
            disabled={!activeTab || !isDirty}
            className="h-7 gap-1 px-2.5 text-xs border-teal-600/40 text-teal-400 hover:bg-teal-600/10 disabled:opacity-40"
          >
            <Save className="h-3.5 w-3.5" />
            <span>Save</span>
          </Button>
        </div>
      </div>

      {/* Main Panel Content */}
      <div className="relative flex-1 min-h-0">
        {workspaceMode === "preview" ? (
          <iframe
            key={iframeKey}
            src={reportUrl}
            className="h-full w-full border-0 bg-white"
            title="Quarto Rendered Report Preview"
          />
        ) : showTableView && filePreview ? (
          <TabularPreview preview={filePreview} />
        ) : activeTab ? (
          <Editor
            height="100%"
            language={getLanguage(activeTab)}
            theme={monacoTheme}
            value={editorValue}
            onChange={onEditorChange}
            onMount={(editor) => {
              monacoEditorRef.current = editor;
              editor.addCommand(
                (window as any).monaco?.KeyMod.CtrlCmd | (window as any).monaco?.KeyCode.KeyK,
                onTriggerInlineAi
              );
            }}
            options={{
              fontSize: 13,
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              wordWrap: "on",
              padding: { top: 12 },
            }}
          />
        ) : (
          <div className="flex h-full items-center justify-center p-6 text-center text-xs text-muted-foreground">
            Select a file to inspect or edit the generated source.
          </div>
        )}

        {/* Monaco Inline AI Overlay Widget (Cmd+K) */}
        {inlineWidget.show && (
          <InlineAiWidget
            top={inlineWidget.top}
            left={inlineWidget.left}
            selectedText={inlineWidget.selectionText}
            isGenerating={inlineWidget.isGenerating}
            hasGenerated={inlineWidget.hasGenerated}
            diffStats={inlineWidget.diffStats}
            onGenerate={onInlineGenerate}
            onAccept={onInlineAccept}
            onReject={onInlineReject}
            onClose={onInlineReject}
          />
        )}
      </div>
    </div>
  );
}
