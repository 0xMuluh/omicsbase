import jwt from 'jsonwebtoken';
import { isValidObjectIdString } from '@librechat/data-schemas';
import type { ChatProjectMethods } from '@librechat/data-schemas';
import type { Request, Response } from 'express';

interface WorkspaceRequest extends Request {
  user?: { id: string; _id?: { toString(): string } };
}

export function createWorkspaceHandler(
  deps: Pick<ChatProjectMethods, 'getChatProject'>,
): (req: WorkspaceRequest, res: Response) => Promise<Response> {
  return async (req, res) => {
    const userId = req.user?.id ?? req.user?._id?.toString() ?? '';
    const projectId = req.params.projectId;
    if (!userId) {
      return res.status(401).json({ error: 'Authentication required' });
    }
    if (typeof projectId !== 'string' || !isValidObjectIdString(projectId)) {
      return res.status(400).json({ error: 'Invalid project ID' });
    }
    const secret = process.env.OMICSBASE_AUTH_SECRET;
    if (!secret) {
      return res.status(503).json({ error: 'Workspace authentication is not configured' });
    }
    try {
      if (!(await deps.getChatProject(userId, projectId))) {
        return res.status(404).json({ error: 'Project not found' });
      }
      const options = {
        algorithm: 'HS256' as const,
        issuer: 'librechat',
        audience: 'omicsbase-openhands',
        subject: userId,
      };
      const session = jwt.sign({ purpose: 'session' }, secret, { ...options, expiresIn: '1h' });
      const ticket = jwt.sign({ purpose: 'launch', project_id: projectId }, secret, {
        ...options,
        expiresIn: '2m',
      });
      res.cookie('omicsbase_openhands', session, {
        httpOnly: true,
        secure: req.secure,
        sameSite: 'lax',
        path: '/',
        maxAge: 60 * 60 * 1000,
      });
      res.setHeader('Cache-Control', 'no-store');
      return res.json({ ticket, userId });
    } catch {
      return res.status(500).json({ error: 'Could not authorize workspace' });
    }
  };
}
