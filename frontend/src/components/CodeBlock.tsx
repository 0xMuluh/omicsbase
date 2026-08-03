"use client";

import { useMemo } from "react";
import Prism from "prismjs";
import "prismjs/components/prism-r";
import "prismjs/components/prism-bash";
import "prismjs/components/prism-python";
import "prismjs/components/prism-json";

import { useCodeTheme } from "@/lib/use-code-theme";

interface CodeBlockProps {
  code: string;
  language?: string | null;
  className?: string;
}

function normaliseLanguage(language?: string | null): string {
  const value = (language || "r").trim().toLowerCase();
  if (value === "rscript") return "r";
  return value;
}

export function CodeBlock({ code, language, className = "" }: CodeBlockProps) {
  const [theme] = useCodeTheme();
  const lang = normaliseLanguage(language);
  const grammar = language ? Prism.languages[lang] || null : null;
  const highlighted = useMemo(
    () => (grammar ? Prism.highlight(code, grammar, lang) : null),
    [code, grammar, lang],
  );
  const dark = theme === "dark";
  return (
    <pre className={"code-theme-" + theme + " overflow-x-auto whitespace-pre-wrap rounded-lg border border-border p-2 font-mono text-sm leading-6 " + (dark ? "bg-slate-900 text-slate-100" : "bg-white text-slate-800") + " " + className}>
      {highlighted !== null ? (
        <code className={`language-${lang}`} dangerouslySetInnerHTML={{ __html: highlighted }} />
      ) : (
        <code>{code}</code>
      )}
    </pre>
  );
}
