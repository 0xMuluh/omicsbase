import type { FileAttachment } from "@/lib/api/types/messages";
import type { NoteCell, NoteCellRevision, NoteCellType } from "@/lib/api/types/notes";

export function latestRevision(cell: NoteCell): NoteCellRevision | null {
  return cell.revisions[cell.revisions.length - 1] || null;
}

export function getRevisionAttachments(metadata: NoteCellRevision["metadata"]): FileAttachment[] {
  const value: unknown = metadata?.attachments;
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is FileAttachment => {
    if (typeof item !== "object" || item === null) return false;
    const record = item as Record<string, unknown>;
    return typeof record.name === "string";
  });
}

export function cellTypeLabel(type: NoteCellType): string {
  return type === "markdown"
    ? "Markdown"
    : type === "agent"
      ? "Question"
      : type === "code"
        ? "Code"
        : type === "output"
          ? "Output"
          : "Provenance";
}
