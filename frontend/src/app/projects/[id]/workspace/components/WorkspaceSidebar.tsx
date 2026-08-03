"use client";

import Link from "next/link";
import { FileTreeNode, Project } from "@/lib/api";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ProjectsSidebarContent } from "@/components/ProjectsSidebar";
import {
  ArrowUpDown,
  Braces,
  ChevronDown,
  ChevronRight,
  ChevronsDownUp,
  ChevronsLeft,
  ChevronsUpDown,
  File,
  FileCode,
  FileText,
  Globe,
  Image,
  Lock,
  Search,
  Table2,
  X,
} from "lucide-react";

function FileTypeIcon({ name, isDir }: { name: string; isDir: boolean }) {
  if (isDir) return null;
  const lower = name.toLowerCase();
  if (lower.endsWith(".qmd") || lower.endsWith(".md")) {
    return <FileText className="h-3.5 w-3.5 shrink-0 text-cyan-500 dark:text-cyan-400" />;
  }
  if (lower.endsWith(".r")) {
    return <FileCode className="h-3.5 w-3.5 shrink-0 text-blue-500 dark:text-blue-400" />;
  }
  if (lower.endsWith(".yml") || lower.endsWith(".yaml")) {
    return <FileCode className="h-3.5 w-3.5 shrink-0 text-amber-500 dark:text-amber-400" />;
  }
  if (lower.endsWith(".json")) {
    return <Braces className="h-3.5 w-3.5 shrink-0 text-yellow-500 dark:text-yellow-400" />;
  }
  if (lower.endsWith(".html") || lower.endsWith(".htm")) {
    return <Globe className="h-3.5 w-3.5 shrink-0 text-orange-500 dark:text-orange-400" />;
  }
  if (lower.endsWith(".css") || lower.endsWith(".scss")) {
    return <FileCode className="h-3.5 w-3.5 shrink-0 text-pink-500 dark:text-pink-400" />;
  }
  if (/\.(png|jpe?g|gif|svg|webp)$/.test(lower)) {
    return <Image className="h-3.5 w-3.5 shrink-0 text-violet-500 dark:text-violet-400" />;
  }
  if (/\.(csv|tsv)$/.test(lower)) {
    return <Table2 className="h-3.5 w-3.5 shrink-0 text-emerald-500 dark:text-emerald-400" />;
  }
  if (/\.(xlsx|xls|sav)$/.test(lower)) {
    return <Table2 className="h-3.5 w-3.5 shrink-0 text-teal-500 dark:text-teal-400" />;
  }
  if (lower.endsWith(".rds") || lower.endsWith(".rda")) {
    return <File className="h-3.5 w-3.5 shrink-0 text-indigo-500 dark:text-indigo-400" />;
  }
  return <File className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />;
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
            ? "bg-teal-500/15 text-teal-300 font-medium"
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

interface WorkspaceSidebarProps {
  projectsList?: Project[];
  fileTree?: FileTreeNode[];
  fileSearch: string;
  setFileSearch: (q: string) => void;
  activeTab: string | null;
  expandedPaths: Set<string>;
  onToggleDir: (path: string) => void;
  onSelectFile: (path: string) => void;
  lockedPaths: string[];
  isSimpleChat?: boolean;
  onCloseSidebar?: () => void;
}

export function WorkspaceSidebar({
  fileTree,
  fileSearch,
  setFileSearch,
  activeTab,
  expandedPaths,
  onToggleDir,
  onSelectFile,
  lockedPaths,
  isSimpleChat = false,
  onCloseSidebar,
}: WorkspaceSidebarProps) {
  // Simple Chat Mode: Exact same Projects Sidebar as Home Page (Image 2)
  if (isSimpleChat) {
    return <ProjectsSidebarContent />;
  }

  // Full Workspace Mode: Code & File Tree Sidebar (Image 3)
  const filterFileTree = (nodes: FileTreeNode[], query: string): FileTreeNode[] => {
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
  };

  const toggleExpandAll = () => {
    if (!fileTree) return;
    const getAllDirPaths = (nodes: FileTreeNode[]): string[] => {
      let paths: string[] = [];
      for (const node of nodes) {
        if (node.type === "directory") {
          paths.push(node.path);
          if (node.children) {
            paths = [...paths, ...getAllDirPaths(node.children)];
          }
        }
      }
      return paths;
    };
    const allDirs = getAllDirPaths(fileTree);
    if (expandedPaths.size >= allDirs.length && allDirs.length > 0) {
      allDirs.forEach((p) => onToggleDir(p));
    } else {
      allDirs.forEach((p) => {
        if (!expandedPaths.has(p)) onToggleDir(p);
      });
    }
  };

  const filteredTree = filterFileTree(fileTree || [], fileSearch);

  return (
    <div className="flex h-full w-full flex-col border-r border-border bg-sidebar text-sidebar-foreground">
      {/* Search Header */}
      <div className="flex shrink-0 items-center gap-1.5 border-b border-border p-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-muted-foreground" />
          <input
            type="text"
            placeholder="Search code.."
            value={fileSearch}
            onChange={(e) => setFileSearch(e.target.value)}
            className="w-full rounded-md border border-border bg-background py-1 pl-8 pr-2 text-xs placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-teal-500"
          />
        </div>
        <button
          type="button"
          onClick={() => {
            if (!fileTree) return;
            const getAllDirPaths = (nodes: FileTreeNode[]): string[] => {
              let paths: string[] = [];
              for (const node of nodes) {
                if (node.type === "directory") {
                  paths.push(node.path);
                  if (node.children) paths = [...paths, ...getAllDirPaths(node.children)];
                }
              }
              return paths;
            };
            getAllDirPaths(fileTree).forEach((p) => {
              if (!expandedPaths.has(p)) onToggleDir(p);
            });
          }}
          className="inline-flex h-7 w-7 items-center justify-center rounded border border-border text-muted-foreground hover:bg-muted hover:text-foreground"
          title="Expand all"
        >
          <ChevronsUpDown className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={() => {
            if (!fileTree) return;
            const getAllDirPaths = (nodes: FileTreeNode[]): string[] => {
              let paths: string[] = [];
              for (const node of nodes) {
                if (node.type === "directory") {
                  paths.push(node.path);
                  if (node.children) paths = [...paths, ...getAllDirPaths(node.children)];
                }
              }
              return paths;
            };
            getAllDirPaths(fileTree).forEach((p) => {
              if (expandedPaths.has(p)) onToggleDir(p);
            });
          }}
          className="inline-flex h-7 w-7 items-center justify-center rounded border border-border text-muted-foreground hover:bg-muted hover:text-foreground"
          title="Collapse all"
        >
          <ChevronsDownUp className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Code / File Tree List */}
      <ScrollArea className="flex-1 p-2">
        {filteredTree.length > 0 ? (
          <div className="space-y-0.5">
            {filteredTree.map((node) => (
              <TreeNode
                key={node.path}
                node={node}
                selectedPath={activeTab}
                expandedPaths={expandedPaths}
                onToggle={onToggleDir}
                onSelect={onSelectFile}
              />
            ))}
          </div>
        ) : (
          <div className="p-4 text-center text-xs text-muted-foreground">
            {fileSearch ? "No matching files" : "Workspace files will appear here"}
          </div>
        )}
      </ScrollArea>

      {/* Active Locks Footer */}
      {lockedPaths.length > 0 && (
        <div className="border-t border-border bg-muted/40 p-2.5 text-[11px] text-muted-foreground">
          <div className="mb-1 flex items-center gap-1.5 font-medium text-foreground">
            <Lock className="h-3 w-3 text-amber-500" />
            <span>Active Locks ({lockedPaths.length})</span>
          </div>
          <p className="line-clamp-2 text-[10px] text-muted-foreground/80">
            Agent is generating report code in: {lockedPaths.join(", ")}
          </p>
        </div>
      )}
    </div>
  );
}
