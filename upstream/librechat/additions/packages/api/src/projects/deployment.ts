export interface WorkspaceURLs {
  openhandsBaseUrl: string;
  engineBaseUrl: string;
  secureCookies: boolean;
}

export function workspaceURLs(): WorkspaceURLs {
  const app = new URL(process.env.DOMAIN_CLIENT || 'http://localhost:3080');
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(app.hostname);
  const configured = process.env.OMICSBASE_OPENHANDS_PUBLIC_URL;
  if (!configured && !local) {
    throw new Error('OMICSBASE_OPENHANDS_PUBLIC_URL is required for public deployment');
  }
  const openhands = new URL(configured || 'http://localhost:3001');
  if (!['http:', 'https:'].includes(openhands.protocol) || openhands.username || openhands.password || openhands.pathname !== '/' || openhands.search || openhands.hash) {
    throw new Error('OpenHands URL must be an HTTP(S) origin');
  }
  if (app.protocol === 'https:' && openhands.protocol !== 'https:') {
    throw new Error('OpenHands must use HTTPS when LibreChat uses HTTPS');
  }
  return {
    openhandsBaseUrl: openhands.origin,
    engineBaseUrl: local ? 'http://localhost:8001' : '/omics-engine',
    secureCookies: app.protocol === 'https:',
  };
}
