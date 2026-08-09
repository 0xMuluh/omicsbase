"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";
import { friendlyToolLabel } from "@/lib/toolLabels";
import type { ActionEvent } from "@/components/AgentActionCard";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { AgentStreamEvent, ChatMessage, FileAttachment, PendingQuestion, ProjectMessage } from "@/lib/api/types/messages";
import type { Project } from "@/lib/api/types/projects";

function projectMessageToChatMessage(message: ProjectMessage): ChatMessage {
  return {
    id: message.id,
    role: message.role,
    kind: message.kind,
    content: message.content,
    time: message.created_at,
    metadata: message.metadata,
    attachments: Array.isArray(message.metadata?.attachments) ? message.metadata.attachments as FileAttachment[] : [],
    cell_id: message.cell_id,
    cell_type: message.cell_type,
    cell_revision: message.cell_revision,
    execution_id: message.execution_id,
  };
}

interface UseWorkspaceAgentOptions {
  projectId: string;
  project?: Project;
  activeTab: string | null;
  activeDraft?: string;
  isDirty: boolean;
  previewReportPath: string;
}

export function useWorkspaceAgent({
  projectId,
  project,
  activeTab,
  activeDraft,
  isDirty,
  previewReportPath,
}: UseWorkspaceAgentOptions) {
  const queryClient = useQueryClient();
  const projectMessagesQuery = useQuery({
    queryKey: ["projectMessages", projectId],
    queryFn: () => api.listMessages(projectId),
  });
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [assistantPending, setAssistantPending] = useState(false);
  const [pendingQuestion, setPendingQuestion] = useState<PendingQuestion | null>(null);
  const [agentActivity, setAgentActivity] = useState("Understanding the workspace...");
  const [chatMode, setChatMode] = useState<"build" | "discuss">("build");
  const [actionEvents, setActionEvents] = useState<ActionEvent[]>([]);
  const [quickActions, setQuickActions] = useState<{ type: string; label: string; prompt: string }[]>([]);

  useEffect(() => {
    const pending = project?.agent_memory?.pending_question as PendingQuestion | undefined;
    if (pending && !assistantPending) {
      setPendingQuestion(pending);
    }
  }, [assistantPending, project?.agent_memory?.pending_question]);

  const handleSendPrompt = async (
    event?: FormEvent,
    override?: { message?: string; mode?: "build" | "discuss"; attachments?: FileAttachment[]; files?: File[] },
  ) => {
    event?.preventDefault();
    const rawMessage = (override?.message || "").trim();
    const mode = override?.mode || chatMode;
    const attachments: FileAttachment[] = override?.attachments ? [...override.attachments] : [];
    if (override?.files?.length) {
      for (const file of override.files) {
        try {
          const uploaded = await api.uploadFile(projectId, file, "auto");
          attachments.push({
            id: uploaded.id,
            name: uploaded.original_name || file.name,
            format: uploaded.detected_format,
            mime_type: file.type || null,
            size_bytes: file.size,
            source: "project",
          });
        } catch (error) {
          console.error("Failed to upload attached file:", file.name, error);
        }
      }
    }

    const message = rawMessage || (attachments.length ? "I attached files for the analysis." : "");
    if (!message || assistantPending) return;

    setPendingQuestion(null);
    const optimisticId = "local-" + Date.now();
    const userMessage: ChatMessage = {
      id: optimisticId,
      role: "user",
      content: message,
      time: new Date().toISOString(),
      attachments,
    };
    setChatMessages((prev) => [...prev, userMessage]);
    setAssistantPending(true);
    setQuickActions([]);
    setActionEvents([]);
    setAgentActivity(mode === "discuss" ? "Discussing the analysis..." : "Understanding the workspace...");

    try {
      await api.streamAgentMessage(
        projectId,
        {
          message,
          selected_file: activeTab,
          selected_content: isDirty ? activeDraft ?? null : null,
          selected_content_dirty: isDirty,
          preview_path: previewReportPath,
          chat_mode: mode,
          attachments,
        },
        (streamEvent: AgentStreamEvent) => {
          if (streamEvent.type === "question" && streamEvent.question) {
            setPendingQuestion(streamEvent.question);
          }
          if (streamEvent.type === "final" && streamEvent.awaiting_answer) {
            setPendingQuestion(streamEvent.awaiting_answer);
          }
          if (streamEvent.type === "title_update" && typeof streamEvent.name === "string") {
            const updatedTitle = streamEvent.name;
            queryClient.setQueryData<Project | undefined>(["project", projectId], (old) =>
              old?.name_source === "user"
                ? old
                : old
                  ? { ...old, name: updatedTitle, name_source: streamEvent.name_source || "auto" }
                  : old,
            );
            queryClient.setQueryData<Project[] | undefined>(["projects"], (old) =>
              Array.isArray(old)
                ? old.map((projectItem) =>
                    projectItem.id !== projectId || projectItem.name_source === "user"
                      ? projectItem
                      : { ...projectItem, name: updatedTitle, name_source: streamEvent.name_source || "auto" },
                  )
                : old,
            );
          }
          if (streamEvent.type === "status" && typeof streamEvent.message === "string") {
            setAgentActivity(streamEvent.message);
          }
          if (streamEvent.type === "action_event" && streamEvent.event) {
            const next = streamEvent.event as ActionEvent;
            setActionEvents((prev) => {
              const without = prev.filter((item) => item.id !== next.id && !item.id.endsWith("-start"));
              return [...without, next];
            });
          }
          if (streamEvent.type === "tool_started") {
            setAgentActivity(streamEvent.reason || friendlyToolLabel(streamEvent.tool) || "Inspecting the workspace...");
          }
          if (streamEvent.type === "tool_completed") {
            setAgentActivity(streamEvent.summary || "Workspace inspection completed");
          }
          if ((streamEvent.type === "token" || streamEvent.type === "token_chunk") && typeof streamEvent.token === "string") {
            setChatMessages((prev) => {
              const existing = prev.find((item) => item.id === "streaming-assistant");
              if (existing) {
                return prev.map((item) =>
                  item.id === "streaming-assistant"
                    ? { ...item, content: item.content + streamEvent.token }
                    : item,
                );
              }
              return [
                ...prev,
                {
                  id: "streaming-assistant",
                  role: "assistant",
                  content: streamEvent.token as string,
                  time: new Date().toISOString(),
                },
              ];
            });
          }
          if (
            streamEvent.type === "message"
            && typeof streamEvent.message === "object"
            && streamEvent.message
          ) {
            const persisted = projectMessageToChatMessage(streamEvent.message);
            setChatMessages((prev) => [
              ...prev.filter((item) => item.id !== optimisticId),
              persisted,
            ]);
          }
          if (
            (streamEvent.type === "final" || streamEvent.type === "action_queued")
            && typeof streamEvent.message === "string"
          ) {
            if (streamEvent.quick_actions?.length) {
              setQuickActions(streamEvent.quick_actions);
            }
            setChatMessages((prev) => [
              ...prev.filter((item) => item.id !== "streaming-assistant"),
              {
                id: streamEvent.message_id,
                role: "assistant",
                content: streamEvent.message as string,
                time: new Date().toISOString(),
                metadata: streamEvent.job_id ? { job_id: streamEvent.job_id } : null,
              },
            ]);
          }
        },
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["projectMessages", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["project", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["jobs", projectId] }),
      ]);
      setChatMessages([]);
    } catch (error) {
      setChatMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: error instanceof Error ? error.message : "Could not reach the assistant.",
          time: new Date().toISOString(),
        },
      ]);
    } finally {
      setAssistantPending(false);
      setAgentActivity("Understanding the workspace...");
    }
  };

  const askAgent = (prompt: string, mode: "build" | "discuss" = "build") => {
    setChatMode(mode);
    void handleSendPrompt(undefined, { message: prompt, mode });
  };

  const answerQuestion = (answer: string) => {
    setPendingQuestion(null);
    void handleSendPrompt(undefined, { message: answer, mode: chatMode });
  };

  const handleAddFiles = async (files: File[]) => {
    const failures: string[] = [];
    const attachments: FileAttachment[] = [];
    for (const file of files) {
      try {
        const uploaded = await api.uploadFile(projectId, file, "auto");
        attachments.push({
          id: uploaded.id,
          name: uploaded.original_name || file.name,
          format: uploaded.detected_format,
          mime_type: file.type || null,
          size_bytes: file.size,
          source: "project",
        });
      } catch {
        failures.push(file.name);
      }
    }
    const failedText = failures.length
      ? " The following files could not be uploaded: " + failures.join(", ") + "."
      : "";
    void handleSendPrompt(undefined, {
      message: attachments.length
        ? "I attached " + (attachments.length === 1 ? "a file" : "files") + " for the analysis." + failedText
        : "I could not attach the selected files." + failedText,
      mode: chatMode,
      attachments,
    });
    void queryClient.invalidateQueries({ queryKey: ["projects"] });
  };

  const displayChatMessages = useMemo(() => {
    const durable = (projectMessagesQuery.data || [])
      .filter((message) => message.role === "user" || message.role === "assistant")
      .map(projectMessageToChatMessage);
    const durableIds = new Set(durable.map((message) => message.id).filter(Boolean));
    return [
      ...durable,
      ...chatMessages.filter((message) => !message.id || !durableIds.has(message.id)),
    ];
  }, [chatMessages, projectMessagesQuery.data]);

  return {
    actionEvents,
    agentActivity,
    answerQuestion,
    askAgent,
    assistantPending,
    chatMode,
    chatMessages,
    displayChatMessages,
    handleAddFiles,
    handleSendPrompt,
    pendingQuestion,
    quickActions,
    setAgentActivity,
    setChatMode,
  };
}
