"use client";

import { useState, useRef } from "react";
import { Job, Project } from "@/lib/api";

export interface InlineWidgetState {
  show: boolean;
  top: number;
  left: number;
  selectionText?: string;
  range?: any;
  originalCode?: string;
  isGenerating: boolean;
  hasGenerated: boolean;
  diffStats?: { added: number; removed: number };
}

export function useMonacoInlineAi({
  project,
  activeTab,
  latestFailedJob,
  onUpdateDraft,
}: {
  project?: Project | null;
  activeTab: string | null;
  latestFailedJob?: Job | null;
  onUpdateDraft: (content: string) => void;
}) {
  const monacoEditorRef = useRef<any>(null);
  const inlineDecorationsRef = useRef<string[]>([]);

  const [inlineWidget, setInlineWidget] = useState<InlineWidgetState>({
    show: false,
    top: 20,
    left: 40,
    isGenerating: false,
    hasGenerated: false,
  });

  const clearInlineDiffDecorations = () => {
    const editor = monacoEditorRef.current;
    if (editor && inlineDecorationsRef.current.length) {
      inlineDecorationsRef.current = editor.deltaDecorations(inlineDecorationsRef.current, []);
    }
  };

  const triggerInlineAi = () => {
    const editor = monacoEditorRef.current;
    if (!editor) return;
    const pos = editor.getScrolledVisiblePosition(editor.getPosition());
    const selection = editor.getModel()?.getValueInRange(editor.getSelection());
    const fullContent = editor.getValue();

    clearInlineDiffDecorations();

    setInlineWidget({
      show: true,
      top: (pos?.top ?? 40) + 30,
      left: Math.min((pos?.left ?? 40) + 40, 450),
      selectionText: selection,
      range: editor.getSelection(),
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
    const selectedText = model.getValueInRange(selection);
    const fullContent = editor.getValue();

    setInlineWidget((prev) => ({ ...prev, isGenerating: true }));

    const projectCtx = project
      ? `Project: ${project.name}\nQuestion: ${project.question || ""}\nDataset: ${project.agent_memory?.summary || ""}`
      : undefined;
    const errorCtx = latestFailedJob ? `Error detail: ${latestFailedJob.error || ""}` : undefined;

    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const response = await fetch(`${baseUrl}/api/inline-edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: activeTab,
          prompt: prompt,
          selection: selectedText || null,
          content: fullContent,
          project_context: projectCtx,
          error_context: errorCtx,
        }),
      });

      if (!response.ok || !response.body) {
        throw new Error("Failed to start inline edit stream");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let streamedTokens = "";
      const startLine = selection ? selection.startLineNumber : 1;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split("\n");

        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const data = JSON.parse(line);
            if (data.type === "token" && data.token) {
              // Accumulate off-model; applying each token to a fixed Monaco
              // range corrupts offsets and can leave a partial saveable edit.
              streamedTokens += data.token;
            }
          } catch {
            // Ignore parse errors
          }
        }
      }

      if (!streamedTokens) {
        throw new Error("Inline edit provider returned an empty preview");
      }
      editor.executeEdits("inline-ai-preview", [{
        range: selectedText ? selection : model.getFullModelRange(),
        text: streamedTokens,
        forceMoveMarkers: true,
      }]);

      const originalLines = (selectedText || fullContent).split("\n").length;
      const streamedLines = streamedTokens.split("\n").length;
      const added = Math.max(0, streamedLines - originalLines);
      const removed = Math.max(0, originalLines - streamedLines);

      const monacoWindow = (window as any).monaco;
      if (monacoWindow && editor) {
        const endLine = startLine + streamedLines - 1;
        inlineDecorationsRef.current = editor.deltaDecorations(
          inlineDecorationsRef.current,
          [
            {
              range: new monacoWindow.Range(startLine, 1, Math.max(startLine, endLine), 1),
              options: {
                isWholeLine: true,
                className: "bg-emerald-500/15 border-l-2 border-emerald-400",
              },
            },
          ]
        );
      }

      setInlineWidget((prev) => ({
        ...prev,
        isGenerating: false,
        hasGenerated: true,
        diffStats: { added, removed },
      }));
    } catch (err) {
      console.error("Inline AI edit failed:", err);
      setInlineWidget((prev) => ({ ...prev, isGenerating: false }));
    }
  };

  const handleInlineAccept = () => {
    clearInlineDiffDecorations();
    if (monacoEditorRef.current) {
      onUpdateDraft(monacoEditorRef.current.getValue() || "");
    }
    setInlineWidget({ show: false, top: 20, left: 40, isGenerating: false, hasGenerated: false });
  };

  const handleInlineReject = () => {
    clearInlineDiffDecorations();
    if (inlineWidget.originalCode !== undefined && monacoEditorRef.current) {
      monacoEditorRef.current.setValue(inlineWidget.originalCode);
    }
    setInlineWidget({ show: false, top: 20, left: 40, isGenerating: false, hasGenerated: false });
  };

  return {
    monacoEditorRef,
    inlineWidget,
    triggerInlineAi,
    handleInlineGenerate,
    handleInlineAccept,
    handleInlineReject,
  };
}
