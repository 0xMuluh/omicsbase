"use client";

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";

/** Legacy route — all build/workspace activity lives at /workspace. */
export default function BuildRedirectPage() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.id as string;

  useEffect(() => {
    router.replace(`/projects/${projectId}/workspace`);
  }, [projectId, router]);

  return null;
}
