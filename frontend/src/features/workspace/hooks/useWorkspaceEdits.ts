"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Project } from "@/lib/api";

interface UseWorkspaceEditsOptions {
  projectId: string;
  project?: Project;
  onReverted: () => void;
}

export function useWorkspaceEdits({ projectId, project, onReverted }: UseWorkspaceEditsOptions) {
  const queryClient = useQueryClient();
  const [selectedEditId, setSelectedEditId] = useState<string | null>(null);
  const editHistoryQuery = useQuery({
    queryKey: ["editTransactions", projectId],
    queryFn: () => api.listEditTransactions(projectId),
    enabled: Boolean(project?.project_dir),
  });
  const selectedEditQuery = useQuery({
    queryKey: ["editTransaction", projectId, selectedEditId],
    queryFn: () => api.getEditTransaction(projectId, selectedEditId as string),
    enabled: Boolean(project?.project_dir && selectedEditId),
  });
  const revertMutation = useMutation({
    mutationFn: (transactionId: string) => api.revertEditTransaction(projectId, transactionId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["editTransactions", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["fileTree", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["fileContent", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["filePreview", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      onReverted();
    },
  });

  return {
    editHistory: editHistoryQuery.data,
    revertEditMutation: revertMutation,
    selectedEdit: selectedEditQuery.data,
    selectedEditId,
    setSelectedEditId,
  };
}
