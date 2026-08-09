import { API_BASE, request } from "./client";
import type { ProjectEvent } from "./types/events";

export const eventsApi = {
  getLocks: (projectId: string) => request<{ paths: string[] }>(`/projects/${projectId}/locks`),
  updateLocks: (projectId: string, paths: string[]) =>
    request<{ paths: string[] }>(`/projects/${projectId}/locks`, {
      method: "PUT",
      body: JSON.stringify({ paths }),
    }),
  getTranscriptUrl: (
    projectId: string,
    options?: { format?: "markdown" | "html"; include_tools?: boolean; include_timestamps?: boolean },
  ) => {
    const params = new URLSearchParams();
    params.set("format", options?.format || "markdown");
    if (options?.include_tools === false) params.set("include_tools", "false");
    if (options?.include_timestamps === false) params.set("include_timestamps", "false");
    return `${API_BASE}/projects/${projectId}/transcript?${params.toString()}`;
  },
  subscribeProjectEvents: (
    projectId: string,
    onEvent: (event: ProjectEvent) => void,
    onError?: () => void,
  ) => {
    const source = new EventSource(`${API_BASE}/projects/${projectId}/events`);
    source.addEventListener("workspace", (rawEvent) => {
      onEvent(JSON.parse((rawEvent as MessageEvent).data) as ProjectEvent);
    });
    source.onerror = () => onError?.();
    return () => source.close();
  },
};
