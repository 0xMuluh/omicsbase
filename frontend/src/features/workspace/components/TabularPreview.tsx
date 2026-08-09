"use client";

import type { FilePreview } from "@/lib/api";

export function TabularPreview({ preview }: { preview: FilePreview }) {
  const columns = preview.columns || [];
  const rows = preview.preview_rows || [];
  const dims = preview.dimensions;
  const formatLabel =
    preview.format === "spss"
      ? "SPSS"
      : preview.format === "excel"
        ? "Excel"
        : preview.format?.toUpperCase() || "Table";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-3 py-2 text-[11px] text-muted-foreground">
        <span className="font-medium text-foreground">{formatLabel}</span>
        {dims?.rows != null && dims?.columns != null ? (
          <span>
            {dims.rows.toLocaleString()} rows × {dims.columns.toLocaleString()} columns
          </span>
        ) : null}
        {preview.selected_sheet ? <span>Sheet: {preview.selected_sheet}</span> : null}
        {preview.preview_truncated ? <span>Showing first {rows.length.toLocaleString()} rows</span> : null}
        {preview.editable === false ? <span>Read-only preview</span> : null}
      </div>
      {preview.error && !rows.length ? (
        <div className="flex h-full items-center justify-center p-6 text-center text-xs text-muted-foreground">
          <div className="max-w-md space-y-2">
            <p>{preview.note || "Could not preview this file."}</p>
            <p className="font-mono text-[10px] opacity-70">{preview.error}</p>
          </div>
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="min-w-full border-collapse text-left text-[12px]">
            <thead className="sticky top-0 z-10 bg-muted/95 backdrop-blur">
              <tr>
                <th className="border-b border-border px-2 py-1.5 font-mono text-[10px] font-normal text-muted-foreground">#</th>
                {columns.map((column) => (
                  <th
                    key={column}
                    className="border-b border-border px-2 py-1.5 font-medium text-foreground"
                    title={preview.column_types?.[column] || column}
                  >
                    <span className="block max-w-[14rem] truncate">{column}</span>
                    {preview.column_types?.[column] ? (
                      <span className="mt-0.5 block text-[10px] font-normal text-muted-foreground">
                        {preview.column_types[column]}
                      </span>
                    ) : null}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="odd:bg-background even:bg-muted/30">
                  <td className="border-b border-border/60 px-2 py-1 font-mono text-[10px] text-muted-foreground">
                    {rowIndex + 1}
                  </td>
                  {columns.map((column, colIndex) => (
                    <td key={rowIndex + "-" + column} className="max-w-[16rem] truncate border-b border-border/60 px-2 py-1 text-foreground">
                      {row[colIndex] ?? ""}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {preview.note ? (
            <p className="border-t border-border px-3 py-2 text-[11px] text-muted-foreground">{preview.note}</p>
          ) : null}
        </div>
      )}
    </div>
  );
}
