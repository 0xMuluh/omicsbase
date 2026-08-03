"use client";

import { useCallback, useEffect, useState } from "react";

export type CodeTheme = "light" | "dark";

const STORAGE_KEY = "omicsbase:code-theme";
const CHANGE_EVENT = "omicsbase:code-theme-change";

export function useCodeTheme(): [CodeTheme, (theme: CodeTheme) => void] {
  const [theme, setTheme] = useState<CodeTheme>(() => {
    if (typeof window === "undefined") return "dark";
    return window.localStorage.getItem(STORAGE_KEY) === "light" ? "light" : "dark";
  });

  useEffect(() => {
    const onChange = (event: Event) => {
      const next = (event as CustomEvent<CodeTheme>).detail;
      setTheme(next === "light" ? "light" : "dark");
    };
    window.addEventListener(CHANGE_EVENT, onChange);
    return () => window.removeEventListener(CHANGE_EVENT, onChange);
  }, []);

  const applyTheme = useCallback((next: CodeTheme) => {
    window.localStorage.setItem(STORAGE_KEY, next);
    window.dispatchEvent(new CustomEvent<CodeTheme>(CHANGE_EVENT, { detail: next }));
    setTheme(next);
  }, []);

  return [theme, applyTheme];
}
