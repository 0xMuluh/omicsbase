import jwt from 'jsonwebtoken';
import express from 'express';
import request from 'supertest';
import { createWorkspaceHandler } from './workspace';

const owner = '507f1f77bcf86cd799439011';
const project = '507f1f77bcf86cd799439012';

function appFor(userId: string, exists = true) {
  const app = express();
  app.use((req, _res, next) => {
    Object.assign(req, { user: { id: userId } });
    next();
  });
  const getChatProject = jest.fn(async () => (exists ? { _id: project } : null));
  const handler = createWorkspaceHandler({ getChatProject } as Parameters<
    typeof createWorkspaceHandler
  >[0]);
  app.post('/projects/:projectId/workspace', async (req, res) => {
    await handler(req as Parameters<typeof handler>[0], res);
  });
  return { app, getChatProject };
}

describe('workspace identity bridge', () => {
  const originalSecret = process.env.OMICSBASE_AUTH_SECRET;
  beforeEach(() => {
    process.env.OMICSBASE_AUTH_SECRET = 'workspace-test-secret';
  });
  afterAll(() => {
    if (originalSecret === undefined) delete process.env.OMICSBASE_AUTH_SECRET;
    else process.env.OMICSBASE_AUTH_SECRET = originalSecret;
  });

  it('signs server-authenticated identity and project ownership, ignoring client identity', async () => {
    const { app, getChatProject } = appFor(owner);
    const response = await request(app)
      .post(`/projects/${project}/workspace`)
      .send({ userId: 'attacker' });
    expect(response.status).toBe(200);
    expect(getChatProject).toHaveBeenCalledWith(owner, project);
    expect(
      jwt.verify(response.body.ticket, 'workspace-test-secret', {
        audience: 'omicsbase-openhands',
        issuer: 'librechat',
      }),
    ).toMatchObject({ sub: owner, project_id: project, purpose: 'launch' });
    expect(response.headers['set-cookie'][0]).toMatch(/HttpOnly/);
    expect(response.headers['set-cookie'][0]).toMatch(/SameSite=Lax/);
    expect(response.headers['cache-control']).toBe('no-store');
  });

  it('does not issue credentials for another user’s project', async () => {
    const { app } = appFor(owner, false);
    const response = await request(app).post(`/projects/${project}/workspace`);
    expect(response.status).toBe(404);
    expect(response.headers['set-cookie']).toBeUndefined();
  });

  it('requires an authenticated user', async () => {
    const { app } = appFor('');
    expect((await request(app).post(`/projects/${project}/workspace`)).status).toBe(401);
  });
});
