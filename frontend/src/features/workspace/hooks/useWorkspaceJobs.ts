"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Job, type Project, type ProjectEvent } from "@/lib/api";
import { retryStageForFailure } from "@/lib/retryStage";

interface UseWorkspaceJobsOptions {
  projectId: string;
  project?: Project;
  onAgentActivity: (activity: string) => void;
  hasUploadedFiles?: boolean;
}

export function useWorkspaceJobs({
  projectId,
  project,
  onAgentActivity,
  hasUploadedFiles = false,
}: UseWorkspaceJobsOptions) {
  const queryClient = useQueryClient();
  const completedSignatureRef = useRef("");
  const autoBuildTriggeredRef = useRef(false);
  const [workspaceRefreshKey, setWorkspaceRefreshKey] = useState(0);
  const jobsQuery = useQuery({
    queryKey: ["jobs", projectId],
    queryFn: () => api.listJobs(projectId),
  });
  const executionRunsQuery = useQuery({
    queryKey: ["executionRuns", projectId],
    queryFn: () => api.listExecutionRuns(projectId, 10),
    enabled: Boolean(project?.project_dir),
  });

  useEffect(() => {
    return api.subscribeProjectEvents(projectId, (event: ProjectEvent) => {
      queryClient.setQueryData<Project | undefined>(["project", projectId], (current) => (
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
                job_type: update.type || job.job_type,
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
        onAgentActivity(latestStep.detail || latestStep.step + " " + latestStep.status);
      } else if (event.agent_summary) {
        onAgentActivity(event.agent_summary);
      }

      const completedSignature = event.jobs
        .filter((job) => job.status === "completed" || job.status === "failed")
        .map((job) => job.id + ":" + job.status + ":" + (job.updated_at || ""))
        .join("|");
      if (completedSignatureRef.current && completedSignatureRef.current !== completedSignature) {
        void queryClient.invalidateQueries({ queryKey: ["fileTree", projectId] });
        void queryClient.invalidateQueries({ queryKey: ["fileContent", projectId] });
        void queryClient.invalidateQueries({ queryKey: ["filePreview", projectId] });
        void queryClient.invalidateQueries({ queryKey: ["executionRuns", projectId] });
        setWorkspaceRefreshKey((value) => value + 1);
      }
      completedSignatureRef.current = completedSignature;
    });
  }, [onAgentActivity, projectId, queryClient]);

  const jobs = jobsQuery.data;
  const latestFailedJob = useMemo(
    () => [...(jobs || [])]
      .filter((job) => job.status === "failed")
      .sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))[0],
    [jobs],
  );
  const previewProgressSignature = useMemo(
    () => (jobs || [])
      .filter((job) => job.job_type === "render" || job.job_type === "edit")
      .flatMap((job) => job.progress || [])
      .map((entry) => entry.step + ":" + entry.status + ":" + (entry.time || ""))
      .join("|"),
    [jobs],
  );

  const retryStage = retryStageForFailure(latestFailedJob?.job_type, {
    hasWorkspace: Boolean(project?.project_dir),
  });
  const retryMutation = useMutation({
    mutationFn: () => {
      if (retryStage === "generate") return api.startBuild(projectId);
      return api.startRendering(projectId);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["jobs", projectId] });
    },
  });
  const buildMutation = useMutation({
    mutationFn: () => api.startBuild(projectId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["jobs", projectId] });
    },
  });

  useEffect(() => {
    if (autoBuildTriggeredRef.current) return;
    if (!project?.auto_build) return;
    if (project.status !== "created") return;
    if (!hasUploadedFiles) return;
    if (jobsQuery.isLoading || jobsQuery.data === undefined) return;
    const hasBuildJob = jobsQuery.data.some((job) => job.job_type === "generate");
    if (hasBuildJob) return;

    autoBuildTriggeredRef.current = true;
    onAgentActivity("Starting the build...");
    buildMutation.mutate();
  }, [
    buildMutation,
    hasUploadedFiles,
    jobsQuery.data,
    jobsQuery.isLoading,
    onAgentActivity,
    project?.auto_build,
    project?.status,
  ]);

  return {
    buildError: buildMutation.error instanceof Error ? buildMutation.error.message : null,
    buildNow: () => {
      buildMutation.reset();
      buildMutation.mutate();
    },
    buildPending: buildMutation.isPending,
    executionRuns: executionRunsQuery.data,
    jobs,
    latestFailedJob,
    previewProgressSignature,
    retryMutation,
    retryStage,
    workspaceRefreshKey,
  };
}
