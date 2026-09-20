import React from 'react';
import { type TChatProject } from 'librechat-data-provider';

export type CanvasViewMode = 'agent' | 'quarto';

export type AgentEngineKey = 'openhands' | 'codex' | 'claude-code' | 'gemini-cli';

export interface AgentEngineOption {
  key: AgentEngineKey;
  name: string;
  provider: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
}

export interface ProjectStatusResponse {
  project_id: string;
  has_site: boolean;
  report_url: string | null;
  files: string[];
}

export interface ProjectSessionOption {
  conversation_id: string;
  title: string;
  created_at: number | null;
  event_count: number;
  agent_engine: string;
}

export interface WorkspaceCanvasProps {
  project: TChatProject;
  onNavigateNotes?: () => void;
  onEditProject?: () => void;
}
