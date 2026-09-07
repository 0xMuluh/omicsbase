export interface TablePreview {
  file: string;
  rows: number;
  cols: number;
  markdown: string;
  url?: string;
}

export interface RCellBlockProps {
  initialCode: string;
  initialOutput?: string;
  initialPlots?: string[];
  initialTables?: TablePreview[];
  isAgentGenerated?: boolean;
  isParentRunning?: boolean;
  cellId?: string;
  executionId?: string;
  blockKey?: string;
}
