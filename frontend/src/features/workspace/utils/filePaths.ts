import type { FileTreeNode } from "@/lib/api";

export function getLanguage(path: string | null): string {
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

export function isImagePath(path: string | null | undefined): boolean {
  if (!path) return false;
  return /.(png|jpe?g|gif|svg|webp)$/i.test(path);
}

export function isTabularPath(path: string | null | undefined): boolean {
  if (!path) return false;
  return /.(csv|tsv|xlsx|xls|sav)$/i.test(path);
}

export function isEditableTabularPath(path: string | null | undefined): boolean {
  if (!path) return false;
  return /.(csv|tsv)$/i.test(path);
}

export function tabLabel(path: string): string {
  return path.split("/").pop() || path;
}

export function flattenFileTree(nodes: FileTreeNode[]): string[] {
  const paths: string[] = [];
  for (const node of nodes) {
    if (node.type === "file") paths.push(node.path);
    if (node.children?.length) paths.push(...flattenFileTree(node.children));
  }
  return paths;
}

export function collectDirPaths(nodes: FileTreeNode[]): string[] {
  const paths: string[] = [];
  for (const node of nodes) {
    if (node.type === "directory") {
      paths.push(node.path);
      if (node.children?.length) paths.push(...collectDirPaths(node.children));
    }
  }
  return paths;
}

export function filterFileTree(nodes: FileTreeNode[], query: string): FileTreeNode[] {
  const normalizedQuery = query.trim().toLowerCase();
  if (!normalizedQuery) return nodes;
  const filtered: FileTreeNode[] = [];
  for (const node of nodes) {
    if (node.type === "directory") {
      const children = filterFileTree(node.children || [], normalizedQuery);
      if (children.length > 0 || node.name.toLowerCase().includes(normalizedQuery)) {
        filtered.push({ ...node, children });
      }
      continue;
    }
    if (node.name.toLowerCase().includes(normalizedQuery) || node.path.toLowerCase().includes(normalizedQuery)) {
      filtered.push(node);
    }
  }
  return filtered;
}
