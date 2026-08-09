"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface ExecutionOutputProps {
  stdout: string;
  truncated: boolean;
}

const SUBSTANTIAL_LINE_COUNT = 40;
const SUBSTANTIAL_CHAR_COUNT = 4000;

export function ExecutionOutput({ stdout, truncated }: ExecutionOutputProps) {
  const [expanded, setExpanded] = useState(false);
  const lines = stdout.split("\n").length;
  const substantial = truncated || lines > SUBSTANTIAL_LINE_COUNT || stdout.length > SUBSTANTIAL_CHAR_COUNT;
  return (
    <div className="relative">
      <pre className={"overflow-auto whitespace-pre-wrap rounded-lg border border-border bg-background p-2 font-mono text-sm leading-6 text-muted-foreground " + (expanded ? "max-h-[85vh]" : "max-h-64")}>
        {stdout}
      </pre>
      {substantial ? (
        <div className="mt-1 flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
          <span>Output · {lines} lines{truncated ? " · preview truncated" : ""}</span>
          <Tooltip>
            <TooltipTrigger render={
              <Button size="icon-xs" variant="ghost" aria-label={expanded ? "Collapse output" : "Expand output"} onClick={() => setExpanded((value) => !value)}>
                {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
              </Button>
            } />
            <TooltipContent>{expanded ? "Collapse output" : "Expand output"}</TooltipContent>
          </Tooltip>
        </div>
      ) : null}
    </div>
  );
}
