"use client";

import type { ReactNode } from "react";

import { MarkdownRenderer } from "@/components/MarkdownRenderer";

export function NoteEmptyState({
  composer,
  liveTurnText = "",
  selectedThreadId,
  turnStreaming = false,
}: {
  composer: ReactNode;
  liveTurnText?: string;
  selectedThreadId?: string | null;
  turnStreaming?: boolean;
}) {
  return (
    <div className="flex min-h-full items-center justify-center p-8">
      <div className="w-full max-w-2xl">
        <div className="mb-8 text-center">
          <h1 className="font-display text-4xl font-medium tracking-tight text-foreground sm:text-5xl">
            See beyond the counts.
          </h1>
          <p className="mx-auto mt-3 max-w-xl text-sm leading-6 text-muted-foreground">
            Create downstream omics reports by chatting with OmicsBase.
          </p>
        </div>
        {composer}
        {turnStreaming && liveTurnText ? (
          <article
            data-overview-block
            data-overview-type="assistant"
            data-overview-id={(selectedThreadId || "new") + "-live-turn"}
            className="relative w-full py-1"
          >
            <MarkdownRenderer content={liveTurnText} className="text-base" />
          </article>
        ) : null}
      </div>
    </div>
  );
}

