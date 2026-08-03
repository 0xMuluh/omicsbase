"use client";

import { Suspense } from "react";
import { useParams, useSearchParams } from "next/navigation";

import { NotesSurface } from "@/components/NotesSurface";

function NotesPageInner() {
  const params = useParams();
  const searchParams = useSearchParams();
  const rawWorkspaceId = params?.id;
  const workspaceId = Array.isArray(rawWorkspaceId) ? rawWorkspaceId[0] : rawWorkspaceId;
  return <NotesSurface workspaceId={workspaceId || undefined} initialThreadId={searchParams.get("thread")} />;
}

export default function NotesPage() {
  return (
    <Suspense fallback={null}>
      <NotesPageInner />
    </Suspense>
  );
}
