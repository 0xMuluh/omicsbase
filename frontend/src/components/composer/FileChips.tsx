"use client";

import { AnimatePresence, motion } from "framer-motion";
import { FileImage, FileText, X } from "lucide-react";

import { formatFileSize, isImageFile } from "@/lib/file";

export function FileChips({
  files,
  onRemove,
  className = "mb-1.5 flex flex-wrap gap-1.5 px-2 pt-1",
}: {
  files: File[];
  onRemove: (index: number) => void;
  className?: string;
}) {
  if (!files.length) return null;

  return (
    <AnimatePresence initial={false}>
      <motion.div
        initial={{ opacity: 0, height: 0 }}
        animate={{ opacity: 1, height: "auto" }}
        exit={{ opacity: 0, height: 0 }}
        className={className}
      >
        {files.map((file, index) => {
          const Icon = isImageFile(file) ? FileImage : FileText;
          const size = formatFileSize(file.size);
          return (
            <div
              key={file.name + "-" + index}
              className="group flex min-w-0 max-w-60 items-center gap-2 rounded-xl border border-border bg-background/80 px-2.5 py-1.5 text-left shadow-sm backdrop-blur"
              title={file.name}
            >
              <Icon className="h-4 w-4 shrink-0 text-red-500" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs font-medium text-foreground">{file.name}</span>
                {size ? <span className="block text-[10px] uppercase text-muted-foreground">{size}</span> : null}
              </span>
              <button
                type="button"
                className="rounded-full p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
                onClick={() => onRemove(index)}
                aria-label={"Remove " + file.name}
                title="Remove attachment"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          );
        })}
      </motion.div>
    </AnimatePresence>
  );
}
