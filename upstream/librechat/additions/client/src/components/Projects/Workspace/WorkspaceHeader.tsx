import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  Folder,
  MessageSquare,
  Pencil,
  Bot,
  Globe,
  RefreshCw,
  ExternalLink,
  Play,
  CheckCircle2,
  AlertCircle,
  Maximize2,
  Minimize2,
} from 'lucide-react';
import { Button, Spinner, TooltipAnchor } from '@librechat/client';
import OpenSidebar from '~/components/Chat/Menus/OpenSidebar';
import { useLocalize } from '~/hooks';
import { cn } from '~/utils';
import {
  CanvasViewMode,
  AgentEngineKey,
  ProjectStatusResponse,
  ProjectSessionOption,
  WorkspaceCanvasProps,
} from './types';
import ThreadDropdown from './ThreadDropdown';
import EngineDropdown from './EngineDropdown';

interface WorkspaceHeaderProps {
  project: WorkspaceCanvasProps['project'];
  projectId: string;
  projectName: string;
  viewMode: CanvasViewMode;
  setViewMode: (mode: CanvasViewMode) => void;
  conversationId: string | null;
  projectConversations: ProjectSessionOption[];
  selectedEngine: AgentEngineKey;
  isSmallScreen: boolean;
  isFullscreen: boolean;
  setIsFullscreen: React.Dispatch<React.SetStateAction<boolean>>;
  projectStatus: ProjectStatusResponse | null;
  isRendering: boolean;
  isLaunching: boolean;
  quartoSiteUrl: string;
  renderMessage: string | null;
  setRenderMessage: (msg: string | null) => void;
  onNavigateNotes?: () => void;
  onEditProject?: () => void;
  onRenderQuarto: () => void;
  onRefreshReport: () => void;
  onResetSession: () => void;
  onSelectConversation: (convoId: string) => void;
  onLaunchNewThread: () => void;
  onSelectEngine: (engineKey: AgentEngineKey) => void;
}

