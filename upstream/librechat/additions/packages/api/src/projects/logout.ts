import jwt from 'jsonwebtoken';

/** Revoke all workspace sessions for this user, including the other host's cookie. */
export async function revokeWorkspaceSessions(userId: string): Promise<void> {
  const secret = process.env.OMICSBASE_AUTH_SECRET;
  if (!secret || !userId) return;
  const token = jwt.sign({ purpose: 'logout' }, secret, {
    algorithm: 'HS256', issuer: 'librechat', audience: 'omicsbase-openhands',
    subject: userId, expiresIn: '30s',
  });
  const base = process.env.OMICSBASE_OPENHANDS_INTERNAL_URL || 'http://openhands:3000';
  const response = await fetch(`${base.replace(/\/$/, '')}/api/omicsbase/revoke`, {
    method: 'POST', headers: { Authorization: `Bearer ${token}` },
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error('Workspace session revocation failed');
}
