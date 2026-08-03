"use client";

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "omicsbase:reuse-cache";
const CHANGE_EVENT = "omicsbase:reuse-cache-change";

export function useReuseCache(): [boolean, (value: boolean) => void] {
  const [reuse, setReuse] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  });

  useEffect(() => {
    const onChange = (event: Event) => {
      setReuse(Boolean((event as CustomEvent<boolean>).detail));
    };
    window.addEventListener(CHANGE_EVENT, onChange);
    return () => window.removeEventListener(CHANGE_EVENT, onChange);
  }, []);

  const applyReuse = useCallback((value: boolean) => {
    window.localStorage.setItem(STORAGE_KEY, value ? "1" : "0");
    window.dispatchEvent(new CustomEvent<boolean>(CHANGE_EVENT, { detail: value }));
    setReuse(value);
  }, []);

  return [reuse, applyReuse];
}
