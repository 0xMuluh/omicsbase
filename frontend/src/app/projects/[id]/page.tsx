"use client";

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Loader2 } from "lucide-react";

export default function ProjectRedirectPage() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.id as string;

  const { data: project } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.getProject(projectId),
  });

  useEffect(() => {
    if (!project) return;
    router.replace(`/projects/${projectId}/workspace`);
  }, [project, projectId, router]);

  return (
    <div className="flex h-[calc(100vh-3.5rem)] items-center justify-center p-8">
      <div className="flex flex-col items-center gap-3 text-center">
        <Loader2 className="h-8 w-8 animate-spin text-teal-400" />
        <p className="text-sm text-muted-foreground">Opening project workspace...</p>
      </div>
    </div>
  );
}
