import React, { useRef } from 'react';
import useEmbeddedTheme from '../useEmbeddedTheme';

interface AgentCanvasFrameProps {
  openhandsBaseUrl: string;
  conversationId: string;
}

export default function AgentCanvasFrame({
  openhandsBaseUrl,
  conversationId,
}: AgentCanvasFrameProps) {
  const agentFrame = useRef<HTMLIFrameElement>(null);
  const sendAgentTheme = useEmbeddedTheme(agentFrame, openhandsBaseUrl);

  const themeQuery =
    typeof document !== 'undefined' && !document.documentElement.classList.contains('dark')
      ? '&theme=light'
      : '&theme=dark';

  return (
    <iframe
      ref={agentFrame}
      onLoad={sendAgentTheme}
      referrerPolicy="origin"
      src={`${openhandsBaseUrl}/conversations/${conversationId}?embedded=true${themeQuery}`}
      title="OmicsBase Agent Canvas"
      className="h-full w-full border-none bg-presentation"
      allow="clipboard-read; clipboard-write;"
    />
  );
}
