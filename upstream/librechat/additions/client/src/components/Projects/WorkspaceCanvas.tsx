import React, { useState, useEffect, useCallback } from 'react';
import { useMediaQuery } from '@librechat/client';
import { request } from 'librechat-data-provider';
import { useAuthContext } from '~/hooks/AuthContext';
import { cn } from '~/utils';
import {
  CanvasViewMode,
  AgentEngineKey,
  ProjectStatusResponse,
  ProjectSessionOption,
  WorkspaceCanvasProps,
} from './Workspace/types';
import WorkspaceHeader from './Workspace/WorkspaceHeader';
import AgentCanvasFrame from './Workspace/AgentCanvasFrame';
import AgentLauncherCard from './Workspace/AgentLauncherCard';
import ReportPreviewFrame from './Workspace/ReportPreviewFrame';

export default function WorkspaceCanvas({
  project,
  onNavigateNotes,
  onEditProject,
}: WorkspaceCanvasProps) {
  const isSmallScreen = useMediaQuery('(max-width: 768px)');
  const projectId = project._id || 'default';
  const projectName = project.name || projectId;
  const { user } = useAuthContext();
  const storageKey = `omicsbase_workspace_session_v2_${user?.id}_${projectId}`;

  const [workspaceAuthorized, setWorkspaceAuthorized] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(() => {
    return localStorage.getItem(storageKey);
  });
  const [projectConversations, setProjectConversations] = useState<ProjectSessionOption[]>([]);
  const [selectedEngine, setSelectedEngine] = useState<AgentEngineKey>(() => {
    return (localStorage.getItem(`${storageKey}_engine`) as AgentEngineKey) || 'openhands';
  });
  const [viewMode, setViewMode] = useState<CanvasViewMode>('agent');
  const [isLaunching, setIsLaunching] = useState(false);
  const [isRendering, setIsRendering] = useState(false);
  const [projectStatus, setProjectStatus] = useState<ProjectStatusResponse | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [renderMessage, setRenderMessage] = useState<string | null>(null);
  const [previewKey, setPreviewKey] = useState(0);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isFullscreen) {
        setIsFullscreen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isFullscreen]);

  const [engineBaseUrl, setEngineBaseUrl] = useState(() =>
    typeof window !== 'undefined' && ['localhost', '127.0.0.1'].includes(window.location.hostname)
      ? 'http://localhost:8001'
      : '/omics-engine'
  );
  const [openhandsBaseUrl, setOpenhandsBaseUrl] = useState(() =>
    typeof window !== 'undefined' && ['localhost', '127.0.0.1'].includes(window.location.hostname)
      ? 'http://localhost:3001'
      : ''
  );

  useEffect(() => {
    let cancelled = false;
    setWorkspaceAuthorized(false);
    setConversationId(localStorage.getItem(storageKey));
    const authorize = async () => {
      try {
        const authorization: { ticket: string; openhandsBaseUrl: string; engineBaseUrl: string } = await request.post(
          `/api/projects/${encodeURIComponent(projectId)}/workspace`, {},
        );
        if (!cancelled) {
          if (authorization.openhandsBaseUrl) {
            setOpenhandsBaseUrl(authorization.openhandsBaseUrl);
          }
          if (authorization.engineBaseUrl) {
            setEngineBaseUrl(authorization.engineBaseUrl);
          }
        }
        const saved = localStorage.getItem(storageKey);
        const currentTheme = typeof document !== 'undefined' && !document.documentElement.classList.contains('dark') ? 'light' : 'dark';
        const response = await fetch(`${authorization.openhandsBaseUrl}/api/omicsbase/session`, {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${authorization.ticket}` },
          body: JSON.stringify({ ticket: authorization.ticket, conversation_id: saved, theme: currentTheme }),
        });
        if (!response.ok) throw new Error('Workspace authorization failed');
        const sessionData = await response.json();
        if (!cancelled) {
          if (Array.isArray(sessionData.conversations)) {
            setProjectConversations(sessionData.conversations);
          }
          if (sessionData.conversation_id) {
            setConversationId(sessionData.conversation_id);
            localStorage.setItem(storageKey, sessionData.conversation_id);
          } else if (sessionData && sessionData.conversation_valid === false && saved) {
            localStorage.removeItem(storageKey);
            setConversationId(null);
          }
          setWorkspaceAuthorized(true);
        }
      } catch {
        if (!cancelled) setWorkspaceAuthorized(false);
      }
    };
    void authorize();
    window.addEventListener('focus', authorize);
    const refresh = setInterval(authorize, 2 * 60 * 1000);
    return () => {
      cancelled = true;
      clearInterval(refresh);
      window.removeEventListener('focus', authorize);
    };
  }, [projectId, storageKey]);

  // Fetch project report status from gateway or engine
  const fetchProjectStatus = useCallback(async () => {
    const gatewayBase =
      openhandsBaseUrl ||
      (typeof window !== 'undefined' && ['localhost', '127.0.0.1'].includes(window.location.hostname)
        ? 'http://localhost:3001'
        : '');
    if (gatewayBase) {
      try {
        const res = await fetch(`${gatewayBase}/api/omicsbase/report/${projectId}/status`, { credentials: 'include' });
        if (res.ok) {
          const data = await res.json();
          setProjectStatus(data);
          return;
        }
      } catch {
        // fallback to engine
      }
    }
    try {
      const res = await fetch(`${engineBaseUrl}/api/projects/${projectId}/status`, { credentials: 'include' });
      if (res.ok) {
        const data = await res.json();
        setProjectStatus(data);
      }
    } catch {
      // engine might still be starting
    }
  }, [openhandsBaseUrl, engineBaseUrl, projectId]);

  useEffect(() => {
    fetchProjectStatus();
    const interval = setInterval(fetchProjectStatus, 10000);
    return () => clearInterval(interval);
  }, [fetchProjectStatus]);

  useEffect(() => {
    if (viewMode === 'quarto') {
      fetchProjectStatus();
    }
  }, [viewMode, fetchProjectStatus]);

  // Trigger Quarto compilation
  const handleRenderQuarto = async () => {
    setIsRendering(true);
    setRenderMessage(null);
    try {
      const res = await fetch(`${engineBaseUrl}/api/projects/${projectId}/render`, {
        method: 'POST',
        credentials: 'include',
      });
      const data = await res.json();
      if (data.success) {
        setRenderMessage('Quarto report compiled successfully!');
        await fetchProjectStatus();
        setPreviewKey((k) => k + 1);
        setViewMode('quarto');
      } else {
        setRenderMessage(`Compilation failed: ${data.stderr || 'Check report R chunks'}`);
      }
    } catch (err) {
      setRenderMessage(`Error triggering render: ${err instanceof Error ? err.message : 'Engine unreachable'}`);
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
        body: JSON.stringify({
          ticket: authorization.ticket,
          agent_engine: selectedEngine,
        }),
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
        localStorage.setItem(`${storageKey}_engine`, selectedEngine);
        setViewMode('agent');
        setProjectConversations((prev) => [
          {
            conversation_id: newConvoId,
            title: `Conversation ${newConvoId.slice(0, 5)}`,
            created_at: Date.now() / 1000,
            event_count: 0,
            agent_engine: selectedEngine,
          },
          ...prev.filter((c) => c.conversation_id !== newConvoId),
        ]);
      }
    } catch (err) {
      alert(`Could not start Workspace Agent: ${err instanceof Error ? err.message : 'Make sure OpenHands is running'}`);
    } finally {
      setIsLaunching(false);
    }
  };

  const handleResetSession = () => {
    if (
      confirm('Reset workspace session for this report? You can launch a fresh agent session or switch agent engines.')
    ) {
      localStorage.removeItem(storageKey);
      localStorage.removeItem(`${storageKey}_engine`);
      setConversationId(null);
    }
  };

  const handleSelectAndSwitchEngine = async (engineKey: AgentEngineKey) => {
    setSelectedEngine(engineKey);
    localStorage.setItem(`${storageKey}_engine`, engineKey);
    localStorage.removeItem(storageKey);
    setConversationId(null);

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
        body: JSON.stringify({
          ticket: authorization.ticket,
          agent_engine: engineKey,
        }),
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
    } catch (err) {
      alert(`Could not start ${engineKey} agent: ${err instanceof Error ? err.message : 'Make sure OpenHands is running'}`);
    } finally {
      setIsLaunching(false);
    }
  };

  const effectiveOpenhandsUrl =
    openhandsBaseUrl ||
    (typeof window !== 'undefined' && ['localhost', '127.0.0.1'].includes(window.location.hostname)
      ? 'http://localhost:3001'
      : '');
  const quartoSiteUrl = `${effectiveOpenhandsUrl}/api/omicsbase/report/${projectId}/site/index.html`;

  return (
    <div
      className={cn(
        'flex w-full flex-col overflow-hidden bg-presentation transition-all',
        isFullscreen
          ? 'fixed inset-0 z-[100] h-screen w-screen'
          : 'h-full flex-1 min-h-0'
      )}
    >
      <WorkspaceHeader
        project={project}
        projectId={projectId}
        projectName={projectName}
        viewMode={viewMode}
        setViewMode={setViewMode}
        conversationId={conversationId}
        projectConversations={projectConversations}
        selectedEngine={selectedEngine}
        isSmallScreen={isSmallScreen}
        isFullscreen={isFullscreen}
        setIsFullscreen={setIsFullscreen}
        projectStatus={projectStatus}
        isRendering={isRendering}
        isLaunching={isLaunching}
        quartoSiteUrl={quartoSiteUrl}
        renderMessage={renderMessage}
        setRenderMessage={setRenderMessage}
        onNavigateNotes={onNavigateNotes}
        onEditProject={onEditProject}
        onRenderQuarto={handleRenderQuarto}
        onRefreshReport={() => {
          fetchProjectStatus();
          setPreviewKey((k) => k + 1);
        }}
        onResetSession={handleResetSession}
        onSelectConversation={(convoId) => {
          setConversationId(convoId);
          localStorage.setItem(storageKey, convoId);
        }}
        onLaunchNewThread={handleLaunchAgent}
        onSelectEngine={handleSelectAndSwitchEngine}
      />

      {/* Main Canvas Body */}
      <div className="relative flex-1 overflow-hidden">
        {viewMode === 'agent' ? (
          conversationId && workspaceAuthorized ? (
            <AgentCanvasFrame
              openhandsBaseUrl={openhandsBaseUrl}
              conversationId={conversationId}
            />
          ) : (
            <AgentLauncherCard
              projectId={projectId}
              selectedEngine={selectedEngine}
              setSelectedEngine={setSelectedEngine}
              onLaunchAgent={handleLaunchAgent}
              isLaunching={isLaunching}
              workspaceAuthorized={workspaceAuthorized}
            />
          )
        ) : (
          <ReportPreviewFrame
            hasSite={!!projectStatus?.has_site}
            quartoSiteUrl={quartoSiteUrl}
            previewKey={previewKey}
            projectName={projectName}
            isRendering={isRendering}
            onRenderQuarto={handleRenderQuarto}
            onReturnToAgent={() => setViewMode('agent')}
          />
        )}
      </div>
    </div>
  );
}
