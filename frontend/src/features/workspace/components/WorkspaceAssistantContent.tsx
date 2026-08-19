"use client";

import { MarkdownRenderer } from "@/components/MarkdownRenderer";

export function WorkspaceAssistantContent({
  content,
  reasoning,
  reasoningOpen = false,
}: {
  content: string;
  reasoning?: string | null;
  reasoningOpen?: boolean;
}) {
  const thinking = (reasoning || "").trim();
  const response = (content || "").trim();

  return (
    <div className="w-full space-y-2">
      {thinking ? (
        <details
          open={reasoningOpen}
          className="rounded-2xl border border-border/70 bg-muted/30 px-3 py-2 text-xs text-muted-foreground"
        >
          <summary className="cursor-pointer select-none font-medium text-foreground/80">
            Thinking
          </summary>
          <div className="mt-2 leading-6">
            <MarkdownRenderer content={thinking} className="text-xs" />
          </div>
        </details>
      ) : null}
      {response ? (
        <div className="text-sm leading-7 text-foreground">
          <MarkdownRenderer content={response} />
        </div>
      ) : null}
    </div>
  );
}