export default function WorkspaceHeader({
  project,
  projectId: _projectId,
  projectName,
  viewMode,
  setViewMode,
  conversationId,
  projectConversations,
  selectedEngine,
  isSmallScreen,
  isFullscreen,
  setIsFullscreen,
  projectStatus,
  isRendering,
  isLaunching,
  quartoSiteUrl,
  renderMessage,
  setRenderMessage,
  onNavigateNotes,
  onEditProject,
  onRenderQuarto,
  onRefreshReport,
  onResetSession,
  onSelectConversation,
  onLaunchNewThread,
  onSelectEngine,
}: WorkspaceHeaderProps) {
  const navigate = useNavigate();
  const localize = useLocalize();
  const [sessionDropdownOpen, setSessionDropdownOpen] = useState(false);
  const [engineDropdownOpen, setEngineDropdownOpen] = useState(false);

  return (
    <>
      <header className="flex h-12 w-full shrink-0 items-center justify-between border-b border-border-light bg-presentation px-3">
        {/* Left: Navigation & Project Breadcrumb */}
        <div className="flex items-center gap-2">
          {isSmallScreen ? <OpenSidebar className="size-8 shrink-0" /> : null}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => navigate('/projects')}
            className="-ml-1 h-8 gap-1.5 px-2 text-xs text-text-secondary hover:text-text-primary"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
            <span className="hidden sm:inline">{localize('com_ui_all_projects')}</span>
          </Button>
          <span className="text-border-light">/</span>
          <span className="flex items-center gap-1.5 text-xs font-semibold text-text-primary truncate max-w-[140px] md:max-w-xs">
            <Folder className="h-3.5 w-3.5 text-primary shrink-0" />
            <span className="truncate">{projectName}</span>
          </span>
        </div>

        {/* Center: Mode Switcher */}
        <div className="flex items-center gap-0.5 rounded-lg border border-border-light bg-surface-secondary/80 p-0.5">
          <TooltipAnchor
            description="Notes"
            render={
              <button
                type="button"
                onClick={onNavigateNotes}
                className="flex h-7 items-center gap-1 rounded-md px-2 text-xs font-medium text-text-secondary transition-all hover:bg-surface-tertiary/60 hover:text-text-primary"
                aria-label="Notes"
              >
                <MessageSquare className="h-3.5 w-3.5 text-primary" />
                {project.conversationCount != null && project.conversationCount > 0 && (
                  <span className="rounded-full bg-surface-tertiary px-1 text-[10px] font-semibold text-text-secondary">
                    {project.conversationCount}
                  </span>
                )}
              </button>
            }
          />

          <TooltipAnchor
            description="Workspace"
            render={
              <button
                type="button"
                onClick={() => setViewMode('agent')}
                className={cn(
                  'flex h-7 items-center gap-1 rounded-md px-2 text-xs font-medium transition-all',
                  viewMode === 'agent'
                    ? 'bg-surface-primary text-text-primary shadow-xs'
                    : 'text-text-secondary hover:bg-surface-tertiary/60 hover:text-text-primary',
                )}
                aria-label="Workspace"
              >
                <Bot className="h-3.5 w-3.5 text-primary" />
                {conversationId && (
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 shadow-xs" />
                )}
              </button>
            }
          />

          <TooltipAnchor
            description="Report preview"
            render={
              <button
                type="button"
                onClick={() => setViewMode('quarto')}
                className={cn(
                  'flex h-7 items-center gap-1 rounded-md px-2 text-xs font-medium transition-all',
                  viewMode === 'quarto'
                    ? 'bg-surface-primary text-text-primary shadow-xs'
                    : 'text-text-secondary hover:bg-surface-tertiary/60 hover:text-text-primary',
                )}
                aria-label="Report preview"
              >
                <Globe className="h-3.5 w-3.5 text-sky-500" />
                {projectStatus?.has_site ? (
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 shadow-xs" />
                ) : (
                  <span className="h-1.5 w-1.5 rounded-full bg-amber-500 shadow-xs" />
                )}
              </button>
            }
          />
        </div>

        {/* Right: Actions & Tools */}
        <div className="flex items-center gap-1">
          {/* Quarto Status Indicator */}
          <TooltipAnchor
            description={projectStatus?.has_site ? 'Site ready' : 'Site not built'}
            render={
              <div
                className="flex h-7 w-7 items-center justify-center rounded-lg text-text-secondary"
                aria-label="Site status"
              >
                {projectStatus?.has_site ? (
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                ) : (
                  <AlertCircle className="h-3.5 w-3.5 text-amber-500/80" />
                )}
              </div>
            }
          />

          {/* Render Quarto Report */}
          <TooltipAnchor
            description="Render report"
            render={
              <Button
                type="button"
                variant="outline"
                size="icon"
                onClick={onRenderQuarto}
                disabled={isRendering}
                className="h-7 w-7 text-text-secondary hover:text-text-primary"
                aria-label="Render report"
              >
                {isRendering ? (
                  <Spinner className="h-3 w-3 text-text-primary" />
                ) : (
                  <Play className="text-primary fill-primary/20 h-3 w-3" />
                )}
              </Button>
            }
          />

          {/* Popout preview link */}
          {projectStatus?.has_site && (
            <TooltipAnchor
              description="Open site"
              render={
                <a
                  href={quartoSiteUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="flex h-7 w-7 items-center justify-center rounded-lg border border-border-light bg-surface-secondary text-text-secondary transition-colors hover:bg-surface-hover hover:text-text-primary"
                  aria-label="Open site"
                >
                  <ExternalLink className="h-3 w-3" />
                </a>
              }
            />
          )}

          {/* Interactive Agent Thread History Dropdown */}
          {viewMode === 'agent' && (
            <ThreadDropdown
              conversationId={conversationId}
              projectConversations={projectConversations}
              isOpen={sessionDropdownOpen}
              setIsOpen={setSessionDropdownOpen}
              onSelectConversation={onSelectConversation}
              onNewThread={onLaunchNewThread}
              isLaunching={isLaunching}
            />
          )}

          {/* Interactive Agent Engine Dropdown Menu */}
          {viewMode === 'agent' && (
            <EngineDropdown
              selectedEngine={selectedEngine}
              isOpen={engineDropdownOpen}
              setIsOpen={setEngineDropdownOpen}
              onSelectEngine={onSelectEngine}
              isLaunching={isLaunching}
            />
          )}

          {/* Refresh Report Preview */}
          {viewMode === 'quarto' && (
            <TooltipAnchor
              description="Refresh report"
              render={
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={onRefreshReport}
                  className="h-7 w-7 text-text-secondary hover:text-text-primary"
                  aria-label="Refresh report"
                >
                  <RefreshCw className="h-3 w-3" />
                </Button>
              }
            />
          )}

          {/* Reset Session Button */}
          {viewMode === 'agent' && conversationId && (
            <TooltipAnchor
              description="Reset session"
              render={
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={onResetSession}
                  className="h-7 w-7 text-text-secondary hover:text-text-primary"
                  aria-label="Reset session"
                >
                  <RefreshCw className="h-3 w-3" />
                </Button>
              }
            />
          )}

          {/* Fullscreen Canvas Toggle */}
          <TooltipAnchor
            description={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
            render={
              <button
                type="button"
                onClick={() => setIsFullscreen((prev) => !prev)}
                className={cn(
                  'flex h-7 w-7 items-center justify-center rounded-lg text-text-tertiary transition-colors hover:bg-surface-hover hover:text-text-primary',
                  isFullscreen && 'bg-surface-hover text-text-primary',
                )}
                aria-label={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
              >
                {isFullscreen ? <Minimize2 className="h-3 w-3" /> : <Maximize2 className="h-3 w-3" />}
              </button>
            }
          />

          {/* Edit Project */}
          {onEditProject && (
            <TooltipAnchor
              description={localize('com_ui_edit_project')}
              render={
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 text-text-secondary hover:text-text-primary"
                  aria-label={localize('com_ui_edit_project')}
                  onClick={onEditProject}
                >
                  <Pencil className="h-3 w-3" aria-hidden="true" />
                </Button>
              }
            />
          )}
        </div>
      </header>

      {/* Optional feedback banner */}
      {renderMessage && (
        <div className="flex items-center justify-between border-b border-border-light bg-surface-tertiary/50 px-4 py-1.5 text-xs text-text-secondary">
          <span>{renderMessage}</span>
          <button
            type="button"
            onClick={() => setRenderMessage(null)}
            className="text-text-tertiary hover:text-text-primary"
          >
            ×
          </button>
        </div>
      )}
    </>
  );
}
