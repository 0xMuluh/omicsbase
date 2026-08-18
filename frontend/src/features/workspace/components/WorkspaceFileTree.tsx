"use client";

import { ChevronDown, ChevronRight, ChevronsDownUp, ChevronsUpDown, Loader2, Lock, Search } from "lucide-react";

import type { FileTreeNode } from "@/lib/api";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useWorkspaceFiles } from "../hooks/useWorkspaceFiles";
import { FileTypeIcon } from "./FileTypeIcon";

type WorkspaceFilesState = ReturnType<typeof useWorkspaceFiles>;

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
        className={"flex cursor-pointer items-center gap-1.5 rounded-md px-2 py-1.5 text-[13px] transition-colors " + (
          isSelected
            ? "bg-teal-500/15 text-teal-800 dark:bg-teal-500/20 dark:text-teal-200"
            : "text-muted-foreground hover:bg-muted hover:text-foreground"
        )}
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
        {!isDir && node.editable === false ? (
          <Lock className="ml-auto h-3 w-3 shrink-0 text-muted-foreground/70" aria-label="Read-only project file" />
        ) : null}
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

export function WorkspaceFileTree({ files }: { files: WorkspaceFilesState }) {
  const {
    activeTab,
    collapseAll,
    effectiveExpandedPaths,
    expandAll,
    fileSearch,
    fileTree,
    setFileSearch,
    selectTab,
    toggleDir,
    treeLoading,
    visibleFileTree,
  } = files;

  return (
    <div className="flex min-h-0 flex-col border-r border-border bg-muted/30">
      <div className="shrink-0 border-b border-border px-3 py-2.5">
        <div className="flex items-center gap-1.5">
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              type="search"
              value={fileSearch}
              onChange={(event) => setFileSearch(event.target.value)}
              placeholder="Search project files"
              className="h-8 w-full rounded-lg border border-border bg-background pl-8 pr-2.5 text-[13px] text-foreground outline-none placeholder:text-muted-foreground focus:border-teal-500/40"
            />
          </div>
          <button
            type="button"
            onClick={expandAll}
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border text-muted-foreground transition hover:bg-muted hover:text-foreground"
            title="Expand all"
          >
            <ChevronsUpDown className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={collapseAll}
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
                onSelect={selectTab}
              />
            ))}
          </div>
        )}
      </ScrollArea>
    </div>
  );
}
