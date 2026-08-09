"use client";

import { RefObject } from "react";
import { ChatMessage } from "@/lib/api";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { MarkdownRenderer } from "@/components/MarkdownRenderer";
import { ActionEvent, AgentActionCard } from "@/components/AgentActionCard";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { SidebarProjectItem } from "@/components/ProjectsSidebar";
import PlanReviewPanel from "@/components/PlanReviewPanel";
import { Project } from "@/lib/api";
import {
  ArrowUp,
  ChevronDown,
  ChevronsLeft,
  ChevronsRight,
  ChevronsUpDown,
  Code2,
  FileImage,
  FileText,
  Loader2,
  MessageSquare,
  Plus,
  RotateCw,
  X,
} from "lucide-react";

function formatBytes(bytes: number | null | undefined): string | null {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return null;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function isImageFile(file: File): boolean {
  if (file.type.startsWith("image/")) return true;
  const ext = file.name.split(".").pop()?.toLowerCase() || "";
  return ["png", "jpg", "jpeg", "gif", "webp", "tif", "tiff", "svg"].includes(ext);
}

interface WorkspaceChatPanelProps {
  project?: Project | null;
  projectsList?: Project[];
  sidebarOpen?: boolean;
  setSidebarOpen?: (open: boolean) => void;
  showProjectMenu?: boolean;
  setShowProjectMenu?: (show: boolean) => void;
  statusTone?: (status: string | null | undefined) => string;
  chatMessages: ChatMessage[];
  actionEvents: ActionEvent[];
  applyActionEvents: ActionEvent[];
  failureActionEvent: ActionEvent | null;
  reviewChecks: { status: string; summary: string; checks: { name: string; status: string; detail: string }[] } | null;
  agentActivity: string;
  assistantPending: boolean;
  promptText: string;
  setPromptText: (text: string) => void;
  onSubmit: (e?: React.FormEvent) => void;
  chatMode: "build" | "discuss";
  setChatMode: (mode: "build" | "discuss") => void;
  modeOpen: boolean;
  setModeOpen: (open: boolean) => void;
  modeMenuRef: RefObject<HTMLDivElement | null>;
  fileInputRef: RefObject<HTMLInputElement | null>;
  onFileUpload: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onQuickAction: (prompt: string) => void;
}

export function WorkspaceChatPanel({
  project,
  projectsList,
  sidebarOpen = true,
  setSidebarOpen,
  showProjectMenu = false,
  setShowProjectMenu,
  statusTone = () => "bg-red-500/10 text-red-400 border-red-500/20",
  chatMessages,
  actionEvents,
  applyActionEvents,
  failureActionEvent,
  reviewChecks,
  agentActivity,
  assistantPending,
  promptText,
  setPromptText,
  onSubmit,
  chatMode,
  setChatMode,
  modeOpen,
  setModeOpen,
  modeMenuRef,
  fileInputRef,
  onFileUpload,
  onQuickAction,
}: WorkspaceChatPanelProps) {
  const combinedEvents = [
    ...actionEvents,
    ...applyActionEvents,
    ...(failureActionEvent ? [failureActionEvent] : []),
  ];

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden bg-background">
      {/* Pane 1 Header Bar */}
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-border bg-background px-3">
        <div className="flex items-center gap-1.5 overflow-hidden">
          <div className="relative flex flex-col justify-center">
            <button
              onClick={() => setShowProjectMenu?.(!showProjectMenu)}
              className="flex items-center gap-1 text-sm font-semibold text-foreground hover:bg-muted/80 py-0.5 px-1"
            >
              <span className="max-w-[150px] truncate">
                {project?.name || "Analyze Attached Study Files"}
              </span>
              <ChevronDown className="h-3 w-3 text-muted-foreground" />
            </button>
            <span className="text-[10px] text-muted-foreground px-1 leading-none">
              {project?.status === "failed" ? "Needs attention" : project?.status === "completed" ? "Ready" : "Processing"}
            </span>

            {showProjectMenu && (
              <div className="absolute left-0 top-full z-50 mt-1 w-64 rounded-lg border border-border bg-background p-1.5 shadow-xl">
                <div className="mb-1.5 flex items-center justify-between px-2 py-1 text-[11px] font-medium text-muted-foreground">
                  <span>Switch Project</span>
                  <Link
                    href="/"
                    className="flex items-center gap-1 text-teal-500 hover:underline"
                  >
                    <Plus className="h-3 w-3" /> New
                  </Link>
                </div>
                <div className="max-h-60 space-y-0.5 overflow-y-auto">
                  {(projectsList || []).map((p) => (
                    <div key={p.id} onClick={() => setShowProjectMenu?.(false)}>
                      <SidebarProjectItem project={p} />
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <Badge variant="outline" className={`text-[10px] font-normal px-1.5 py-0.5 capitalize ${statusTone(project?.status)}`}>
            {project?.status || "failed"}
          </Badge>

          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 text-muted-foreground hover:text-foreground"
            onClick={() => window.location.reload()}
            title="Refresh"
          >
            <RotateCw className="h-3 w-3" />
          </Button>

          {setSidebarOpen && (
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6 text-muted-foreground hover:text-foreground ml-auto"
              onClick={() => setSidebarOpen(!sidebarOpen)}
              title={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
            >
              <ChevronsLeft className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>
      {/* Messages Area */}
      <div className="flex-1 min-h-0 overflow-y-auto space-y-4 px-4 py-6 no-scrollbar">
        <div className="mx-auto max-w-3xl space-y-4">
          {/* Recent Agent Action Cards */}
          {combinedEvents.length > 0 && (
            <div className="space-y-2">
              {combinedEvents.map((evt, idx) => (
                <AgentActionCard key={evt.id || idx} event={evt} />
              ))}
            </div>
          )}

          {/* Chat Messages */}
          {chatMessages.map((msg, index) => (
            <div
              key={msg.id || index}
              className={`flex w-full ${
                msg.role === "user" ? "justify-end py-1" : "justify-start py-3"
              }`}
            >
              {msg.role === "user" ? (
                <div className="max-w-[75%] rounded-3xl bg-muted/80 px-4.5 py-3 text-base leading-relaxed text-foreground shadow-sm">
                  <MarkdownRenderer content={msg.content} />
                </div>
              ) : (
                <div className="w-full text-base leading-relaxed text-foreground">
                  <MarkdownRenderer content={msg.content} />
                </div>
              )}
            </div>
          ))}

          {/* Inline plan review when the plan is ready for approval */}
          {project &&
            project.analysis_plan &&
            (project.status === "planned" ||
              project.status === "needs_user" ||
              project.status === "needs_clarification") && (
              <div className="py-2">
                <PlanReviewPanel projectId={project.id} />
              </div>
            )}

          {/* Pending Assistant Indicator */}
          {assistantPending && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
              <Loader2 className="h-4 w-4 animate-spin text-teal-500" />
              <span>{agentActivity || "OmicsBase is processing..."}</span>
            </div>
          )}
        </div>
      </div>

      {/* Composer Input Area - Always pinned at bottom */}
      <div className="shrink-0 bg-background p-3">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            onSubmit();
          }}
          className="relative mx-auto max-w-3xl rounded-[28px] border border-border bg-[var(--composer-surface)] p-3 shadow-[0_18px_50px_rgba(15,23,42,0.08)] backdrop-blur transition-colors dark:shadow-[0_30px_80px_rgba(0,0,0,0.35)]"
        >
          <Textarea
            value={promptText}
            onChange={(e) => setPromptText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onSubmit();
              }
            }}
            placeholder="Ask OmicsBase..."
            className="min-h-[80px] w-full resize-none border-0 bg-transparent text-base leading-relaxed text-foreground placeholder:text-muted-foreground focus-visible:ring-0 focus-visible:ring-offset-0"
          />

          <div className="flex items-center justify-between border-t border-border/40 pt-2 mt-1">
            <div className="flex items-center gap-2">
              {/* File Upload Button */}
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                onChange={onFileUpload}
              />
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-8 rounded-full border border-border bg-muted/40 px-3 text-xs text-muted-foreground hover:bg-muted hover:text-foreground flex items-center gap-1.5"
                onClick={() => fileInputRef.current?.click()}
                title="Attach dataset file"
              >
                <Plus className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Add data, plans, notes</span>
              </Button>
            </div>

            <div className="flex items-center gap-2">
              {/* Mode Menu Dropdown */}
              <div className="relative" ref={modeMenuRef}>
                <button
                  type="button"
                  onClick={() => setModeOpen(!modeOpen)}
                  className="inline-flex h-8 items-center gap-1.5 rounded-full border border-border bg-muted/40 px-3 text-xs font-medium text-foreground hover:bg-muted"
                >
                  {chatMode === "build" ? (
                    <>
                      <Code2 className="h-3.5 w-3.5 text-teal-500" />
                      <span>Build</span>
                    </>
                  ) : (
                    <>
                      <MessageSquare className="h-3.5 w-3.5 text-indigo-400" />
                      <span>Discuss</span>
                    </>
                  )}
                  <ChevronDown className="h-3.5 w-3.5 opacity-70" />
                </button>

                {modeOpen && (
                  <div className="absolute bottom-full right-0 z-50 mb-2 w-48 rounded-xl border border-border bg-background p-1.5 shadow-xl">
                    <button
                      type="button"
                      onClick={() => {
                        setChatMode("build");
                        setModeOpen(false);
                      }}
                      className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-xs hover:bg-muted"
                    >
                      <Code2 className="h-3.5 w-3.5 text-teal-500" />
                      <div className="font-medium">Build</div>
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setChatMode("discuss");
                        setModeOpen(false);
                      }}
                      className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-xs hover:bg-muted"
                    >
                      <MessageSquare className="h-3.5 w-3.5 text-indigo-400" />
                      <div className="font-medium">Discuss</div>
                    </button>
                  </div>
                )}
              </div>

              {/* Submit Button */}
              <Button
                type="submit"
                disabled={!promptText.trim() || assistantPending}
                size="icon"
                className="h-8 w-8 rounded-full bg-teal-600 text-white hover:bg-teal-500 disabled:opacity-40"
              >
                <ArrowUp className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
