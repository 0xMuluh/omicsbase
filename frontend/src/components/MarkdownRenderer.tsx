"use client";

import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CodeBlock } from "@/components/CodeBlock";

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

export function MarkdownRenderer({ content, className = "" }: MarkdownRendererProps) {
  return (
    <div className={`markdown-body space-y-4 text-base leading-relaxed ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <h1 className="mt-6 mb-3 text-2xl font-bold tracking-tight text-foreground">{children}</h1>,
          h2: ({ children }) => <h2 className="mt-5 mb-2.5 text-xl font-semibold tracking-tight text-foreground">{children}</h2>,
          h3: ({ children }) => <h3 className="mt-4 mb-2 text-lg font-semibold text-foreground">{children}</h3>,
          h4: ({ children }) => <h4 className="mt-3 mb-1.5 text-base font-semibold text-foreground">{children}</h4>,
          p: ({ children }) => <p className="mb-3 leading-relaxed text-foreground/90 last:mb-0">{children}</p>,
          ul: ({ children }) => <ul className="mb-3.5 ml-5 list-disc space-y-1.5 text-foreground/90">{children}</ul>,
          ol: ({ children }) => <ol className="mb-3.5 ml-5 list-decimal space-y-1.5 text-foreground/90">{children}</ol>,
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
          em: ({ children }) => <em className="italic text-foreground">{children}</em>,
          code: ({ className: codeClassName, children, ...props }) => {
            const match = /language-(\w+)/.exec(codeClassName || "");
            const isInline = !match && !String(children).includes("\n");
            return isInline ? (
              <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-sm text-teal-400 dark:text-teal-300" {...props}>
                {children}
              </code>
            ) : (
              <CodeBlock code={String(children).replace(/\n$/, "")} language={match ? match[1] : null} />
            );
          },
          pre: ({ children }) => <>{children}</>,
          table: ({ children }) => (
            <div className="my-4 w-full overflow-x-auto rounded-xl border border-border bg-card/60 shadow-sm">
              <table className="w-full border-collapse text-left text-sm">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="border-b border-border bg-muted/80">{children}</thead>,
          tbody: ({ children }) => <tbody className="divide-y divide-border/50">{children}</tbody>,
          tr: ({ children }) => <tr className="transition-colors hover:bg-muted/40">{children}</tr>,
          th: ({ children }) => <th className="px-4 py-3 font-semibold text-foreground">{children}</th>,
          td: ({ children }) => <td className="px-4 py-2.5 text-foreground/90">{children}</td>,
          blockquote: ({ children }) => (
            <blockquote className="my-3.5 border-l-3 border-teal-500 pl-4 italic text-muted-foreground">
              {children}
            </blockquote>
          ),
          hr: () => <hr className="my-5 border-border/60" />,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
