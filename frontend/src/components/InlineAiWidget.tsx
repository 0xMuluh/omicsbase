"use client";

import React, { useState, useEffect, useRef } from "react";
import { Sparkles, ArrowRight, Check, X, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";

interface InlineAiWidgetProps {
  top: number;
  left: number;
  selectedText?: string;
  onGenerate: (prompt: string) => void;
  onAccept: () => void;
  onReject: () => void;
  onClose: () => void;
  isGenerating: boolean;
  hasGenerated: boolean;
  diffStats?: { added: number; removed: number };
}

export function InlineAiWidget({
  top,
  left,
  selectedText,
  onGenerate,
  onAccept,
  onReject,
  onClose,
  isGenerating,
  hasGenerated,
  diffStats,
}: InlineAiWidgetProps) {
  const [prompt, setPrompt] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (prompt.trim() && !isGenerating) {
      onGenerate(prompt.trim());
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && hasGenerated) {
      e.preventDefault();
      onAccept();
    }
  };

  return (
    <div
      style={{
        position: "absolute",
        top: Math.max(10, top),
        left: Math.max(10, left),
        zIndex: 100,
      }}
      className="w-[450px] rounded-xl border border-teal-500/30 bg-card/95 p-3 shadow-2xl backdrop-blur-md dark:bg-[#121520]/95"
      onKeyDown={handleKeyDown}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-semibold text-teal-400">
          <Sparkles className="h-4 w-4 animate-pulse text-teal-400" />
          <span>Inline OmicsBase Edit (Cmd+K)</span>
          {selectedText ? (
            <span className="truncate rounded bg-teal-500/10 px-1.5 py-0.5 font-mono text-[10px] text-teal-300">
              {selectedText.split("\n").length} lines selected
            </span>
          ) : null}
        </div>
        {diffStats && (diffStats.added > 0 || diffStats.removed > 0) ? (
          <div className="flex items-center gap-1.5 font-mono text-[10px] font-medium">
            <span className="text-emerald-400">+{diffStats.added}</span>
            <span className="text-rose-400">-{diffStats.removed}</span>
          </div>
        ) : null}
      </div>

      <form onSubmit={handleSubmit} className="mt-2.5 flex items-center gap-2">
        <input
          ref={inputRef}
          type="text"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Describe code edit (e.g. convert theme, fix syntax, add column)..."
          disabled={isGenerating}
          className="h-9 w-full rounded-lg border border-border bg-background/60 px-3 text-xs text-foreground outline-none placeholder:text-muted-foreground focus:border-teal-500/60 focus:ring-1 focus:ring-teal-500/30"
        />
        <Button
          type="submit"
          size="sm"
          disabled={!prompt.trim() || isGenerating}
          className="h-9 shrink-0 gap-1 rounded-lg bg-teal-600 px-3 text-xs text-white hover:bg-teal-500 disabled:opacity-50"
        >
          {isGenerating ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <ArrowRight className="h-3.5 w-3.5" />
          )}
        </Button>
      </form>

      {hasGenerated || isGenerating ? (
        <div className="mt-2.5 flex items-center justify-between border-t border-border/60 pt-2 text-[11px]">
          <span className="text-muted-foreground">
            {isGenerating ? "Streaming code into editor..." : "Review changes in editor"}
          </span>
          <div className="flex items-center gap-1.5">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={onReject}
              className="h-7 gap-1 px-2 text-[11px] text-red-400 hover:bg-red-500/10 hover:text-red-300"
            >
              <X className="h-3 w-3" />
              Reject (Esc)
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={onAccept}
              disabled={isGenerating}
              className="h-7 gap-1 rounded bg-teal-600 px-2.5 text-[11px] text-white hover:bg-teal-500"
            >
              <Check className="h-3 w-3" />
              Accept (Cmd+Enter)
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-2 flex items-center justify-between text-[10px] text-muted-foreground">
          <span>Press Enter to generate</span>
          <span>Esc to cancel</span>
        </div>
      )}
    </div>
  );
}
