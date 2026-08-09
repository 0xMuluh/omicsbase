import { request } from "./client";
import type { ExecutionProvenanceSummary } from "./types/executions";

export const executionsApi = {
  listExecutionRuns: (projectId: string, limit = 20) =>
    request<{ runs: ExecutionProvenanceSummary[] }>(`/projects/${projectId}/execution-runs?limit=${Math.max(1, Math.min(limit, 50))}`),
  getExecutionRun: (projectId: string, runId: string) =>
    request<Record<string, unknown>>(`/projects/${projectId}/execution-runs/${runId}`),
};
