import {
  Braces,
  File,
  FileCode,
  FileText,
  Globe,
  Image as ImageIcon,
  Table2,
} from "lucide-react";

export function FileTypeIcon({ name, isDir }: { name: string; isDir: boolean }) {
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
    return <ImageIcon className="h-3.5 w-3.5 shrink-0 text-violet-600 dark:text-violet-400" />;
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
