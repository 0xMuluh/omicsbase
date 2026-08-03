"use client";

import { useEffect, useRef } from "react";
import { QueryClient } from "@tanstack/react-query";
import { api, Job, Project } from "@/lib/api";

export function useWorkspaceEvents({
  projectId,
  queryClient,
  setAgentActivity,
  setIframeKey,
}: {
  projectId: string;
  queryClient: QueryClient;
  setAgentActivity: (activity: string) => void;
  setIframeKey: React.Dispatch<React.SetStateAction<number>>;
}) {
  const completedJobSignatureRef = useRef("");

  useEffect(() => {
    return api.subscribeProjectEvents(projectId, (event) => {
      queryClient.setQueryData(["project", projectId], (current: Project | undefined) => (
        current
          ? {
              ...current,
              status: event.status,
              agent_state: event.agent_state,
              agent_memory: {
                ...(current.agent_memory || {}),
                summary: event.agent_summary || current.agent_memory?.summary,
                pending_guidance: event.pending_guidance || current.agent_memory?.pending_guidance,
              },
            }
          : current
      ));
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      const currentJobs = queryClient.getQueryData<Job[]>(["jobs", projectId]);
      if (event.jobs.some((eventJob) => !currentJobs?.some((job) => job.id === eventJob.id))) {
        void queryClient.invalidateQueries({ queryKey: ["jobs", projectId] });
      }
      queryClient.setQueryData<Job[]>(["jobs", projectId], (current) => {
        if (!current) return current;
        const updates = new Map(event.jobs.map((job) => [job.id, job]));
        return current.map((job) => {
          const update = updates.get(job.id);
          return update
            ? {
                ...job,
                status: update.status,
                progress: update.progress,
                error: update.error,
                updated_at: update.updated_at || job.updated_at,
              }
            : job;
        });
      });
      if (event.latest_message_id) {
        void queryClient.invalidateQueries({ queryKey: ["projectMessages", projectId] });
      }

      const liveJob = event.jobs.find((job) => job.status === "running" || job.status === "pending");
      const latestStep = [...(liveJob?.progress || [])].reverse().find((step) => step.detail || step.step);
      if (latestStep) {
        setAgentActivity(latestStep.detail || `${latestStep.step} ${latestStep.status}`);
      } else if (event.agent_summary) {
        setAgentActivity(event.agent_summary);
      }

      const completedSignature = event.jobs
        .filter((job) => job.status === "completed" || job.status === "failed")
        .map((job) => `${job.id}:${job.status}:${job.updated_at}`)
        .join("|");
      if (
        completedJobSignatureRef.current
        && completedJobSignatureRef.current !== completedSignature
      ) {
        void queryClient.invalidateQueries({ queryKey: ["fileTree", projectId] });
        void queryClient.invalidateQueries({ queryKey: ["fileContent", projectId] });
        void queryClient.invalidateQueries({ queryKey: ["filePreview", projectId] });
        setIframeKey((value) => value + 1);
      }
      completedJobSignatureRef.current = completedSignature;
    });
  }, [projectId, queryClient, setAgentActivity, setIframeKey]);
}
