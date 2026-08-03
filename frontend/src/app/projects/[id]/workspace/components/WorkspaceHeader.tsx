"use client";

import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ThemeToggle } from "@/components/ThemeToggle";
import { SidebarProjectItem } from "@/components/ProjectsSidebar";
import { Project } from "@/lib/api";
import {
  ChevronDown,
  ChevronsLeft,
  ChevronsRight,
  Code,
  Code2,
  Download,
  ExternalLink,
  FlaskConical,
  Globe,
  MessageSquare,
  Plus,
  RotateCw,
} from "lucide-react";

interface WorkspaceHeaderProps {
  project?: Project | null;
  projectsList?: Project[];
  sidebarOpen: boolean;
  setSidebarOpen: (open: boolean) => void;
  showProjectMenu: boolean;
  setShowProjectMenu: (show: boolean) => void;
  showHistory: boolean;
  setShowHistory: (show: boolean) => void;
  viewMode: "chat" | "workspace" | "code";
  setViewMode: (mode: "chat" | "workspace" | "code") => void;
  workspaceMode?: "preview" | "code";
  setWorkspaceMode?: (mode: "preview" | "code") => void;
  hasPreview: boolean;
  reportUrl: string;
  downloadUrl: string;
  statusTone: (status: string | null | undefined) => string;
  stateCopy: Record<string, string>;
  agentActivity: string;
  isSimpleChat?: boolean;
}

export function WorkspaceHeader({
  project,
  projectsList,
  sidebarOpen,
  setSidebarOpen,
  showProjectMenu,
  setShowProjectMenu,
  viewMode,
  setViewMode,
  workspaceMode,
  setWorkspaceMode,
  hasPreview,
  reportUrl,
  downloadUrl,
  statusTone,
  stateCopy,
  isSimpleChat = false,
  onReloadIframe,
}: WorkspaceHeaderProps & { onReloadIframe?: () => void }) {
  // Simple Chat Mode Header: Clean & Minimal with OmicsBase Logo
  if (isSimpleChat) {
    return (
      <header className="flex h-12 shrink-0 items-center justify-between px-3 bg-background border-b border-border/40">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 text-muted-foreground hover:text-foreground"
            onClick={() => setSidebarOpen(!sidebarOpen)}
            title={sidebarOpen ? "Hide projects" : "Show projects"}
          >
            {sidebarOpen ? <ChevronsLeft className="h-4 w-4" /> : <ChevronsRight className="h-4 w-4" />}
          </Button>

          {!sidebarOpen && (
            <Link href="/" className="flex items-center gap-2 text-sm font-semibold hover:opacity-80 transition">
              <div className="flex h-6 w-6 items-center justify-center rounded-md bg-teal-600 text-white">
                <FlaskConical className="h-3.5 w-3.5" />
              </div>
              <span className="font-display font-medium tracking-tight text-foreground">OmicsBase</span>
            </Link>
          )}
        </div>
        <div className="flex items-center gap-2">
          <ThemeToggle />
        </div>
      </header>
    );
  }

  // Full Workspace Header for QMD / Generated Projects
  return (
    <header className="flex h-11 shrink-0 items-center justify-between border-b border-border bg-background px-3">
      {/* Left: Mode Toggle Pills + Re-open Sidebar Button */}
      <div className="flex items-center gap-2">
        {!sidebarOpen && (
          <button
            type="button"
            onClick={() => setSidebarOpen(true)}
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            title="Open sidebar"
          >
            <ChevronsRight className="h-4 w-4" />
          </button>
        )}

        {/* Primary View Mode Switcher: Chat vs Workspace */}
        <div className="inline-flex shrink-0 items-center rounded-full border border-border bg-muted/60 p-0.5">
          <button
            type="button"
            onClick={() => setViewMode("chat")}
            className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors ${
              viewMode === "chat"
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            <MessageSquare className="h-3.5 w-3.5" />
            Chat
          </button>
          <button
            type="button"
            onClick={() => setViewMode("workspace")}
            className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors ${
              viewMode === "workspace" || viewMode === "code"
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            <Code2 className="h-3.5 w-3.5" />
            Workspace
          </button>
        </div>

        {/* Secondary Sub-Mode Pills in Workspace mode: Preview vs Code */}
        {(viewMode === "workspace" || viewMode === "code") && (
          <div className="ml-2 inline-flex shrink-0 items-center rounded-full border border-border bg-muted p-1">
            <button
              type="button"
              onClick={() => {
                setViewMode("workspace");
                setWorkspaceMode?.("preview");
              }}
              className={`inline-flex items-center gap-1.5 rounded-full transition-colors ${
                workspaceMode === "preview" && viewMode === "workspace"
                  ? "bg-teal-500 px-3 py-1.5 text-xs font-medium text-zinc-950"
                  : "p-1.5 text-muted-foreground hover:text-foreground"
              }`}
              title="Preview"
            >
              <Globe className="h-4 w-4" />
              {workspaceMode === "preview" && viewMode === "workspace" ? <span>Preview</span> : null}
            </button>
            <button
              type="button"
              onClick={() => {
                setViewMode("code");
                setWorkspaceMode?.("code");
              }}
              className={`inline-flex items-center gap-1.5 rounded-full transition-colors ${
                workspaceMode === "code" || viewMode === "code"
                  ? "bg-teal-500 px-3 py-1.5 text-xs font-medium text-zinc-950"
                  : "p-1.5 text-muted-foreground hover:text-foreground"
              }`}
              title="Code"
            >
              <Code2 className="h-4 w-4" />
              {workspaceMode === "code" || viewMode === "code" ? <span>Code</span> : null}
            </button>
          </div>
        )}
      </div>

      {/* Top Right: Theme, Reload, Download ZIP, Open new tab */}
      <div className="flex items-center gap-1.5">
        <ThemeToggle />
        {(viewMode === "workspace" || viewMode === "code") && (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onReloadIframe ? onReloadIframe() : window.location.reload()}
              className="h-8 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground"
            >
              <RotateCw className="h-3.5 w-3.5" />
              Reload
            </Button>
            <a
              href={downloadUrl}
              download
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-muted/60 px-3 text-xs text-foreground/90 transition-colors hover:bg-muted"
            >
              <Download className="h-3.5 w-3.5" />
              Download ZIP
            </a>
            <a
              href={reportUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-teal-500 text-zinc-950 transition-colors hover:bg-teal-400"
              title="Open new tab"
            >
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          </>
        )}
      </div>
    </header>
  );
}
