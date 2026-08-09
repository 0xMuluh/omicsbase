
import { API_BASE } from "./client";
import type { AgentStreamEvent, FileAttachment } from "./types/messages";
import type { NoteTurnStreamEvent } from "./types/notes";

type DurableStreamEvent = {
  type?: string;
  sequence?: number;
  run?: { status?: string };
};

export async function streamNdjsonWithReplay<T extends DurableStreamEvent>(
  url: string,
  body: Record<string, unknown>,
  headers: HeadersInit,
  onEvent: (event: T) => void,
  signal?: AbortSignal,
): Promise<void> {
  const suppliedKey = body.idempotency_key;
  const idempotencyKey =
    typeof suppliedKey === "string" && suppliedKey.trim()
      ? suppliedKey
      : Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
  const requestBody = { ...body, idempotency_key: idempotencyKey };
  const maxAttempts = 3;
  let lastSequence = -1;

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    if (signal?.aborted) throw new Error("The agent stream was aborted.");
    let response: Response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers,
        body: JSON.stringify(requestBody),
        signal,
      });
    } catch (error) {
      if (signal?.aborted || attempt === maxAttempts - 1) throw error;
      await new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1)));
      continue;
    }

    if (!response.ok) {
      const detail = await response.text().catch(() => response.statusText);
      const retryable = response.status === 429 || response.status >= 500;
      if (!retryable || attempt === maxAttempts - 1) {
        throw new Error("Agent stream error " + response.status + ": " + detail);
      }
      await new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1)));
      continue;
    }
    if (!response.body) throw new Error("Agent stream did not return a response body.");

    let terminal = false;
    const emitLine = (line: string) => {
      const event = JSON.parse(line) as T;
      const sequence = Number(event.sequence);
      if (Number.isFinite(sequence)) {
        if (sequence <= lastSequence) return;
        lastSequence = sequence;
      }
      onEvent(event);
      if (
        event.type === "final" ||
        event.type === "cancelled" ||
        event.type === "paused" ||
        (event.type === "run" &&
          ["completed", "failed", "cancelled"].includes(event.run?.status || ""))
      ) {
        terminal = true;
      }
    };

    try {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (line.trim()) emitLine(line);
        }
        if (done) break;
      }
      if (buffer.trim()) emitLine(buffer);
    } catch (error) {
      if (signal?.aborted || attempt === maxAttempts - 1) throw error;
      await new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1)));
      continue;
    }

    if (terminal) return;
    if (attempt < maxAttempts - 1) {
      await new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1)));
    }
  }

  throw new Error("Agent stream ended before the durable run completed.");
}

export async function streamAgentMessage(
  projectId: string,
  data: {
    message: string;
    selected_file?: string | null;
    selected_content?: string | null;
    selected_content_dirty?: boolean;
    preview_path?: string | null;
    chat_mode?: "build" | "discuss";
    attachments?: FileAttachment[];
    idempotency_key?: string;
  },
  onEvent: (event: AgentStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamNdjsonWithReplay<AgentStreamEvent>(
    API_BASE + "/projects/" + projectId + "/agent/stream",
    data,
    { "Content-Type": "application/json" },
    onEvent,
    signal,
  );
}

export async function streamNoteThreadTurn(
  threadId: string,
  data: { message: string; auto_execute?: boolean; idempotency_key?: string; attachments?: FileAttachment[] },
  onEvent: (event: NoteTurnStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamNdjsonWithReplay<NoteTurnStreamEvent>(
    API_BASE + "/notes/" + threadId + "/turn",
    data,
    {
      "Content-Type": "application/json",
      "X-Tenant-ID": "default_tenant",
      "X-User-ID": "default_user",
    },
    onEvent,
    signal,
  );
}
