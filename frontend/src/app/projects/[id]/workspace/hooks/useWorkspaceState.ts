"use client";

import { useState, useRef, useEffect } from "react";

export function useWorkspaceState() {
  const [promptText, setPromptText] = useState("");
  const [assistantPending, setAssistantPending] = useState(false);
  const [agentActivity, setAgentActivity] = useState("Understanding the workspace...");
  const [chatMode, setChatMode] = useState<"build" | "discuss">("build");
  const [modeOpen, setModeOpen] = useState(false);
  const modeMenuRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [openTabs, setOpenTabs] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [dataViewMode, setDataViewMode] = useState<"table" | "source">("table");
  const [iframeKey, setIframeKey] = useState(0);
  const [workspaceMode, setWorkspaceMode] = useState<"preview" | "code">("preview");
  const [viewMode, setViewMode] = useState<"chat" | "workspace" | "code">("code");

  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState<number | null>(null);
  const [isResizingSidebar, setIsResizingSidebar] = useState(false);

  const [showProjectMenu, setShowProjectMenu] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [fileSearch, setFileSearch] = useState("");
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(new Set());

  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      if (!modeMenuRef.current?.contains(event.target as Node)) {
        setModeOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, []);

  const openFileTab = (path: string) => {
    setOpenTabs((prev) => (prev.includes(path) ? prev : [...prev, path]));
    setActiveTab(path);
  };

  const closeTab = (path: string) => {
    setOpenTabs((prev) => prev.filter((t) => t !== path));
    if (activeTab === path) {
      const remaining = openTabs.filter((t) => t !== path);
      setActiveTab(remaining.length ? remaining[remaining.length - 1] : null);
    }
  };

  const updateActiveDraft = (content: string) => {
    if (activeTab) {
      setDrafts((prev) => ({ ...prev, [activeTab]: content }));
    }
  };

  return {
    promptText,
    setPromptText,
    assistantPending,
    setAssistantPending,
    agentActivity,
    setAgentActivity,
    chatMode,
    setChatMode,
    modeOpen,
    setModeOpen,
    modeMenuRef,
    fileInputRef,
    openTabs,
    setOpenTabs,
    activeTab,
    setActiveTab,
    drafts,
    setDrafts,
    dataViewMode,
    setDataViewMode,
    iframeKey,
    setIframeKey,
    workspaceMode,
    setWorkspaceMode,
    viewMode,
    setViewMode,
    sidebarOpen,
    setSidebarOpen,
    sidebarWidth,
    setSidebarWidth,
    isResizingSidebar,
    setIsResizingSidebar,
    showProjectMenu,
    setShowProjectMenu,
    showHistory,
    setShowHistory,
    fileSearch,
    setFileSearch,
    expandedPaths,
    setExpandedPaths,
    openFileTab,
    closeTab,
    updateActiveDraft,
  };
}
