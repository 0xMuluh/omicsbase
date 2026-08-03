"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

interface ThreadOverviewRailProps {
  containerRef: RefObject<HTMLDivElement | null>;
  refreshKey?: string;
}

interface PromptMarker {
  id: string;
  text: string;
  topPct: number;
}

const DASH_HEIGHT = 2;
const DASH_SPACING = 5;
const RAIL_GAP = 6;
const ACTIVE_OFFSET = 120;
const RAIL_HEIGHT_PX = 170;

export function ThreadOverviewRail({ containerRef, refreshKey }: ThreadOverviewRailProps) {
  const [prompts, setPrompts] = useState<PromptMarker[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const [open, setOpen] = useState(false);
  const [overflowing, setOverflowing] = useState(false);
  const [rightInset, setRightInset] = useState(22);
  const railRef = useRef<HTMLDivElement>(null);
  const promptTopsRef = useRef<number[]>([]);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const updateActiveIndex = (container: HTMLElement, count: number) => {
    if (count === 0) {
      setActiveIndex(0);
      return;
    }
    const position = container.scrollTop + ACTIVE_OFFSET;
    let index = 0;
    for (let i = 0; i < promptTopsRef.current.length; i += 1) {
      if (promptTopsRef.current[i] <= position) index = i;
      else break;
    }
    setActiveIndex(Math.min(index, count - 1));
  };

  const measure = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const scrollHeight = Math.max(1, container.scrollHeight);
    const clientHeight = container.clientHeight;
    setOverflowing(scrollHeight > clientHeight + 1);

    const column = container.querySelector<HTMLElement>("[data-thread-column]");
    if (!column || scrollHeight <= clientHeight + 1) {
      promptTopsRef.current = [];
      setPrompts([]);
      return;
    }

    const containerRect = container.getBoundingClientRect();
    const promptElements = Array.from(
      container.querySelectorAll<HTMLElement>("[data-overview-block][data-overview-type='user']"),
    );
    const count = promptElements.length;
    const tops: number[] = [];
    const railHeight = Math.max(1, window.innerHeight - RAIL_HEIGHT_PX);
    const totalHeight = count > 0 ? (count - 1) * DASH_SPACING : 0;
    const startPx = Math.max(0, (railHeight - totalHeight) / 2);
    const next = promptElements.map((element, index) => {
      const rect = element.getBoundingClientRect();
      const top = rect.top - containerRect.top + container.scrollTop;
      tops.push(top);
      const text = (element.textContent || "").replace(/\s+/g, " ").trim();
      return {
        id: element.dataset.overviewId || `prompt-${index}`,
        text: text.slice(0, 180) || `Prompt ${index + 1}`,
        // Centralised: dashes keep a fixed few-pixel spacing and grow from
        // the middle of the rail — never content-anchored, never spread out.
        topPct: count > 0 ? ((startPx + index * DASH_SPACING) / railHeight) * 100 : 0,
      };
    });
    promptTopsRef.current = tops;
    setPrompts(next);
    updateActiveIndex(container, next.length);

    // Keep the rail beside, not on top of, the native scrollbar.
    const scrollbarWidth = container.offsetWidth - container.clientWidth - container.clientLeft;
    setRightInset(Math.max(20, scrollbarWidth + RAIL_GAP));
  }, [containerRef]);

  useEffect(() => {
    measure();
    const container = containerRef.current;
    if (!container) return;
    let raf = 0;
    const schedule = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(measure);
    };
    const mutationObserver = new MutationObserver(schedule);
    mutationObserver.observe(container, { childList: true, subtree: true });
    const resizeObserver = new ResizeObserver(schedule);
    resizeObserver.observe(container);
    window.addEventListener("resize", schedule);
    return () => {
      mutationObserver.disconnect();
      resizeObserver.disconnect();
      window.removeEventListener("resize", schedule);
      cancelAnimationFrame(raf);
    };
  }, [measure, containerRef, refreshKey]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let raf = 0;
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => updateActiveIndex(container, prompts.length));
    };
    container.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      container.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(raf);
    };
  }, [containerRef, prompts.length]);

  useEffect(() => () => {
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
  }, []);

  if (!overflowing || prompts.length === 0) return null;

  const selectedIndex = hoveredIndex ?? activeIndex;
  const selectedPrompt = prompts[selectedIndex];

  const keepOpen = () => {
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    setOpen(true);
  };

  const scheduleClose = () => {
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    closeTimerRef.current = setTimeout(() => {
      setOpen(false);
      setHoveredIndex(null);
    }, 140);
  };

  return (
    <div
      ref={railRef}
      className="pointer-events-auto absolute top-3 z-10 h-[calc(100dvh-170px)] w-3"
      style={{ right: rightInset }}
      role="navigation"
      aria-label="Thread prompt overview"
      onMouseEnter={keepOpen}
      onMouseLeave={scheduleClose}
    >
      <div className="absolute inset-0 overflow-hidden">
        {prompts.map((prompt, index) => {
          const active = index === selectedIndex;
          return (
            <div
              key={prompt.id}
              className={
                "absolute left-0 w-full rounded-full transition-colors " +
                (active ? "bg-foreground/80 shadow-[0_0_8px_2px_rgba(128,128,128,0.35)]" : "bg-foreground/35")
              }
              style={{
                top: `calc(${prompt.topPct}% - ${DASH_HEIGHT / 2}px)`,
                height: DASH_HEIGHT,
              }}
              onMouseEnter={() => {
                keepOpen();
                setHoveredIndex(index);
              }}
            />
          );
        })}
      </div>

      {open && selectedPrompt ? (
        <div
          className="absolute right-[calc(100%+12px)] top-1/2 w-[min(22rem,75vw)] -translate-y-1/2 overflow-y-auto rounded-2xl border border-border bg-popover p-1.5 text-xs text-popover-foreground shadow-2xl"
          style={{ maxHeight: "min(62vh, 520px)" }}
          onMouseEnter={keepOpen}
          onMouseLeave={scheduleClose}
        >
          {prompts.map((prompt, index) => (
            <button
              key={prompt.id}
              type="button"
              className={
                "block w-full truncate rounded-xl px-3 py-2 text-left transition-colors " +
                (index === selectedIndex ? "bg-muted font-medium" : "hover:bg-muted/70")
              }
              title={prompt.text}
              onClick={() => {
                const element = containerRef.current?.querySelector(`[data-overview-id="${prompt.id}"]`);
                element?.scrollIntoView({
                  block: "start",
                  behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
                });
              }}
            >
              {prompt.text}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
