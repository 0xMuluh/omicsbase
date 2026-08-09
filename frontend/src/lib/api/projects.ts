import { API_BASE, request } from "./client";
import type { AnalysisPlan, ClarificationAnswer, ClarificationRequest, Job, Project, ProjectFile, WorkspaceResult } from "./types/projects";
import type { AssistantMessage, ChatMessage, ProjectMessage } from "./types/messages";

export const projectsApi = {
  listProjects: () => request<Project[]>("/projects/"),
  createProject: (data: { name?: string; question?: string; notes?: string; custom_plan_text?: string; auto_build?: boolean }) =>
    request<Project>("/projects/", { method: "POST", body: JSON.stringify(data) }),
  getProject: (id: string) => request<Project>(`/projects/${id}`),
  deleteProject: (id: string) => request<void>(`/projects/${id}`, { method: "DELETE" }),
  updateProject: (id: string, data: { name?: string; notes?: string; status?: string }) =>
    request<Project>(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(data) }),

  uploadFile: async (projectId: string, file: File, role?: string) => {
    const formData = new FormData();
    formData.append("file", file);
    const url = `${API_BASE}/projects/${projectId}/files?file_role=${role || "auto"}`;
    const res = await fetch(url, { method: "POST", body: formData });
    if (!res.ok) {
      const detail = await res.text().catch(() => res.statusText);
      throw new Error(`Upload failed (${res.status}): ${detail}`);
    }
    return res.json() as Promise<ProjectFile>;
  },
  listFiles: (projectId: string) => request<ProjectFile[]>(`/projects/${projectId}/files`),
  getNoteResults: (projectId: string) =>
    request<WorkspaceResult[]>(`/projects/${projectId}/note-results`),

  startPlanning: (projectId: string) =>
    request<Job>(`/projects/${projectId}/plan`, { method: "POST" }),
  getClarifications: (projectId: string) =>
    request<ClarificationRequest | null>(`/projects/${projectId}/clarifications`),
  submitClarifications: (projectId: string, answers: ClarificationAnswer[]) =>
    request<Job>(`/projects/${projectId}/clarifications`, {
      method: "POST",
      body: JSON.stringify({ answers }),
    }),
  approvePlan: (projectId: string, plan: AnalysisPlan) =>
    request<{ status: string }>(`/projects/${projectId}/approve`, {
      method: "POST",
      body: JSON.stringify({ project_id: projectId, plan }),
    }),
  startGeneration: (projectId: string) =>
    request<Job>(`/projects/${projectId}/generate`, { method: "POST" }),
  startRendering: (projectId: string) =>
    request<Job>(`/projects/${projectId}/run`, { method: "POST" }),
  editProject: (projectId: string, instruction: string) =>
    request<Job>(`/projects/${projectId}/edit`, {
      method: "POST",
      body: JSON.stringify({ instruction }),
    }),
  assistantMessage: (projectId: string, message: string, history: ChatMessage[] = []) =>
    request<AssistantMessage>(`/projects/${projectId}/assistant`, {
      method: "POST",
      body: JSON.stringify({
        message,
        history: history.map((item) => ({ role: item.role, content: item.content })),
      }),
    }),
  listMessages: (projectId: string) =>
    request<ProjectMessage[]>(`/projects/${projectId}/messages`),
};
