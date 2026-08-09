"use client";

import { FileImage, FileText } from "lucide-react";

import type { FileAttachment } from "@/lib/api";

function formatBytes(bytes: number | null | undefined): string | null {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return null;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function isImageAttachment(attachment: FileAttachment): boolean {
  const format = String(attachment.format || "").toLowerCase();
  return attachment.mime_type?.startsWith("image/") === true || ["png", "jpg", "jpeg", "gif", "webp", "tif", "tiff"].includes(format);
}

export function MessageAttachments({ attachments, className = "" }: { attachments?: FileAttachment[]; className?: string }) {
  if (!attachments?.length) return null;

  return (
    <div className={`flex flex-wrap gap-2 ${className}`}>
      {attachments.map((attachment, index) => {
        const Icon = isImageAttachment(attachment) ? FileImage : FileText;
        const details = [
          attachment.format || attachment.mime_type?.split("/").pop(),
          formatBytes(attachment.size_bytes),
        ].filter(Boolean).join(" · ");
        return (
          <div
            key={`${attachment.id || attachment.name}-${index}`}
            className="flex min-w-0 max-w-64 items-center gap-2 rounded-xl border border-border bg-background/70 px-2.5 py-2 text-left shadow-sm"
            title={attachment.name}
          >
            <Icon className="h-5 w-5 shrink-0 text-red-500" />
            <span className="min-w-0">
              <span className="block truncate text-xs font-medium text-foreground">{attachment.name}</span>
              <span className="block truncate text-[10px] uppercase text-muted-foreground">{details || "File"}</span>
            </span>
          </div>
        );
      })}
    </div>
  );
}

