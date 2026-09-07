import { useMemo } from 'react';
import RCellBlock from './RCell';

type ToolArgs = string | { code?: unknown; cell_id?: unknown };

export function useNoteTool({
  function_name,
  name,
  _args,
  output,
  phase,
  toolCallId,
}: {
  function_name: string;
  name: string;
  _args: ToolArgs;
  output?: string | null;
  phase: string;
  toolCallId?: string;
}) {
  const isRCellTool = function_name === 'execute_r_cell' || name?.includes('execute_r_cell');
  const parsedRCode = useMemo(() => {
    if (!_args) return '';
    if (typeof _args === 'object' && _args !== null) {
      return String(_args.code || '').trim();
    }
    if (typeof _args === 'string') {
      try {
        const obj = JSON.parse(_args);
        if (obj && typeof obj === 'object') {
          return String((obj as { code?: unknown; cell_id?: unknown }).code || '').trim();
        }
      } catch {
        // Streamed JSON chunk: extract code content while streaming
        const match = /"code"\s*:\s*"((?:[^"\\]|\\.)*)/.exec(_args);
        if (match && match[1]) {
          try {
            return JSON.parse(`"${match[1]}"`);
          } catch {
            return match[1].replace(/\\n/g, '\n').replace(/\\"/g, '"');
          }
        }
        if (!_args.trim().startsWith('{')) {
          return _args.trim();
        }
        return '';
      }
    }
    return '';
  }, [_args]);

  const parsedCellId = useMemo(() => {
    if (!_args) return undefined;
    if (typeof _args === 'object' && _args !== null) {
      const id = String(_args.cell_id || '').trim();
      return id || undefined;
    }
    if (typeof _args === 'string') {
      try {
        const obj = JSON.parse(_args);
        if (obj && typeof obj === 'object') {
          const id = String((obj as { code?: unknown; cell_id?: unknown }).cell_id || '').trim();
          return id || undefined;
        }
      } catch {
        const match = /"cell_id"\s*:\s*"([^"\\]*)"/.exec(_args);
        if (match && match[1]) {
          return match[1].trim() || undefined;
        }
      }
    }
    return undefined;
  }, [_args]);

  if (isRCellTool && (parsedRCode || output)) {
    const metaMatch = output
      ? /<!--\s*noteCell\s+cellId=([^\s]+)\s+executionId=([^\s]+)/.exec(output)
      : null;
    return (
      <div className="my-2 w-full">
        <RCellBlock
          initialCode={parsedRCode}
          initialOutput={output ?? undefined}
          isAgentGenerated={true}
          isParentRunning={phase === 'running'}
          cellId={metaMatch?.[1] || parsedCellId}
          executionId={metaMatch?.[2]}
          blockKey={toolCallId ? `tool-${toolCallId}` : undefined}
        />
      </div>
    );
  }

  return null;
}
