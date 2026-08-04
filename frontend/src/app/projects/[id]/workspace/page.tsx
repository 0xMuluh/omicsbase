"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, AgentStreamEvent, ChatMessage, FilePreview, FileTreeNode, Job, PendingQuestion, ProjectMessage } from "@/lib/api";
import PlanReviewPanel from "@/components/PlanReviewPanel";
import { WorkspaceComposer } from "@/components/WorkspaceComposer";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { ThemeToggle } from "@/components/ThemeToggle";
import {
  ActionEvent,
  AgentActionCard,
  applyResultsToActionEvents,
  jobFailureToActionEvent,
} from "@/components/AgentActionCard";
import Editor from "@monaco-editor/react";
import { InlineAiWidget } from "@/components/InlineAiWidget";
import { MarkdownRenderer } from "@/components/MarkdownRenderer";
import { ProjectsSidebarContent } from "@/components/ProjectsSidebar";
import { ThreadOverviewRail } from "@/components/ThreadOverviewRail";
import { useTheme } from "next-themes";
import {
  AlertCircle,
  Braces,
  Check,
  ChevronsDownUp,
  ChevronsLeft,
  ChevronsRight,
  ChevronsUpDown,
  ChevronDown,
  ChevronRight,
  Code2,
  Download,
  ExternalLink,
  File,
  FileCode,
  FileText,
  Globe,
  HelpCircle,
  History,
  Image,
  Loader2,
  Lock,
  MessageSquare,
  Play,
  RefreshCw,
  Save,
  Search,
  Table2,
  Unlock,
  X,
  ArrowRight,
} from "lucide-react";

const stateCopy: Record<string, string> = {
  idle: "Ready for instruction",
  planning: "Designing the analysis plan",
  needs_user: "Waiting for plan approval",
  generating: "Writing analysis source",
  rendering: "Rendering report preview",
  repairing: "Repairing generated code",
  editing: "Applying requested edits",
  reviewing: "Reviewing rendered output",
  completed: "Report ready",
  failed: "Needs attention",
};

function statusTone(status: string | null | undefined) {
  if (status === "completed" || status === "approved" || status === "passed") {
    return "bg-emerald-500/15 text-emerald-700 border-emerald-500/20 dark:text-emerald-300";
  }
  if (status === "failed") return "bg-red-500/15 text-red-700 border-red-500/20 dark:text-red-300";
  if (status === "warning") return "bg-amber-500/15 text-amber-700 border-amber-500/20 dark:text-amber-300";
  return "bg-teal-500/15 text-teal-700 border-teal-500/20 dark:text-teal-300";
}

function getLanguage(path: string | null) {
  if (!path) return "plaintext";
  const ext = path.split(".").pop()?.toLowerCase();
  switch (ext) {
    case "r":
      return "r";
    case "qmd":
    case "md":
      return "markdown";
    case "yml":
    case "yaml":
      return "yaml";
    case "json":
      return "json";
    case "html":
      return "html";
    case "css":
      return "css";
    case "js":
    case "ts":
    case "tsx":
      return "typescript";
    case "csv":
    case "tsv":
      return "plaintext";
    default:
      return "plaintext";
  }
}

function isImagePath(path: string | null | undefined) {
  if (!path) return false;
  return /\.(png|jpe?g|gif|svg|webp)$/i.test(path);
}

function isTabularPath(path: string | null | undefined) {
  if (!path) return false;
  return /\.(csv|tsv|xlsx|xls|sav)$/i.test(path);
}

function isEditableTabularPath(path: string | null | undefined) {
  if (!path) return false;
  return /\.(csv|tsv)$/i.test(path);
}

function tabLabel(path: string) {
  return path.split("/").pop() || path;
}

function flattenFileTree(nodes: FileTreeNode[]): string[] {
  const paths: string[] = [];
  for (const node of nodes) {
    if (node.type === "file") {
      paths.push(node.path);
    }
    if (node.children?.length) {
      paths.push(...flattenFileTree(node.children));
    }
  }
  return paths;
}

function projectMessageToChatMessage(message: ProjectMessage): ChatMessage {
  return {
    id: message.id,
    role: message.role,
    kind: message.kind,
    content: message.content,
    time: message.created_at,
    metadata: message.metadata,
    cell_id: message.cell_id,
    cell_type: message.cell_type,
    cell_revision: message.cell_revision,
    execution_id: message.execution_id,
  };
}

function collectDirPaths(nodes: FileTreeNode[]): string[] {
  const paths: string[] = [];
  for (const node of nodes) {
    if (node.type === "directory") {
      paths.push(node.path);
      if (node.children?.length) paths.push(...collectDirPaths(node.children));
    }
  }
  return paths;
}

function filterFileTree(nodes: FileTreeNode[], query: string): FileTreeNode[] {
  const q = query.trim().toLowerCase();
  if (!q) return nodes;
  const filtered: FileTreeNode[] = [];
  for (const node of nodes) {
    if (node.type === "directory") {
      const children = filterFileTree(node.children || [], q);
      if (children.length > 0 || node.name.toLowerCase().includes(q)) {
        filtered.push({ ...node, children });
      }
      continue;
    }
    if (node.name.toLowerCase().includes(q) || node.path.toLowerCase().includes(q)) {
      filtered.push(node);
    }
  }
  return filtered;
}

function FileTypeIcon({ name, isDir }: { name: string; isDir: boolean }) {
  if (isDir) return null;
  const lower = name.toLowerCase();
  if (lower.endsWith(".qmd") || lower.endsWith(".md")) {
    return <FileText className="h-3.5 w-3.5 shrink-0 text-cyan-600 dark:text-cyan-400" />;
  }
  if (lower.endsWith(".r")) {
    return <FileCode className="h-3.5 w-3.5 shrink-0 text-blue-600 dark:text-blue-400" />;
  }
  if (lower.endsWith(".yml") || lower.endsWith(".yaml")) {
    return <FileCode className="h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" />;
  }
  if (lower.endsWith(".json")) {
    return <Braces className="h-3.5 w-3.5 shrink-0 text-yellow-600 dark:text-yellow-400" />;
  }
  if (lower.endsWith(".html") || lower.endsWith(".htm")) {
    return <Globe className="h-3.5 w-3.5 shrink-0 text-orange-600 dark:text-orange-400" />;
  }
  if (lower.endsWith(".css") || lower.endsWith(".scss")) {
    return <FileCode className="h-3.5 w-3.5 shrink-0 text-pink-600 dark:text-pink-400" />;
  }
  if (/\.(png|jpe?g|gif|svg|webp)$/.test(lower)) {
    return <Image className="h-3.5 w-3.5 shrink-0 text-violet-600 dark:text-violet-400" />;
  }
  if (/\.(csv|tsv)$/.test(lower)) {
    return <Table2 className="h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />;
  }
  if (/\.(xlsx|xls|sav)$/.test(lower)) {
    return <Table2 className="h-3.5 w-3.5 shrink-0 text-teal-600 dark:text-teal-400" />;
  }
  if (lower.endsWith(".rds") || lower.endsWith(".rda")) {
    return <File className="h-3.5 w-3.5 shrink-0 text-indigo-600 dark:text-indigo-400" />;
  }
  return <File className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />;
}

