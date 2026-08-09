import { request } from "./client";
import type { EditReview, EditTransaction } from "./types/edits";

export const editsApi = {
  listEditTransactions: (projectId: string) =>
    request<{ transactions: EditTransaction[] }>(`/projects/${projectId}/edits`),
  getEditTransaction: (projectId: string, transactionId: string) =>
    request<EditTransaction>(`/projects/${projectId}/edits/${transactionId}`),
  revertEditTransaction: (projectId: string, transactionId: string) =>
    request<{ transaction_id: string; status: string; modified_files?: string[] }>(
      `/projects/${projectId}/edits/${transactionId}/revert`,
      { method: "POST" },
    ),
  recoverEditJournals: (projectId: string, transactionId?: string) =>
    request<{ recovered: Record<string, unknown>[] }>(
      `/projects/${projectId}/edits/recover${transactionId ? `?transaction_id=${encodeURIComponent(transactionId)}` : ""}`,
      { method: "POST" },
    ),
  listEditReviews: (projectId: string, limit = 20) =>
    request<{ reviews: EditReview[] }>(`/projects/${projectId}/edit-reviews?limit=${Math.max(1, Math.min(limit, 100))}`),
  getEditReview: (projectId: string, reviewId: string) =>
    request<EditReview>(`/projects/${projectId}/edit-reviews/${reviewId}`),
  approveEditReview: (projectId: string, reviewId: string) =>
    request<{ transaction_id: string; status: string; modified_files?: string[] }>(
      `/projects/${projectId}/edit-reviews/${reviewId}/approve`,
      { method: "POST" },
    ),
  rejectEditReview: (projectId: string, reviewId: string) =>
    request<EditReview>(`/projects/${projectId}/edit-reviews/${reviewId}/reject`, { method: "POST" }),
};
