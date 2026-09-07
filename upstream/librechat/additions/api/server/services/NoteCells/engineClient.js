const ENGINE_URL = (
  process.env.NOTEKERNEL_URL ||
  process.env.ENGINE_URL ||
  'http://engine:8001'
).replace(/\/$/, '');
const INTERNAL_SECRET = process.env.OMICSBASE_AUTH_SECRET || process.env.JWT_SECRET || '';

/**
 * Call NoteKernel HTTP execute (server-side only).
 */
async function executeOnEngine({ code, threadId, executionId, timeoutSeconds = 180 }) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), (timeoutSeconds + 30) * 1000);

  try {
    const headers = { 'Content-Type': 'application/json' };
    if (INTERNAL_SECRET) {
      headers['X-Internal-Secret'] = INTERNAL_SECRET;
    }

    const response = await fetch(`${ENGINE_URL}/api/execute`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        code,
        thread_id: threadId,
        execution_id: executionId,
        timeout_seconds: timeoutSeconds,
      }),
      signal: controller.signal,
    });

    if (!response.ok) {
      const text = await response.text().catch(() => '');
      throw new Error(`Engine HTTP ${response.status}: ${text.slice(0, 200)}`);
    }

    return await response.json();
  } catch (err) {
    if (err?.name === 'AbortError') {
      const timeoutErr = new Error('Cell execution timed out');
      timeoutErr.code = 'TIMED_OUT';
      throw timeoutErr;
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Fetch artifact bytes from engine static /projects mount.
 */
async function fetchArtifactFromEngine(threadId, relativePath) {
  const url = `${ENGINE_URL}/projects/${encodeURIComponent(threadId)}/${relativePath
    .split('/')
    .map(encodeURIComponent)
    .join('/')}`;
  const response = await fetch(url);
  if (!response.ok) {
    const err = new Error(`Artifact fetch failed (${response.status})`);
    err.status = response.status;
    throw err;
  }
  const buffer = Buffer.from(await response.arrayBuffer());
  const contentType = response.headers.get('content-type') || 'application/octet-stream';
  return { buffer, contentType };
}

module.exports = {
  ENGINE_URL,
  INTERNAL_SECRET,
  executeOnEngine,
  fetchArtifactFromEngine,
};