function TabularPreview({ preview }: { preview: FilePreview }) {
  const columns = preview.columns || [];
  const rows = preview.preview_rows || [];
  const dims = preview.dimensions;
  const formatLabel =
    preview.format === "spss"
      ? "SPSS"
      : preview.format === "excel"
        ? "Excel"
        : preview.format?.toUpperCase() || "Table";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-3 py-2 text-[11px] text-muted-foreground">
        <span className="font-medium text-foreground">{formatLabel}</span>
        {dims?.rows != null && dims?.columns != null ? (
          <span>
            {dims.rows.toLocaleString()} rows × {dims.columns.toLocaleString()} columns
          </span>
        ) : null}
        {preview.selected_sheet ? <span>Sheet: {preview.selected_sheet}</span> : null}
        {preview.preview_truncated ? <span>Showing first {rows.length.toLocaleString()} rows</span> : null}
        {preview.editable === false ? <span>Read-only preview</span> : null}
      </div>
      {preview.error && !rows.length ? (
        <div className="flex h-full items-center justify-center p-6 text-center text-xs text-muted-foreground">
          <div className="max-w-md space-y-2">
            <p>{preview.note || "Could not preview this file."}</p>
            <p className="font-mono text-[10px] opacity-70">{preview.error}</p>
          </div>
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="min-w-full border-collapse text-left text-[12px]">
            <thead className="sticky top-0 z-10 bg-muted/95 backdrop-blur">
              <tr>
                <th className="border-b border-border px-2 py-1.5 font-mono text-[10px] font-normal text-muted-foreground">#</th>
                {columns.map((column) => (
                  <th
                    key={column}
                    className="border-b border-border px-2 py-1.5 font-medium text-foreground"
                    title={preview.column_types?.[column] || column}
                  >
                    <span className="block max-w-[14rem] truncate">{column}</span>
                    {preview.column_types?.[column] ? (
                      <span className="mt-0.5 block text-[10px] font-normal text-muted-foreground">
                        {preview.column_types[column]}
                      </span>
                    ) : null}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="odd:bg-background even:bg-muted/30">
                  <td className="border-b border-border/60 px-2 py-1 font-mono text-[10px] text-muted-foreground">
                    {rowIndex + 1}
                  </td>
                  {columns.map((column, colIndex) => (
                    <td key={`${rowIndex}-${column}`} className="max-w-[16rem] truncate border-b border-border/60 px-2 py-1 text-foreground">
                      {row[colIndex] ?? ""}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {preview.note ? (
            <p className="border-t border-border px-3 py-2 text-[11px] text-muted-foreground">{preview.note}</p>
          ) : null}
        </div>
      )}
    </div>
  );
}

function TreeNode({
  node,
  selectedPath,
  expandedPaths,
  onToggle,
  onSelect,
}: {
  node: FileTreeNode;
  selectedPath: string | null;
  expandedPaths: Set<string>;
  onToggle: (path: string) => void;
  onSelect: (path: string) => void;
}) {
  const isDir = node.type === "directory";
  const isSelected = selectedPath === node.path;
  const open = isDir && expandedPaths.has(node.path);

  return (
    <div>
      <div
        onClick={() => (isDir ? onToggle(node.path) : onSelect(node.path))}
        className={`flex cursor-pointer items-center gap-1.5 rounded-md px-2 py-1.5 text-[13px] transition-colors ${
          isSelected
            ? "bg-teal-500/15 text-teal-800 dark:bg-teal-500/20 dark:text-teal-200"
            : "text-muted-foreground hover:bg-muted hover:text-foreground"
        }`}
      >
        {isDir ? (
          open ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          )
        ) : (
          <span className="w-3.5 shrink-0" />
        )}
        <FileTypeIcon name={node.name} isDir={isDir} />
        <span className="truncate">{node.name}</span>
      </div>
      {isDir && open && node.children ? (
        <div className="ml-2 mt-0.5 space-y-0.5 border-l border-border/40 pl-2.5">
          {node.children.map((child) => (
            <TreeNode
              key={child.path}
              node={child}
              selectedPath={selectedPath}
              expandedPaths={expandedPaths}
              onToggle={onToggle}
              onSelect={onSelect}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export default function WorkspacePage() {
  const params = useParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { resolvedTheme } = useTheme();
  const projectId = params.id as string;

  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [assistantPending, setAssistantPending] = useState(false);
  const [pendingQuestion, setPendingQuestion] = useState<PendingQuestion | null>(null);
  const [agentActivity, setAgentActivity] = useState("Understanding the workspace...");
  const [chatMode, setChatMode] = useState<"build" | "discuss">("build");
  const workspaceChatScrollRef = useRef<HTMLDivElement>(null);
  const [actionEvents, setActionEvents] = useState<ActionEvent[]>([]);
  const [quickActions, setQuickActions] = useState<{ type: string; label: string; prompt: string }[]>([]);
  const [openTabs, setOpenTabs] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [dataViewMode, setDataViewMode] = useState<"table" | "source">("table");
  const [iframeKey, setIframeKey] = useState(0);
  const [workspaceMode, setWorkspaceMode] = useState<"preview" | "code">("preview");
  const [viewMode, setViewMode] = useState<"chat" | "workspace">("chat");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState<number | null>(null);
  const [isResizingSidebar, setIsResizingSidebar] = useState(false);
  const [showProjectMenu, setShowProjectMenu] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [fileSearch, setFileSearch] = useState("");
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(new Set());
  const treeExpandedInitRef = useRef(false);
  const completedJobSignatureRef = useRef("");

  const { data: project } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
  });

  useQuery({
    queryKey: ["projects"],
    queryFn: api.listProjects,
  });

  const { data: projectMessages } = useQuery({
    queryKey: ["projectMessages", projectId],
    queryFn: () => api.listMessages(projectId),
  });

  const { data: jobs } = useQuery({
    queryKey: ["jobs", projectId],
    queryFn: () => api.listJobs(projectId),
  });

  const { data: fileTree, isLoading: treeLoading } = useQuery({
    queryKey: ["fileTree", projectId],
    queryFn: () => api.getFileTree(projectId),
  });

  const { data: projectFiles } = useQuery({
    queryKey: ["projectFiles", projectId],
    queryFn: () => api.listFiles(projectId),
    enabled: project?.status === "created",
  });
  const hasUploadedFiles = Boolean(projectFiles && projectFiles.length > 0);

  const { data: locksData } = useQuery({
    queryKey: ["locks", projectId],
    queryFn: () => api.getLocks(projectId),
    enabled: Boolean(project?.project_dir),
  });
  const lockedPaths = locksData?.paths || [];
  const hasProjectFiles = Boolean(project?.project_dir || (fileTree && fileTree.length > 0));

  const showTableView = Boolean(activeTab && isTabularPath(activeTab) && dataViewMode === "table");
  const needsTextContent = Boolean(
    activeTab
    && !isImagePath(activeTab)
    && (!isTabularPath(activeTab) || (isEditableTabularPath(activeTab) && dataViewMode === "source"))
  );

  const { data: fileContent, isLoading: contentLoading } = useQuery({
    queryKey: ["fileContent", projectId, activeTab],
    queryFn: () => (activeTab ? api.getFileContent(projectId, activeTab) : null),
    enabled: needsTextContent,
  });

  const { data: filePreview, isLoading: previewLoading } = useQuery({
    queryKey: ["filePreview", projectId, activeTab],
    queryFn: () => (activeTab ? api.getFilePreview(projectId, activeTab) : null),
    enabled: Boolean(activeTab) && isTabularPath(activeTab),
  });

  useEffect(() => {
    setDataViewMode("table");
  }, [activeTab]);

  useEffect(() => {
    return api.subscribeProjectEvents(projectId, (event) => {
      queryClient.setQueryData(["project", projectId], (current: typeof project) => (
        current
          ? {
              ...current,
              status: event.status,
              agent_state: event.agent_state,
              agent_memory: {
                ...(current.agent_memory || {}),
                summary: event.agent_summary || current.agent_memory?.summary,
                pending_guidance: event.pending_guidance || current.agent_memory?.pending_guidance,
              },
            }
          : current
      ));
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      const currentJobs = queryClient.getQueryData<Job[]>(["jobs", projectId]);
      if (event.jobs.some((eventJob) => !currentJobs?.some((job) => job.id === eventJob.id))) {
        void queryClient.invalidateQueries({ queryKey: ["jobs", projectId] });
      }
      queryClient.setQueryData<Job[]>(["jobs", projectId], (current) => {
        if (!current) return current;
        const updates = new Map(event.jobs.map((job) => [job.id, job]));
        return current.map((job) => {
          const update = updates.get(job.id);
          return update
            ? {
                ...job,
                status: update.status,
                progress: update.progress,
                error: update.error,
                updated_at: update.updated_at || job.updated_at,
              }
            : job;
        });
      });
      if (event.latest_message_id) {
        void queryClient.invalidateQueries({ queryKey: ["projectMessages", projectId] });
      }

      const liveJob = event.jobs.find((job) => job.status === "running" || job.status === "pending");
      const latestStep = [...(liveJob?.progress || [])].reverse().find((step) => step.detail || step.step);
      if (latestStep) {
        setAgentActivity(latestStep.detail || `${latestStep.step} ${latestStep.status}`);
      } else if (event.agent_summary) {
        setAgentActivity(event.agent_summary);
      }

      const completedSignature = event.jobs
        .filter((job) => job.status === "completed" || job.status === "failed")
        .map((job) => `${job.id}:${job.status}:${job.updated_at}`)
        .join("|");
      if (
        completedJobSignatureRef.current
        && completedJobSignatureRef.current !== completedSignature
      ) {
        void queryClient.invalidateQueries({ queryKey: ["fileTree", projectId] });
        void queryClient.invalidateQueries({ queryKey: ["fileContent", projectId] });
        void queryClient.invalidateQueries({ queryKey: ["filePreview", projectId] });
        setIframeKey((value) => value + 1);
      }
      completedJobSignatureRef.current = completedSignature;
    });
  }, [projectId, queryClient]);

  const isLive = ["planning", "generating", "generated", "rendering"].includes(project?.status || "");
  const isFailed = project?.status === "failed";
  const hasPreview = Boolean(project?.project_dir);
  const agentState = project?.agent_state || "idle";
  const latestFailedJob = useMemo(() => jobs?.find((job) => job.status === "failed"), [jobs]);
  const recentAgentActions = useMemo(() => [...(project?.agent_actions || [])].reverse().slice(0, 5), [project?.agent_actions]);
  const applyActionEvents = useMemo(() => {
    const editAction = [...(project?.agent_actions || [])]
      .reverse()
      .find((action) => action.type === "edit" && Array.isArray(action.details?.apply_results));
    if (!editAction || !editAction.details) return [] as ActionEvent[];
    return applyResultsToActionEvents(editAction.details.apply_results, String(editAction.time || "edit"));
  }, [project?.agent_actions]);
  const failureActionEvent = useMemo(
    () => (latestFailedJob ? jobFailureToActionEvent(latestFailedJob) : null),
    [latestFailedJob],
  );
  const displayChatMessages = useMemo(() => {
    const durable = (projectMessages || [])
      .filter((message) => message.role === "user" || message.role === "assistant")
      .map(projectMessageToChatMessage);
    const durableIds = new Set(durable.map((message) => message.id).filter(Boolean));
    return [
      ...durable,
      ...chatMessages.filter((message) => !message.id || !durableIds.has(message.id)),
    ];
  }, [chatMessages, projectMessages]);
  const previewProgressSignature = useMemo(
    () =>
      (jobs || [])
        .filter((job) => job.job_type === "render" || job.job_type === "edit")
        .flatMap((job) => job.progress || [])
        .map((entry) => `${entry.step}:${entry.status}:${entry.time || ""}`)
        .join("|"),
    [jobs]
  );
  const reviewChecks = useMemo(() => {
    const reviewAction = [...(project?.agent_actions || [])].reverse().find((action) => action.type === "review");
    if (!reviewAction) return null;
    return {
      status: reviewAction.status,
      summary: reviewAction.summary,
      checks: (reviewAction.details?.checks || []) as { name: string; status: string; detail: string }[],
    };
  }, [project?.agent_actions]);

  useEffect(() => {
    if (project?.project_dir) {
      setIframeKey((value) => value + 1);
    }
  }, [project?.project_dir, project?.status, previewProgressSignature]);

  useEffect(() => {
    if (project?.project_dir || ["generated", "completed", "rendering"].includes(project?.status || "")) {
      setViewMode("workspace");
    } else if (project?.status === "created") {
      setViewMode("chat");
    }
  }, [project?.project_dir, project?.status]);

  useEffect(() => {
    if (project?.status === "planned" || (project?.agent_state === "needs_user" && !project.project_dir)) {
      router.replace(`/projects/${projectId}/plan`);
    }
  }, [project?.agent_state, project?.project_dir, project?.status, projectId, router]);

  const activeDraft = activeTab ? drafts[activeTab] : undefined;
  const editorValue = activeDraft ?? fileContent?.content ?? "";
  const isDirty = Boolean(
    activeTab
    && activeDraft !== undefined
    && fileContent?.path === activeTab
    && activeDraft !== fileContent.content
  );

  const monacoEditorRef = useRef<any>(null);
  const inlineDecorationsRef = useRef<string[]>([]);

  const [inlineWidget, setInlineWidget] = useState<{
    show: boolean;
    top: number;
    left: number;
    selectionText?: string;
    range?: any;
    originalCode?: string;
    isGenerating: boolean;
    hasGenerated: boolean;
    diffStats?: { added: number; removed: number };
  }>({ show: false, top: 20, left: 40, isGenerating: false, hasGenerated: false });

  const clearInlineDiffDecorations = () => {
    const editor = monacoEditorRef.current;
    if (editor && inlineDecorationsRef.current.length) {
      inlineDecorationsRef.current = editor.deltaDecorations(inlineDecorationsRef.current, []);
    }
  };

  const triggerInlineAi = () => {
    const editor = monacoEditorRef.current;
    if (!editor) return;
    const pos = editor.getScrolledVisiblePosition(editor.getPosition());
    const selection = editor.getModel()?.getValueInRange(editor.getSelection());
    const fullContent = editor.getValue();

    clearInlineDiffDecorations();

    setInlineWidget({
      show: true,
      top: (pos?.top ?? 40) + 30,
      left: Math.min((pos?.left ?? 40) + 40, 450),
      selectionText: selection,
      range: editor.getSelection(),
      originalCode: fullContent,
      isGenerating: false,
      hasGenerated: false,
    });
  };

  const handleInlineGenerate = async (prompt: string) => {
    const editor = monacoEditorRef.current;
    if (!editor || !activeTab) return;
    const model = editor.getModel();
    const selection = editor.getSelection();
    const selectedText = model.getValueInRange(selection);
    const fullContent = editor.getValue();

    setInlineWidget((prev) => ({ ...prev, isGenerating: true }));

    // Rich domain context
    const projectCtx = project
      ? `Project: ${project.name}\nQuestion: ${project.question || ""}\nDataset: ${project.agent_memory?.summary || ""}`
      : undefined;
    const errorCtx = latestFailedJob ? `Error detail: ${latestFailedJob.error || ""}` : undefined;

    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const response = await fetch(`${baseUrl}/api/inline-edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: activeTab,
          prompt: prompt,
          selection: selectedText || null,
          content: fullContent,
          project_context: projectCtx,
          error_context: errorCtx,
        }),
      });

      if (!response.ok || !response.body) {
        throw new Error("Failed to start inline edit stream");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let streamedTokens = "";
      const startLine = selection ? selection.startLineNumber : 1;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split("\n");

        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const data = JSON.parse(line);
            if (data.type === "token" && data.token) {
              streamedTokens += data.token;
              if (selectedText) {
                editor.executeEdits("inline-ai", [{
                  range: selection,
                  text: streamedTokens,
                  forceMoveMarkers: true,
                }]);
              } else {
                editor.executeEdits("inline-ai", [{
                  range: model.getFullModelRange(),
                  text: streamedTokens,
                  forceMoveMarkers: true,
                }]);
              }
            }
          } catch {
            // Ignore parse errors
          }
        }
      }

      // Compute diff stats
      const originalLines = (selectedText || fullContent).split("\n").length;
      const streamedLines = streamedTokens.split("\n").length;
      const added = Math.max(0, streamedLines - originalLines);
      const removed = Math.max(0, originalLines - streamedLines);

      // Apply Monaco green background deltaDecorations
      const monacoWindow = (window as any).monaco;
      if (monacoWindow && editor) {
        const endLine = startLine + streamedLines - 1;
        inlineDecorationsRef.current = editor.deltaDecorations(
          inlineDecorationsRef.current,
          [
            {
              range: new monacoWindow.Range(startLine, 1, Math.max(startLine, endLine), 1),
              options: {
                isWholeLine: true,
                className: "bg-emerald-500/15 border-l-2 border-emerald-400",
              },
            },
          ]
        );
      }

      setInlineWidget((prev) => ({
        ...prev,
        isGenerating: false,
        hasGenerated: true,
        diffStats: { added, removed },
      }));
    } catch (err) {
      console.error("Inline AI edit failed:", err);
      setInlineWidget((prev) => ({ ...prev, isGenerating: false }));
    }
  };

  const handleInlineAccept = () => {
    clearInlineDiffDecorations();
    if (monacoEditorRef.current) {
      updateActiveDraft(monacoEditorRef.current.getValue() || "");
    }
    setInlineWidget({ show: false, top: 20, left: 40, isGenerating: false, hasGenerated: false });
  };

  const handleInlineReject = () => {
    clearInlineDiffDecorations();
    if (inlineWidget.originalCode !== undefined && monacoEditorRef.current) {
      monacoEditorRef.current.setValue(inlineWidget.originalCode);
    }
    setInlineWidget({ show: false, top: 20, left: 40, isGenerating: false, hasGenerated: false });
  };
  const dirtyTabs = useMemo(() => new Set(Object.keys(drafts)), [drafts]);
  const previewReportPath = useMemo(() => {
    const allPaths = flattenFileTree(fileTree || []);
    const renderedPages = allPaths.filter((path) => path.startsWith("output/") && path.endsWith(".html") && !path.includes("/site_libs/"));
    const firstContentPage = renderedPages.find((path) => path !== "output/index.html");
    if (firstContentPage && isFailed) {
      return firstContentPage.replace(/^output\//, "");
    }
    return "index.html";
  }, [fileTree, isFailed]);
  const reportUrl = api.getReportUrl(projectId, previewReportPath);
  const downloadUrl = api.getDownloadUrl(projectId);

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!activeTab || activeDraft === undefined) return Promise.reject("No file selected");
      return api.saveFileContent(projectId, activeTab, activeDraft);
    },
    onSuccess: () => {
      if (activeTab) {
        setDrafts((prev) => {
          const next = { ...prev };
          delete next[activeTab];
          return next;
        });
      }
      queryClient.invalidateQueries({ queryKey: ["fileContent", projectId, activeTab] });
      queryClient.invalidateQueries({ queryKey: ["filePreview", projectId, activeTab] });
      queryClient.invalidateQueries({ queryKey: ["fileTree", projectId] });
    },
  });

  const retryMutation = useMutation({
    mutationFn: () => {
      if (project?.project_dir) return api.startRendering(projectId);
      if (project?.analysis_plan) return api.startGeneration(projectId);
      return api.startPlanning(projectId);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      queryClient.invalidateQueries({ queryKey: ["jobs", projectId] });
    },
  });

  const runChunkMutation = useMutation({
    mutationFn: () => {
      const codeToRun = activeDraft ?? fileContent?.content ?? "";
      return api.runCodeChunk(projectId, codeToRun, activeTab);
    },
  });

  const locksMutation = useMutation({
    mutationFn: (paths: string[]) => api.updateLocks(projectId, paths),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["locks", projectId] });
    },
  });

  const toggleActiveLock = () => {
    if (!activeTab) return;
    const next = lockedPaths.includes(activeTab)
      ? lockedPaths.filter((path) => path !== activeTab)
      : [...lockedPaths, activeTab];
    locksMutation.mutate(next);
  };

  const saveMutationRef = useRef<() => void>(() => {});
  saveMutationRef.current = () => {
    if (isDirty && !saveMutation.isPending) saveMutation.mutate();
  };

  const handleSendPrompt = async (
    event?: React.FormEvent,
    override?: { message?: string; mode?: "build" | "discuss" },
  ) => {
    event?.preventDefault();
    const message = (override?.message ?? "").trim();
    const mode = override?.mode ?? chatMode;
    if (!message || assistantPending) return;

    setPendingQuestion(null);
    const optimisticId = `local-${Date.now()}`;
    const userMessage: ChatMessage = {
      id: optimisticId,
      role: "user",
      content: message,
      time: new Date().toISOString(),
    };
    setChatMessages((prev) => [...prev, userMessage]);
    setAssistantPending(true);
    setQuickActions([]);
    setActionEvents([]);
    setAgentActivity(mode === "discuss" ? "Discussing the analysis..." : "Understanding the workspace...");
    try {
      await api.streamAgentMessage(
        projectId,
        {
          message,
          selected_file: activeTab,
          selected_content: isDirty ? activeDraft ?? null : null,
          selected_content_dirty: isDirty,
          preview_path: previewReportPath,
          chat_mode: mode,
        },
        (streamEvent: AgentStreamEvent) => {
          if (streamEvent.type === "question" && streamEvent.question) {
            setPendingQuestion(streamEvent.question);
          }
          if (streamEvent.type === "final" && streamEvent.awaiting_answer) {
            setPendingQuestion(streamEvent.awaiting_answer);
          }
          if (streamEvent.type === "title_update" && typeof streamEvent.name === "string") {
            const updatedTitle = streamEvent.name;
            queryClient.setQueryData(["project", projectId], (old: any) =>
              old ? { ...old, name: updatedTitle } : old
            );
            queryClient.setQueryData(["projects"], (old: any) =>
              Array.isArray(old)
                ? old.map((p: any) => (p.id === projectId ? { ...p, name: updatedTitle } : p))
                : old
            );
          }
          if (streamEvent.type === "status" && typeof streamEvent.message === "string") {
            setAgentActivity(streamEvent.message);
          }
          if (streamEvent.type === "action_event" && streamEvent.event) {
            const next = streamEvent.event as ActionEvent;
            setActionEvents((prev) => {
              const without = prev.filter((item) => item.id !== next.id && !item.id.endsWith("-start"));
              return [...without, next];
            });
          }
          if (streamEvent.type === "tool_started") {
            setAgentActivity(streamEvent.reason || `Inspecting ${streamEvent.tool || "the workspace"}...`);
          }
          if (streamEvent.type === "tool_completed") {
            setAgentActivity(streamEvent.summary || "Workspace inspection completed");
          }
          if (streamEvent.type === "token" && typeof streamEvent.token === "string") {
            setChatMessages((prev) => {
              const existing = prev.find((item) => item.id === "streaming-assistant");
              if (existing) {
                return prev.map((item) =>
                  item.id === "streaming-assistant"
                    ? { ...item, content: `${item.content}${streamEvent.token}` }
                    : item
                );
              }
              return [
                ...prev,
                {
                  id: "streaming-assistant",
                  role: "assistant",
                  content: streamEvent.token as string,
                  time: new Date().toISOString(),
                },
              ];
            });
          }
          if (
            streamEvent.type === "message"
            && typeof streamEvent.message === "object"
            && streamEvent.message
          ) {
            const persisted = projectMessageToChatMessage(streamEvent.message);
            setChatMessages((prev) => [
              ...prev.filter((item) => item.id !== optimisticId),
              persisted,
            ]);
          }
          if (
            (streamEvent.type === "final" || streamEvent.type === "action_queued")
            && typeof streamEvent.message === "string"
          ) {
            if (streamEvent.quick_actions?.length) {
              setQuickActions(streamEvent.quick_actions);
            }
            setChatMessages((prev) => [
              ...prev.filter((item) => item.id !== "streaming-assistant"),
              {
                id: streamEvent.message_id,
                role: "assistant",
                content: streamEvent.message as string,
                time: new Date().toISOString(),
                metadata: streamEvent.job_id ? { job_id: streamEvent.job_id } : null,
              },
            ]);
          }
        },
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["projectMessages", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["project", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["jobs", projectId] }),
      ]);
      setChatMessages([]);
    } catch (error) {
      setChatMessages((prev) => [
        ...prev,
        { role: "assistant", content: error instanceof Error ? error.message : "Could not reach the assistant.", time: new Date().toISOString() },
      ]);
    } finally {
      setAssistantPending(false);
      setAgentActivity("Understanding the workspace...");
    }
  };

  const initialQuestionSentRef = useRef(false);
  useEffect(() => {
    if (
      project?.question
      && projectMessages !== undefined
      && projectMessages.length === 0
      && !initialQuestionSentRef.current
      && !assistantPending
    ) {
      initialQuestionSentRef.current = true;
      void handleSendPrompt(undefined, { message: project.question, mode: "build" });
    }
  }, [project?.question, projectMessages, assistantPending]);

  const askAgent = (prompt: string, mode: "build" | "discuss" = "build") => {
    setChatMode(mode);
    void handleSendPrompt(undefined, { message: prompt, mode });
  };

  const answerQuestion = (answer: string) => {
    setPendingQuestion(null);
    void handleSendPrompt(undefined, { message: answer, mode: chatMode });
  };

  const handleAddFiles = async (files: File[]) => {
    const failures: string[] = [];
    for (const file of files) {
      try {
        await api.uploadFile(projectId, file, "auto");
      } catch {
        failures.push(file.name);
      }
    }
    void handleSendPrompt(undefined, {
      message: `[Attached: ${files.map((file) => file.name).join(", ")}]`
        + (failures.length ? ` (failed to upload: ${failures.join(", ")})` : ""),
      mode: chatMode,
    });
    void queryClient.invalidateQueries({ queryKey: ["projects"] });
  };

  useEffect(() => {
    const pending = project?.agent_memory?.pending_question as PendingQuestion | undefined;
    if (pending && !assistantPending) {
      setPendingQuestion(pending);
    }
  }, [project?.agent_memory?.pending_question, assistantPending]);

  const [buildError, setBuildError] = useState<string | null>(null);
  const [buildPending, setBuildPending] = useState(false);
  const buildNow = async () => {
    setBuildError(null);
    setBuildPending(true);
    try {
      await api.startPlanning(projectId);
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    } catch (error) {
      setBuildError(error instanceof Error ? error.message : "Planning could not be started.");
    } finally {
      setBuildPending(false);
    }
  };

  const handleFileSelect = (path: string) => {
    setOpenTabs((prev) => (prev.includes(path) ? prev : [...prev, path]));
    setActiveTab(path);
  };

  const closeTab = (path: string) => {
    setOpenTabs((prev) => {
      const index = prev.indexOf(path);
      const next = prev.filter((item) => item !== path);
      if (activeTab === path) {
        const fallback = next[Math.min(index, Math.max(next.length - 1, 0))] ?? null;
        setActiveTab(fallback);
      }
      return next;
    });
    setDrafts((prev) => {
      if (!(path in prev)) return prev;
      const next = { ...prev };
      delete next[path];
      return next;
    });
  };

  const updateActiveDraft = (value: string) => {
    if (!activeTab) return;
    setDrafts((prev) => {
      if (fileContent?.path === activeTab && value === fileContent.content) {
        if (!(activeTab in prev)) return prev;
        const next = { ...prev };
        delete next[activeTab];
        return next;
      }
      return { ...prev, [activeTab]: value };
    });
  };

  const visibleFileTree = useMemo(
    () => filterFileTree(fileTree || [], fileSearch),
    [fileTree, fileSearch]
  );

  const allDirPaths = useMemo(
    () => collectDirPaths(fileTree || []),
    [fileTree]
  );

  useEffect(() => {
    if (!fileTree?.length || treeExpandedInitRef.current) return;
    setExpandedPaths(new Set(collectDirPaths(fileTree)));
    treeExpandedInitRef.current = true;
  }, [fileTree]);

  const searching = Boolean(fileSearch.trim());
  const effectiveExpandedPaths = useMemo(() => {
    if (searching) return new Set(collectDirPaths(visibleFileTree));
    return expandedPaths;
  }, [searching, visibleFileTree, expandedPaths]);

  const toggleDir = (path: string) => {
    setExpandedPaths((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      {sidebarOpen ? (
      <aside
        className="relative flex min-h-0 shrink-0 flex-col border-r border-border bg-sidebar"
        style={{ width: sidebarWidth === null ? (viewMode === "chat" ? "320px" : "clamp(340px, 33vw, 460px)") : `${sidebarWidth}px` }}
      >
        {viewMode === "chat" ? (
          <div className="flex h-full min-h-0 flex-col">
            <div className="min-h-0 flex-1">
              <ProjectsSidebarContent notesScope="recent" onClose={() => setSidebarOpen(false)} />
            </div>
            <div className="flex shrink-0 items-center justify-between border-t border-border p-3">
              <ThemeToggle />
            </div>
          </div>
        ) : (
          <>
            <div className="border-b border-border px-4 py-4">
          <div className="flex items-start justify-between gap-3">
            <div className="relative min-w-0">
              <button
                type="button"
                onClick={() => setShowProjectMenu((value) => !value)}
                className="flex max-w-full items-center gap-1 text-left"
                aria-expanded={showProjectMenu}
              >
                <span className="truncate text-sm font-semibold text-foreground">{project?.name || "Project"}</span>
                <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              </button>
              <p className="mt-1 text-[11px] text-muted-foreground">{stateCopy[agentState] || "Working"}</p>

              {showProjectMenu ? (
                <div className="absolute left-0 top-9 z-30 w-60 overflow-hidden rounded-xl border border-border bg-popover py-1 shadow-2xl">
                  <Link href="/" className="block px-3 py-2 text-xs text-muted-foreground hover:bg-muted hover:text-foreground">
                    Dashboard
                  </Link>
                  <button
                    type="button"
                    onClick={() => {
                      setShowHistory(true);
                      setShowProjectMenu(false);
                    }}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    <History className="h-3.5 w-3.5" />
                    View change history
                  </button>
                  <a href={downloadUrl} download className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground hover:bg-muted hover:text-foreground">
                    <Download className="h-3.5 w-3.5" />
                    Download project
                  </a>
                </div>
              ) : null}
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="outline" className={`border px-2 py-1 text-[10px] ${statusTone(project?.status)}`}>
                {project?.status || "loading"}
              </Badge>
              <button
                type="button"
                onClick={() => setShowHistory((value) => !value)}
                className={`rounded-md p-1 transition-colors hover:bg-muted hover:text-foreground ${
                  showHistory ? "text-teal-300" : "text-muted-foreground"
                }`}
                title={showHistory ? "Return to chat" : "View history"}
                aria-pressed={showHistory}
              >
                <History className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={() => setSidebarOpen(false)}
                className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                title="Collapse sidebar"
              >
                <ChevronsLeft className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>

        {showHistory ? (
          <>
            <div className="shrink-0 border-b border-border px-3 py-2">
              <div className="grid grid-cols-2 rounded-lg bg-muted p-0.5">
                <button
                  type="button"
                  className="rounded-md bg-background px-3 py-1.5 text-xs text-white"
                >
                  History
                </button>
                <button
                  type="button"
                  onClick={() => setShowHistory(false)}
                  className="rounded-md px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground"
                >
                  Chat
                </button>
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3 no-scrollbar">
              <div className="space-y-0.5">
                {[...(project?.agent_actions || [])].reverse().map((action, index) => (
                  <div
                    key={`${action.time}-${index}`}
                    className="rounded-lg px-2 py-2 transition-colors hover:bg-muted"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <p className="text-xs leading-4 text-foreground/90">{action.summary}</p>
                      <time className="shrink-0 text-[9px] text-muted-foreground/70">
                        {new Date(action.time).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                      </time>
                    </div>
                    <p className="mt-1 text-[9px] uppercase tracking-wide text-muted-foreground/70">{action.type}</p>
                  </div>
                ))}
              </div>
            </div>
          </>
        ) : (
          <>
        <ScrollArea className="min-h-0 flex-1 px-4 py-4">
          <div className="space-y-4">
            <div className="rounded-2xl bg-muted px-3 py-3 text-xs leading-5 text-muted-foreground">
              <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                Agent
              </div>
              <p className="mt-2">{project?.agent_memory?.summary || "Ask for a change or inspect the current report."}</p>
            </div>

            {isLive ? (
              <div className="flex flex-col gap-1 rounded-2xl bg-teal-500/[0.08] px-3 py-2.5 text-xs text-teal-800 dark:text-teal-100">
                <div className="flex items-center gap-2 font-medium">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Updating the report…
                </div>
                {agentActivity ? (
                  <p className="pl-6 text-[11px] text-teal-800 dark:text-teal-700/90 dark:text-teal-100/80">{agentActivity}</p>
                ) : null}
                {Array.isArray(project?.agent_memory?.pending_guidance)
                  && project.agent_memory.pending_guidance.length > 0 ? (
                  <p className="pl-6 text-[11px] text-amber-700 dark:text-amber-200/90">
                    Queued guidance: {project.agent_memory.pending_guidance.map((item: { content: string }) => item.content).join("; ")}
                  </p>
                ) : null}
              </div>
            ) : null}

            {project?.status === "needs_clarification" ? (
              <div className="flex items-center gap-3 rounded-2xl border border-amber-500/30 bg-amber-500/10 px-4 py-3">
                <HelpCircle className="h-4 w-4 shrink-0 text-amber-400" />
                <p className="flex-1 text-xs leading-5 text-amber-700 dark:text-amber-200/90">
                  The planner needs a couple of decisions before it can build the analysis.
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 gap-1.5 border-amber-500/40 text-amber-700 hover:bg-amber-500/10 dark:text-amber-200"
                  onClick={() => router.push(`/projects/${projectId}/plan`)}
                >
                  <ArrowRight className="h-3.5 w-3.5" />
                  Answer
                </Button>
              </div>
            ) : null}

            {isFailed && failureActionEvent ? (
              <AgentActionCard
                event={failureActionEvent}
                onAskAgent={(prompt) => askAgent(prompt, "build")}
                onOpenPath={handleFileSelect}
              />
            ) : null}

            <div className="space-y-2">
              {[...applyActionEvents, ...actionEvents].map((event) => (
                <AgentActionCard
                  key={event.id}
                  event={event}
                  onAskAgent={(prompt) => askAgent(prompt, "build")}
                  onOpenPath={handleFileSelect}
                />
              ))}

              {recentAgentActions.length ? recentAgentActions.slice().reverse().map((action, index) => (
                <div key={`${action.time}-${index}`} className="flex gap-2">
                  <div className={`mt-2 h-1.5 w-1.5 shrink-0 rounded-full ${action.status === "failed" ? "bg-red-400" : action.status === "completed" || action.status === "approved" || action.status === "passed" ? "bg-emerald-400" : "bg-teal-400"}`} />
                  <div className="rounded-2xl bg-muted px-3 py-2 text-xs leading-5 text-muted-foreground">
                    {action.summary}
                  </div>
                </div>
              )) : null}

              {viewMode === "workspace" ? displayChatMessages.map((message, index) => (
                <div
                  key={message.id || `${message.time}-${index}`}
                  className={`flex w-full ${message.role === "user" ? "justify-end" : "justify-start"} py-1`}
                >
                  {message.role === "user" ? (
                    <div className="max-w-[85%] rounded-3xl bg-muted/80 px-4 py-2.5 text-base leading-relaxed text-foreground shadow-sm">
                      {message.content}
                    </div>
                  ) : (
                    <div className="w-full text-base leading-relaxed text-foreground">
                      <MarkdownRenderer content={message.content} />
                    </div>
                  )}
                </div>
              )) : null}

              {quickActions.length ? (
                <div className="flex flex-wrap gap-2">
                  {quickActions.map((action) => (
                    <Button
                      key={`${action.type}-${action.label}`}
                      type="button"
                      size="sm"
                      variant="outline"
                      className="h-7 gap-1.5 rounded-full text-[11px]"
                      disabled={assistantPending}
                      onClick={() => askAgent(action.prompt, "build")}
                    >
                      <Play className="h-3 w-3" />
                      {action.label}
                    </Button>
                  ))}
                </div>
              ) : null}
            </div>

            {displayChatMessages.length === 0 && recentAgentActions.length === 0 && actionEvents.length === 0 ? (
              <div className="px-1 text-xs leading-5 text-muted-foreground">
                Ask a question or describe the change you want. Use Discuss to plan without editing.
              </div>
            ) : null}

            {assistantPending ? (
              <div className="flex items-center gap-2 rounded-2xl border border-border bg-muted/60 px-3 py-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin text-teal-300" />
                {agentActivity}
              </div>
            ) : null}

            {reviewChecks?.status === "warning" ? (
              <p className="px-1 text-[11px] text-amber-700 dark:text-amber-300">{reviewChecks.summary}</p>
            ) : null}
          </div>
        </ScrollArea>

        {viewMode === "workspace" ? (
        <div className="shrink-0 border-t border-border p-3">
          <WorkspaceComposer
            pendingQuestion={pendingQuestion}
            chatMode={chatMode}
            disabled={assistantPending}
            onSend={(message, mode) => {
              setChatMode(mode);
              void handleSendPrompt(undefined, { message, mode });
            }}
            onAnswer={answerQuestion}
            onModeChange={setChatMode}
            onAddFiles={handleAddFiles}
          />
        </div>
        ) : null}
          </>
        )}
      </>
    )}
        <div
          className={`absolute inset-y-0 right-[-3px] z-20 w-1.5 touch-none cursor-col-resize transition-colors ${
            isResizingSidebar ? "bg-teal-400/60" : "bg-transparent hover:bg-teal-400/35"
          }`}
          onPointerDown={(event) => {
            event.currentTarget.setPointerCapture(event.pointerId);
            setIsResizingSidebar(true);
          }}
          onPointerMove={(event) => {
            if (!isResizingSidebar) return;
            setSidebarWidth(Math.min(520, Math.max(300, event.clientX)));
          }}
          onPointerUp={(event) => {
            if (event.currentTarget.hasPointerCapture(event.pointerId)) {
              event.currentTarget.releasePointerCapture(event.pointerId);
            }
            setIsResizingSidebar(false);
          }}
          onPointerCancel={() => setIsResizingSidebar(false)}
        />
      </aside>
      ) : (
        null
      )}

      <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden bg-background">
        <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden">
          <div className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-border px-3">
            <div className="flex items-center gap-2">
              {!sidebarOpen ? (
                <button
                  type="button"
                  onClick={() => setSidebarOpen(true)}
                  className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  title="Open sidebar"
                >
                  <ChevronsRight className="h-4 w-4" />
                </button>
              ) : null}

              {hasProjectFiles ? (
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
                      viewMode === "workspace"
                        ? "bg-background text-foreground shadow-sm"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <Code2 className="h-3.5 w-3.5" />
                    Workspace
                  </button>
                </div>
              ) : null}

              {viewMode === "workspace" ? (
                <div className="ml-2 inline-flex shrink-0 items-center rounded-full border border-border bg-muted p-1">
                  <button
                    type="button"
                    onClick={() => setWorkspaceMode("preview")}
                    className={`inline-flex items-center gap-1.5 rounded-full transition-colors ${
                      workspaceMode === "preview"
                        ? "bg-teal-500 px-3 py-1.5 text-xs font-medium text-zinc-950"
                        : "p-1.5 text-muted-foreground hover:text-foreground"
                    }`}
                    title="Preview"
                  >
                    <Globe className="h-4 w-4" />
                    {workspaceMode === "preview" ? <span>Preview</span> : null}
                  </button>
                  <button
                    type="button"
                    onClick={() => setWorkspaceMode("code")}
                    className={`inline-flex items-center gap-1.5 rounded-full transition-colors ${
                      workspaceMode === "code"
                        ? "bg-teal-500 px-3 py-1.5 text-xs font-medium text-zinc-950"
                        : "p-1.5 text-muted-foreground hover:text-foreground"
                    }`}
                    title="Code"
                  >
                    <Code2 className="h-4 w-4" />
                    {workspaceMode === "code" ? <span>Code</span> : null}
                  </button>
                </div>
              ) : null}
            </div>

            <div className="flex items-center gap-1.5">
              <ThemeToggle />
              <Link
                href={"/projects/" + projectId + "/notes"}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-muted/40 px-2.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                title="Open interactive notes"
              >
                <FileText className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Notes</span>
              </Link>
              {isDirty ? <Badge variant="outline" className="border-amber-500/20 bg-amber-500/10 px-2 py-1 text-[10px] text-amber-700 dark:text-amber-300">unsaved code</Badge> : null}
              {viewMode === "workspace" ? (
                <>
                  <Button variant="ghost" size="sm" onClick={() => setIframeKey((value) => value + 1)} className="h-8 gap-1.5 px-2 text-muted-foreground hover:text-foreground">
                    <RefreshCw className="h-3.5 w-3.5" />
                    Reload
                  </Button>
                  <a href={downloadUrl} download className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-muted/60 px-3 text-sm text-foreground/90 transition-colors hover:bg-muted">
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
              ) : null}
            </div>
          </div>

          {["planning", "planned", "needs_clarification"].includes(project?.status || "") ? (
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
              <div className="min-h-0 flex-1 overflow-y-auto">
                <PlanReviewPanel projectId={projectId} />
              </div>
              <div className="shrink-0 border-t border-border p-3">
                <WorkspaceComposer
                  pendingQuestion={pendingQuestion}
                  chatMode={chatMode}
                  disabled={assistantPending}
                  onSend={(message, mode) => {
                    setChatMode(mode);
                    void handleSendPrompt(undefined, { message, mode });
                  }}
                  onAnswer={answerQuestion}
                  onModeChange={setChatMode}
                  onAddFiles={handleAddFiles}
                />
              </div>
            </div>
          ) : viewMode === "chat" ? (
            <div className="relative flex min-h-0 flex-1 flex-col items-center justify-between p-4 md:p-6 overflow-hidden">
              <div ref={workspaceChatScrollRef} data-thread-column className="w-full max-w-3xl flex-1 overflow-y-auto space-y-4 pr-1 no-scrollbar">
                {displayChatMessages.map((message, index) => (
                  <div
                    key={message.id || `${message.time}-${index}`}
                    className={`flex w-full ${message.role === "user" ? "justify-end" : "justify-start"} py-1`}
                  >
                    {message.role === "user" ? (
                      <div
                        data-overview-block
                        data-overview-type="user"
                        data-overview-id={`ws-msg-${message.id || index}`}
                        className="max-w-[75%] rounded-3xl bg-muted/80 px-4.5 py-3 text-base leading-relaxed text-foreground shadow-sm"
                      >
                        {message.content}
                      </div>
                    ) : (
                      <div className="w-full text-sm leading-7 text-foreground">
                        <MarkdownRenderer content={message.content} />
                      </div>
                    )}
                  </div>
                ))}

                {assistantPending ? (
                  <div className="flex justify-start py-2">
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin text-teal-500" />
                      {agentActivity}
                    </div>
                  </div>
                ) : null}
              </div>

              <ThreadOverviewRail
                containerRef={workspaceChatScrollRef}
                refreshKey={displayChatMessages.length + "-" + (assistantPending ? "busy" : "idle")}
              />

              {project?.status === "created" ? (
                <div className="w-full max-w-3xl shrink-0 pb-2">
                  {hasUploadedFiles && !assistantPending ? (
                    <div className="flex flex-col items-center gap-1.5">
                      <Button
                        type="button"
                        size="sm"
                        onClick={() => void buildNow()}
                        disabled={buildPending}
                        className="gap-2 rounded-full border border-teal-500/40 bg-teal-500/10 px-4 text-teal-800 hover:bg-teal-500/20 dark:text-teal-100"
                      >
                        {buildPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                        Build the report
                      </Button>
                      {buildError ? (
                        <p className="text-xs text-red-600 dark:text-red-300">{buildError}</p>
                      ) : null}
                    </div>
                  ) : !hasUploadedFiles && !assistantPending ? (
                    <p className="text-center text-xs leading-5 text-muted-foreground">
                      Attach study files with +, or ask the agent to import an example dataset. Planning starts when you say go.
                    </p>
                  ) : null}
                </div>
              ) : null}

              <div className="w-full max-w-3xl shrink-0 pt-4">
                <WorkspaceComposer
                  pendingQuestion={pendingQuestion}
                  chatMode={chatMode}
                  disabled={assistantPending}
                  onSend={(message, mode) => {
                    setChatMode(mode);
                    void handleSendPrompt(undefined, { message, mode });
                  }}
                  onAnswer={answerQuestion}
                  onModeChange={setChatMode}
                  onAddFiles={handleAddFiles}
                />
              </div>
            </div>
          ) : workspaceMode === "preview" ? (
            <div className="min-h-0 flex-1 overflow-hidden bg-white">
              {hasPreview ? (
                <iframe key={iframeKey} src={reportUrl} className="h-full w-full border-0 bg-white" title="Quarto Report Preview" />
              ) : (
                <div className="flex h-full flex-col items-center justify-center bg-background px-8 text-center">
                  {isFailed ? <AlertCircle className="mb-4 h-10 w-10 text-red-400" /> : <Globe className="mb-4 h-10 w-10 text-teal-300" />}
                  <h2 className="text-xl font-semibold text-foreground">
                    {isFailed
                      ? "Preview was not produced"
                      : isLive
                        ? "The report is being assembled"
                        : "No report yet"}
                  </h2>
                  <p className="mt-3 max-w-xl text-sm leading-6 text-muted-foreground">
                    {isFailed
                      ? "Use Ask agent to fix in the left sidebar. Generated files stay visible, so the build can be inspected and fixed."
                      : isLive
                        ? "As soon as OmicsBase renders the first valid page, it will appear here as the primary canvas."
                        : "Attach data, or ask the agent to import an example dataset (e.g. GlobalPatterns) and build a report."}
                  </p>
                </div>
              )}
            </div>
          ) : (
            <section className="grid min-h-0 flex-1 grid-cols-[minmax(240px,300px)_minmax(0,1fr)] overflow-hidden rounded-[28px] border border-border bg-card shadow-sm dark:bg-[#0b0d15]/95 dark:shadow-[0_20px_80px_rgba(0,0,0,0.35)]">
              <div className="flex min-h-0 flex-col border-r border-border bg-muted/30">
                <div className="shrink-0 border-b border-border px-3 py-2.5">
                  <div className="flex items-center gap-1.5">
                    <div className="relative min-w-0 flex-1">
                      <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                      <input
                        type="search"
                        value={fileSearch}
                        onChange={(event) => setFileSearch(event.target.value)}
                        placeholder="Search code"
                        className="h-8 w-full rounded-lg border border-border bg-background pl-8 pr-2.5 text-[13px] text-foreground outline-none placeholder:text-muted-foreground focus:border-teal-500/40"
                      />
                    </div>
                    <button
                      type="button"
                      onClick={() => setExpandedPaths(new Set(allDirPaths))}
                      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border text-muted-foreground transition hover:bg-muted hover:text-foreground"
                      title="Expand all"
                    >
                      <ChevronsUpDown className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => setExpandedPaths(new Set())}
                      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border text-muted-foreground transition hover:bg-muted hover:text-foreground"
                      title="Collapse all"
                    >
                      <ChevronsDownUp className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
                <ScrollArea className="min-h-0 flex-1 p-2">
                  {treeLoading ? (
                    <div className="flex justify-center p-4">
                      <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                    </div>
                  ) : !fileTree?.length ? (
                    <p className="p-3 text-xs text-muted-foreground">No files written yet.</p>
                  ) : !visibleFileTree.length ? (
                    <p className="p-3 text-xs text-muted-foreground">No files match “{fileSearch.trim()}”.</p>
                  ) : (
                    <div className="space-y-0.5">
                      {visibleFileTree.map((node) => (
                        <TreeNode
                          key={node.path}
                          node={node}
                          selectedPath={activeTab}
                          expandedPaths={effectiveExpandedPaths}
                          onToggle={toggleDir}
                          onSelect={handleFileSelect}
                        />
                      ))}
                    </div>
                  )}
                </ScrollArea>
              </div>

              <div className="flex min-h-0 flex-col overflow-hidden bg-background">
                <div className="flex shrink-0 items-center gap-2 border-b border-border bg-muted/40">
                  <div className="flex min-w-0 flex-1 items-stretch overflow-x-auto">
                    {openTabs.length ? (
                      openTabs.map((path) => {
                        const active = path === activeTab;
                        const dirty = dirtyTabs.has(path);
                        return (
                          <div
                            key={path}
                            className={`group inline-flex max-w-[12rem] items-center gap-1 border-r border-border px-1 py-1 text-[12px] transition-colors ${
                              active
                                ? "bg-background text-foreground"
                                : "bg-transparent text-muted-foreground hover:bg-muted/70 hover:text-foreground"
                            }`}
                          >
                            <button
                              type="button"
                              onClick={() => setActiveTab(path)}
                              className="inline-flex min-w-0 flex-1 items-center gap-1.5 px-2 py-1 text-left"
                              title={path}
                            >
                              <FileTypeIcon name={tabLabel(path)} isDir={false} />
                              <span className="truncate">{tabLabel(path)}</span>
                              {dirty ? (
                                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" aria-label="Unsaved" />
                              ) : null}
                            </button>
                            <button
                              type="button"
                              onClick={() => closeTab(path)}
                              className="rounded p-1 text-muted-foreground opacity-70 transition hover:bg-muted hover:text-foreground group-hover:opacity-100"
                              title="Close tab"
                              aria-label={`Close ${tabLabel(path)}`}
                            >
                              <X className="h-3 w-3" />
                            </button>
                          </div>
                        );
                      })
                    ) : (
                      <div className="px-3 py-2 text-[12px] text-muted-foreground">No open files</div>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-2 px-2 py-1.5">
                    {saveMutation.isSuccess && !isDirty ? (
                      <span className="inline-flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
                        <Check className="h-3 w-3" />
                        Saved
                      </span>
                    ) : null}
                    {isTabularPath(activeTab) ? (
                      <div className="inline-flex overflow-hidden rounded-md border border-border">
                        <button
                          type="button"
                          onClick={() => setDataViewMode("table")}
                          className={`px-2 py-1 text-[11px] ${
                            dataViewMode === "table"
                              ? "bg-muted text-foreground"
                              : "text-muted-foreground hover:text-foreground"
                          }`}
                        >
                          Table
                        </button>
                        {isEditableTabularPath(activeTab) ? (
                          <button
                            type="button"
                            onClick={() => setDataViewMode("source")}
                            className={`border-l border-border px-2 py-1 text-[11px] ${
                              dataViewMode === "source"
                                ? "bg-muted text-foreground"
                                : "text-muted-foreground hover:text-foreground"
                            }`}
                          >
                            Source
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                    {(activeTab?.endsWith(".R") || activeTab?.endsWith(".qmd")) ? (
                      <Button variant="ghost" size="sm" onClick={() => runChunkMutation.mutate()} disabled={runChunkMutation.isPending} className="h-7 gap-1 bg-blue-600/80 px-2 text-[11px] text-white hover:bg-blue-500">
                        {runChunkMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
                        Run
                      </Button>
                    ) : null}
                    {activeTab ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={toggleActiveLock}
                        disabled={locksMutation.isPending || !project?.project_dir}
                        className="h-7 gap-1 px-2 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
                        title={lockedPaths.includes(activeTab) ? "Unlock for agent edits" : "Lock against agent edits"}
                      >
                        {lockedPaths.includes(activeTab) ? <Lock className="h-3 w-3 text-amber-500" /> : <Unlock className="h-3 w-3" />}
                        {lockedPaths.includes(activeTab) ? "Locked" : "Lock"}
                      </Button>
                    ) : null}
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => saveMutation.mutate()}
                      disabled={!isDirty || saveMutation.isPending || isImagePath(activeTab) || (isTabularPath(activeTab) && !isEditableTabularPath(activeTab))}
                      className="h-7 gap-1 bg-teal-600/80 px-2 text-[11px] text-white hover:bg-teal-500 disabled:opacity-40"
                    >
                      {saveMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                      Save
                    </Button>
                  </div>
                </div>

                <div className="min-h-0 flex-1 bg-muted/40 dark:bg-[#1e1e1e]">
                  {!activeTab ? (
                    <div className="flex h-full items-center justify-center p-4 text-center text-xs text-muted-foreground">
                      Select a file to inspect or edit the generated source.
                    </div>
                  ) : isImagePath(activeTab) ? (
                    <div className="flex h-full flex-col items-center justify-center gap-3 p-6">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={api.getRawFileUrl(projectId, activeTab)}
                        alt={tabLabel(activeTab)}
                        className="max-h-full max-w-full rounded-lg border border-border object-contain shadow-sm"
                      />
                      <p className="font-mono text-[11px] text-muted-foreground">{activeTab}</p>
                    </div>
                  ) : showTableView ? (
                    previewLoading ? (
                      <div className="flex h-full items-center justify-center">
                        <Loader2 className="h-5 w-5 animate-spin text-teal-400" />
                      </div>
                    ) : filePreview ? (
                      <TabularPreview preview={filePreview} />
                    ) : (
                      <div className="flex h-full items-center justify-center p-4 text-center text-xs text-muted-foreground">
                        Could not preview this table.
                      </div>
                    )
                  ) : contentLoading ? (
                    <div className="flex h-full items-center justify-center">
                      <Loader2 className="h-5 w-5 animate-spin text-teal-400" />
                    </div>
                  ) : (
                    <div className="relative h-full w-full">
                      {inlineWidget.show ? (
                        <InlineAiWidget
                          top={inlineWidget.top}
                          left={inlineWidget.left}
                          selectedText={inlineWidget.selectionText}
                          onGenerate={handleInlineGenerate}
                          onAccept={handleInlineAccept}
                          onReject={handleInlineReject}
                          onClose={handleInlineReject}
                          isGenerating={inlineWidget.isGenerating}
                          hasGenerated={inlineWidget.hasGenerated}
                          diffStats={inlineWidget.diffStats}
                        />
                      ) : null}
                      <Editor
                        key={activeTab}
                        height="100%"
                        language={getLanguage(activeTab)}
                        theme={resolvedTheme === "light" ? "light" : "vs-dark"}
                        value={editorValue}
                        onChange={(value) => updateActiveDraft(value ?? "")}
                        onMount={(editor, monaco) => {
                          monacoEditorRef.current = editor;
                          editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => saveMutationRef.current());
                          editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyK, () => triggerInlineAi());
                          editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyI, () => triggerInlineAi());
                        }}
                        options={{
                          fontSize: 13,
                          minimap: { enabled: false },
                          scrollBeyondLastLine: false,
                          wordWrap: "on",
                          automaticLayout: true,
                          padding: { top: 8, bottom: 8 },
                          tabSize: 2,
                          lineNumbersMinChars: 3,
                        }}
                      />
                    </div>
                  )}
                </div>
              </div>
            </section>
          )}
        </div>
      </main>

    </div>
  );
}
