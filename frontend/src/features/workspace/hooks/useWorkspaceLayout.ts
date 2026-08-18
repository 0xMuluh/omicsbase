"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { Project } from "@/lib/api";

interface UseWorkspaceLayoutOptions {
  project?: Project;
  previewProgressSignature: string;
  workspaceRefreshKey: number;
}

export function useWorkspaceLayout({
  project,
  previewProgressSignature,
  workspaceRefreshKey,
}: UseWorkspaceLayoutOptions) {
  const workspaceChatScrollRef = useRef<HTMLDivElement>(null);
  const [iframeKey, setIframeKey] = useState(0);
  const [workspaceMode, setWorkspaceMode] = useState<"preview" | "code">(
    project?.status === "failed" ? "code" : "preview",
  );
  const [viewMode, setViewMode] = useState<"chat" | "workspace">("chat");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState<number | null>(null);
  const [isResizingSidebar, setIsResizingSidebar] = useState(false);
  const [showProjectMenu, setShowProjectMenu] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  const refreshPreview = useCallback(() => {
    setIframeKey((value) => value + 1);
  }, []);

  useEffect(() => {
    if (project?.project_dir) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- refresh the preview after workspace output changes
      setIframeKey((value) => value + 1);
    }
  }, [project?.project_dir, project?.status, previewProgressSignature, workspaceRefreshKey]);

  useEffect(() => {
    if (project?.status === "failed") {
      // A failed build must expose its generated source before asking the user to retry.
      // eslint-disable-next-line react-hooks/set-state-in-effect -- follow the persisted failure state
      setWorkspaceMode("code");
    }
  }, [project?.status]);

  useEffect(() => {
    if (project?.project_dir || ["generated", "completed", "rendering"].includes(project?.status || "")) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- follow the persisted workspace capability
      setViewMode("workspace");
    } else if (project?.status === "created") {
      setViewMode("chat");
    }
  }, [project?.project_dir, project?.status]);

  return {
    iframeKey,
    isResizingSidebar,
    refreshPreview,
    setIframeKey,
    setIsResizingSidebar,
    setShowHistory,
    setShowProjectMenu,
    setSidebarOpen,
    setSidebarWidth,
    setViewMode,
    setWorkspaceMode,
    showHistory,
    showProjectMenu,
    sidebarOpen,
    sidebarWidth,
    viewMode,
    workspaceChatScrollRef,
    workspaceMode,
  };
}
