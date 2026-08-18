"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Project } from "@/lib/api";
import {
  collectDirPaths,
  filterFileTree,
  findFileTreeNode,
  isEditableTabularPath,
  isEditableWorkspacePath,
  isReadOnlyWorkspacePath,
  isTextPath,
  isTabularPath,
} from "../utils/filePaths";

interface UseWorkspaceFilesOptions {
  projectId: string;
  project?: Project;
}

export function useWorkspaceFiles({ projectId, project }: UseWorkspaceFilesOptions) {
  const queryClient = useQueryClient();
  const [openTabs, setOpenTabs] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [fileSaveError, setFileSaveError] = useState<string | null>(null);
  const [showConflictDialog, setShowConflictDialog] = useState(false);
  const [dataViewMode, setDataViewMode] = useState<"table" | "source">("table");
  const [fileSearch, setFileSearch] = useState("");
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(new Set());
  const treeExpandedInitRef = useRef(false);

  const fileTreeQuery = useQuery({
    queryKey: ["fileTree", projectId],
    queryFn: () => api.getFileTree(projectId),
    // Live streaming: while generation runs, files appear as they are written.
    refetchInterval: project?.status === "generating" ? 2000 : false,
  });
  const projectFilesQuery = useQuery({
    queryKey: ["projectFiles", projectId],
    queryFn: () => api.listFiles(projectId),
    enabled: project?.status === "created",
  });
  const locksQuery = useQuery({
    queryKey: ["locks", projectId],
    queryFn: () => api.getLocks(projectId),
    enabled: Boolean(project?.project_dir),
  });

  const showTableView = Boolean(activeTab && isTabularPath(activeTab) && dataViewMode === "table");
  const needsTextContent = Boolean(
    activeTab
    && isTextPath(activeTab)
    && (!isTabularPath(activeTab) || (isEditableTabularPath(activeTab) && dataViewMode === "source")),
  );
  const fileContentQuery = useQuery({
    queryKey: ["fileContent", projectId, activeTab],
    queryFn: () => (activeTab ? api.getFileContent(projectId, activeTab) : null),
    enabled: needsTextContent,
  });
  const filePreviewQuery = useQuery({
    queryKey: ["filePreview", projectId, activeTab],
    queryFn: () => (activeTab ? api.getFilePreview(projectId, activeTab) : null),
    enabled: Boolean(activeTab) && isTabularPath(activeTab),
  });

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset the table view when a new file is selected
    setDataViewMode("table");
  }, [activeTab]);

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!activeTab || drafts[activeTab] === undefined) {
        throw new Error("No file selected");
      }
      const baseSha256 = fileContentQuery.data?.sha256;
      if (!baseSha256) {
        throw new Error("Reload the file before saving so its SHA-256 is known.");
      }
      return api.saveFileContent(projectId, activeTab, drafts[activeTab], baseSha256);
    },
    onMutate: () => setFileSaveError(null),
    onError: (error) => {
      const message = error instanceof Error ? error.message : "Save failed; reload the file before retrying.";
      setFileSaveError(message);
      if (/409|conflict|changed since/i.test(message)) setShowConflictDialog(true);
    },
    onSuccess: () => {
      setFileSaveError(null);
      if (activeTab) {
        setDrafts((prev) => {
          if (!(activeTab in prev)) return prev;
          const next = { ...prev };
          delete next[activeTab];
          return next;
        });
      }
      void queryClient.invalidateQueries({ queryKey: ["fileContent", projectId, activeTab] });
      void queryClient.invalidateQueries({ queryKey: ["filePreview", projectId, activeTab] });
      void queryClient.invalidateQueries({ queryKey: ["fileTree", projectId] });
    },
  });

  const runChunkMutation = useMutation({
    mutationFn: () => {
      const codeToRun = activeTab ? drafts[activeTab] ?? fileContentQuery.data?.content ?? "" : "";
      return api.runCodeChunk(projectId, codeToRun, activeTab);
    },
  });

  const locksMutation = useMutation({
    mutationFn: (paths: string[]) => api.updateLocks(projectId, paths),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["locks", projectId] });
    },
  });

  const selectTab = (path: string) => {
    setOpenTabs((prev) => (prev.includes(path) ? prev : [...prev, path]));
    setFileSaveError(null);
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
      if (fileContentQuery.data?.path === activeTab && value === fileContentQuery.data.content) {
        if (!(activeTab in prev)) return prev;
        const next = { ...prev };
        delete next[activeTab];
        return next;
      }
      return { ...prev, [activeTab]: value };
    });
  };

  const discardDraft = (path: string) => {
    void queryClient.invalidateQueries({ queryKey: ["fileContent", projectId, path] });
    setDrafts((prev) => {
      if (!(path in prev)) return prev;
      const next = { ...prev };
      delete next[path];
      return next;
    });
    setFileSaveError(null);
  };

  const clearDrafts = () => {
    setDrafts({});
    setFileSaveError(null);
  };

  const toggleActiveLock = () => {
    if (!activeTab) return;
    const lockedPaths = locksQuery.data?.paths || [];
    const next = lockedPaths.includes(activeTab)
      ? lockedPaths.filter((path) => path !== activeTab)
      : [...lockedPaths, activeTab];
    locksMutation.mutate(next);
  };

  const visibleFileTree = useMemo(
    () => filterFileTree(fileTreeQuery.data || [], fileSearch),
    [fileTreeQuery.data, fileSearch],
  );
  const activeFileNode = useMemo(
    () => findFileTreeNode(fileTreeQuery.data || [], activeTab),
    [fileTreeQuery.data, activeTab],
  );
  const activeFileEditable = Boolean(
    activeTab
    && activeFileNode?.type === "file"
    && activeFileNode.editable !== false
    && isEditableWorkspacePath(activeTab),
  );
  const allDirPaths = useMemo(
    () => collectDirPaths(fileTreeQuery.data || []),
    [fileTreeQuery.data],
  );

  useEffect(() => {
    if (!fileTreeQuery.data?.length || treeExpandedInitRef.current) return;
    setExpandedPaths(new Set(collectDirPaths(fileTreeQuery.data)));
    treeExpandedInitRef.current = true;
  }, [fileTreeQuery.data]);

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

  const activeDraft = activeTab ? drafts[activeTab] : undefined;
  const editorValue = activeDraft ?? fileContentQuery.data?.content ?? "";
  const isDirty = Boolean(
    activeTab
    && activeDraft !== undefined
    && fileContentQuery.data?.path === activeTab
    && activeDraft !== fileContentQuery.data.content,
  );
  const dirtyTabs = useMemo(() => new Set(Object.keys(drafts)), [drafts]);
  const saveMutationRef = useRef<() => void>(() => {});
  useEffect(() => {
    saveMutationRef.current = () => {
      if (isDirty && !saveMutation.isPending) saveMutation.mutate();
    };
  }, [isDirty, saveMutation]);

  return {
    activeDraft,
    activeTab,
    activeFileEditable,
    activeFileReadOnly: isReadOnlyWorkspacePath(activeTab),

    clearDrafts,
    closeConflictDialog: () => setShowConflictDialog(false),
    closeTab,
    dataViewMode,
    discardDraft,
    dirtyTabs,
    editorValue,
    effectiveExpandedPaths,
    expandAll: () => setExpandedPaths(new Set(allDirPaths)),
    collapseAll: () => setExpandedPaths(new Set()),
    contentLoading: fileContentQuery.isLoading,
    fileContent: fileContentQuery.data,
    filePreview: filePreviewQuery.data,
    fileSaveError,
    fileSearch,
    fileTree: fileTreeQuery.data,
    hasProjectFiles: Boolean(project?.project_dir || (fileTreeQuery.data && fileTreeQuery.data.length > 0)),
    hasUploadedFiles: Boolean(projectFilesQuery.data && projectFilesQuery.data.length > 0),
    isDirty,
    locksMutation,
    lockedPaths: locksQuery.data?.paths || [],
    openTabs,
    previewLoading: filePreviewQuery.isLoading,
    projectFiles: projectFilesQuery.data,
    runChunkMutation,
    saveMutation,
    saveMutationRef,
    selectTab,
    setDataViewMode,
    setFileSearch,
    showConflictDialog,
    showTableView,
    toggleActiveLock,
    toggleDir,
    treeLoading: fileTreeQuery.isLoading,
    updateActiveDraft,
    visibleFileTree,
  };
}
