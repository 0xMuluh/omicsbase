"use client";

import { useEffect, useState } from "react";
import type { NoteExecutionArtifact } from "@/lib/api/types/notes";

interface UseExecutionArtifactsOptions {
  artifacts: NoteExecutionArtifact[];
  enabled: boolean;
  fetchArtifact: (artifactId: string) => Promise<Blob>;
}

export function useExecutionArtifacts({ artifacts, enabled, fetchArtifact }: UseExecutionArtifactsOptions) {
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    const createdUrls: string[] = [];
    setUrls({});
    setErrors({});

    if (!enabled) {
      return () => {
        cancelled = true;
      };
    }

    const candidates = artifacts.filter((artifact) => artifact.artifact_type !== "console");
    void Promise.all(
      candidates.map(async (artifact) => {
        try {
          const blob = await fetchArtifact(artifact.id);
          if (cancelled) return;
          const url = URL.createObjectURL(blob);
          createdUrls.push(url);
          setUrls((current) => ({ ...current, [artifact.id]: url }));
        } catch (error) {
          if (cancelled) return;
          setErrors((current) => ({
            ...current,
            [artifact.id]: error instanceof Error ? error.message : "Unavailable",
          }));
        }
      }),
    );

    return () => {
      cancelled = true;
      createdUrls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [artifacts, enabled, fetchArtifact]);

  return { urls, errors };
}
