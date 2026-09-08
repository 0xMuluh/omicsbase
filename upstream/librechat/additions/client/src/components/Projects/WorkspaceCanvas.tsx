import React, { useState, useEffect, useCallback, useRef } from 'react';
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
  Sparkles,
  Layers,
  FileCode,
  Maximize2,
  Minimize2,
} from 'lucide-react';
import { Button, Spinner, TooltipAnchor, useMediaQuery } from '@librechat/client';
import { request, type TChatProject } from 'librechat-data-provider';
import OpenSidebar from '~/components/Chat/Menus/OpenSidebar';
import { useAuthContext } from '~/hooks/AuthContext';
import { useLocalize } from '~/hooks';
import { cn } from '~/utils';
import useEmbeddedTheme from './useEmbeddedTheme';

interface WorkspaceCanvasProps {
  project: TChatProject;
  onNavigateNotes?: () => void;
  onEditProject?: () => void;
}

type CanvasViewMode = 'agent' | 'quarto';

interface ProjectStatusResponse {
  project_id: string;
  has_site: boolean;
  report_url: string | null;
  files: string[];
}

export default function WorkspaceCanvas({
  project,
  onNavigateNotes,
  onEditProject,
}: WorkspaceCanvasProps) {
  const navigate = useNavigate();
  const localize = useLocalize();
  const isSmallScreen = useMediaQuery('(max-width: 768px)');
  const projectId = project._id || 'default';
  const projectName = project.name || projectId;
  const { user } = useAuthContext();
  const storageKey = `omicsbase_workspace_session_v2_${user?.id}_${projectId}`;
  const [workspaceAuthorized, setWorkspaceAuthorized] = useState(false);

  const [conversationId, setConversationId] = useState<string | null>(() => {
    return localStorage.getItem(storageKey);
  });
  const [viewMode, setViewMode] = useState<CanvasViewMode>('agent');
  const [isLaunching, setIsLaunching] = useState(false);
  const [isRendering, setIsRendering] = useState(false);
  const [projectStatus, setProjectStatus] = useState<ProjectStatusResponse | null>(null);
  const [renderMessage, setRenderMessage] = useState<string | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isFullscreen) {
        setIsFullscreen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isFullscreen]);

  const engineBaseUrl = `${window.location.origin}/omics-engine`;
  const openhandsBaseUrl = 'https://openhands.learnpanta.com';
  const agentFrame = useRef<HTMLIFrameElement>(null);
  const sendAgentTheme = useEmbeddedTheme(agentFrame, openhandsBaseUrl);

  useEffect(() => {
    let cancelled = false;
    setWorkspaceAuthorized(false);
    setConversationId(localStorage.getItem(storageKey));
    const authorize = async () => {
      try {
        await request.post(`/api/projects/${encodeURIComponent(projectId)}/workspace`, {});
        if (!cancelled) setWorkspaceAuthorized(true);
      } catch {
        if (!cancelled) setWorkspaceAuthorized(false);
      }
    };
    void authorize();
    const refresh = setInterval(authorize, 30 * 60 * 1000);
    return () => {
      cancelled = true;
      clearInterval(refresh);
    };
  }, [projectId, storageKey]);

  // Fetch project report status from engine
  const fetchProjectStatus = useCallback(async () => {
    try {
      const res = await fetch(`${engineBaseUrl}/api/projects/${projectId}/status`);
      if (res.ok) {
        const data = await res.json();
        setProjectStatus(data);
      }
    } catch {
      // engine might still be starting
    }
  }, [engineBaseUrl, projectId]);

  useEffect(() => {
    fetchProjectStatus();
    const interval = setInterval(fetchProjectStatus, 10000);
    return () => clearInterval(interval);
  }, [fetchProjectStatus]);

  // Trigger Quarto compilation
  const handleRenderQuarto = async () => {
    setIsRendering(true);
    setRenderMessage(null);
    try {
      const res = await fetch(`${engineBaseUrl}/api/projects/${projectId}/render`, {
        method: 'POST',
      });
      const data = await res.json();
      if (data.success) {
        setRenderMessage('Quarto report compiled successfully!');
        await fetchProjectStatus();
        setViewMode('quarto');
      } else {
        setRenderMessage(`Compilation failed: ${data.stderr || 'Check report R chunks'}`);
      }
    } catch (err: any) {
      setRenderMessage(`Error triggering render: ${err.message || 'Engine unreachable'}`);
    } finally {
      setIsRendering(false);
    }
  };

  // Launch OpenHands workspace conversation
  const handleLaunchAgent = async () => {
    setIsLaunching(true);
    try {
      const authorization: { ticket: string } = await request.post(
        `/api/projects/${encodeURIComponent(projectId)}/workspace`,
        {},
      );
      const res = await fetch(`${openhandsBaseUrl}/api/omicsbase/conversations`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${authorization.ticket}`,
        },
        credentials: 'include',
        body: JSON.stringify({ ticket: authorization.ticket }),
      });

      if (!res.ok) {
        throw new Error(`Agent server returned HTTP ${res.status}`);
      }

      const data = await res.json();
      const newConvoId = data.conversation_id;
      if (newConvoId) {
        setWorkspaceAuthorized(true);
        setConversationId(newConvoId);
        localStorage.setItem(storageKey, newConvoId);
        setViewMode('agent');
      }
    } catch (err: any) {
      alert(`Could not start Workspace Agent: ${err.message || 'Make sure OpenHands is running'}`);
    } finally {
      setIsLaunching(false);
    }
  };

  const handleResetSession = () => {
    if (
      confirm('Reset workspace session for this report? You can launch a fresh agent session.')
    ) {
      localStorage.removeItem(storageKey);
      setConversationId(null);
    }
  };

  const quartoPreviewUrl = `${engineBaseUrl}/projects/${projectId}/_site/index.html`;

  return (
    <div
      className={cn(
        'flex w-full flex-col overflow-hidden bg-presentation transition-all',
        isFullscreen
          ? 'fixed inset-0 z-[100] h-screen w-screen'
          : 'h-full flex-1 min-h-0'
      )}
    >
      {/* Sleek Unified Top Navigation Bar */}
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

        {/* Center: Sleek Mode Switcher (Iconic + Tooltips on Hover) */}
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

        {/* Right: Actions & Tools (Iconic + Tooltips on Hover) */}
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
                onClick={handleRenderQuarto}
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
                  href={quartoPreviewUrl}
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

          {/* Reset Agent Session */}
          {conversationId && viewMode === 'agent' && (
            <TooltipAnchor
              description="Reset session"
              render={
                <button
                  type="button"
                  onClick={handleResetSession}
                  className="flex h-7 w-7 items-center justify-center rounded-lg text-text-tertiary transition-colors hover:bg-surface-hover hover:text-text-primary"
                  aria-label="Reset session"
                >
                  <RefreshCw className="h-3 w-3" />
                </button>
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

      {/* Main Canvas Body */}
      <div className="relative flex-1 overflow-hidden">
        {viewMode === 'agent' ? (
          conversationId && workspaceAuthorized ? (
            /* Active OpenHands Session Canvas */
            <iframe
              ref={agentFrame}
              onLoad={sendAgentTheme}
              referrerPolicy="origin"
              src={`${openhandsBaseUrl}/conversations/${conversationId}?embedded=true`}
              title="OmicsBase Agent Canvas"
              className="h-full w-full border-none bg-presentation"
              allow="clipboard-read; clipboard-write;"
            />
          ) : (
            /* Onboarding / Session Launcher Card */
            <div className="flex h-full w-full flex-col items-center justify-center p-6 text-center">
              <div className="w-full max-w-xl rounded-2xl border border-border-light bg-surface-primary p-6 text-left shadow-md">
                <div className="flex items-center gap-3">
                  <div className="bg-primary/10 text-primary flex h-11 w-11 shrink-0 items-center justify-center rounded-xl">
                    <Sparkles className="h-6 w-6" />
                  </div>
                  <div>
                    <h2 className="text-base font-semibold text-text-primary">
                      Downstream Omics Workspace Agent
                    </h2>
                    <p className="text-xs text-text-secondary">
                      Multi-file Quarto report authoring, live code steering, and Bioconductor
                      workflow execution.
                    </p>
                  </div>
                </div>

                <div className="mt-5 space-y-3">
                  <div className="rounded-xl border border-border-light bg-surface-secondary/60 p-3 text-xs text-text-secondary">
                    <div className="flex items-center gap-2 font-medium text-text-primary">
                      <Layers className="text-primary h-3.5 w-3.5" />
                      <span>Workspace Target: ./projects/{projectId}</span>
                    </div>
                    <p className="mt-1 leading-relaxed">
                      The Workspace Agent directly inspects, edits, and compiles files in your
                      report directory. You can steer the agent in a note, audit R code in Monaco,
                      and review line-by-line Git diffs.
                    </p>
                  </div>
                </div>

                <div className="mt-5 flex items-center justify-end gap-3">
                  <Button
                    type="button"
                    variant="default"
                    size="sm"
                    onClick={handleLaunchAgent}
                    disabled={isLaunching}
                    className="gap-2 px-4 py-2 text-xs font-medium"
                  >
                    {isLaunching ? (
                      <Spinner className="h-4 w-4 text-white" />
                    ) : (
                      <Play className="h-4 w-4 fill-white" />
                    )}
                    <span>{isLaunching ? 'Initializing Environment...' : 'Open Workspace'}</span>
                  </Button>
                </div>
              </div>
            </div>
          )
        ) : /* Quarto Report Preview Tab */
        projectStatus?.has_site ? (
          <div className="relative h-full w-full">
            <iframe
              src={quartoPreviewUrl}
              title="Quarto Live Report Preview"
              className="h-full w-full border-none bg-white"
            />
          </div>
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center p-6 text-center">
            <div className="w-full max-w-md rounded-2xl border border-border-light bg-surface-primary p-6 shadow-sm">
              <FileCode className="mx-auto h-12 w-12 text-text-tertiary" />
              <h3 className="mt-3 text-base font-medium text-text-primary">
                Quarto Report Not Rendered Yet
              </h3>
              <p className="mt-1 text-xs text-text-secondary">
                The publication website has not been compiled yet for study '{projectName}'.
              </p>
              <div className="mt-5 flex justify-center gap-3">
                <Button
                  type="button"
                  variant="default"
                  size="sm"
                  onClick={handleRenderQuarto}
                  disabled={isRendering}
                  className="gap-2 text-xs"
                >
                  {isRendering ? (
                    <Spinner className="h-3.5 w-3.5 text-white" />
                  ) : (
                    <Play className="h-3.5 w-3.5 fill-white" />
                  )}
                  <span>Compile Report Now</span>
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setViewMode('agent')}
                  className="text-xs"
                >
                  Return to Agent Canvas
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
