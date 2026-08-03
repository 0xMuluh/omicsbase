"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";

import { NotesSurface } from "@/components/NotesSurface";

function NotesPageInner() {
  const searchParams = useSearchParams();
  return <NotesSurface initialThreadId={searchParams.get("thread")} />;
}

export default function NotesPage() {
  return (
    <Suspense fallback={null}>
      <NotesPageInner />
    </Suspense>
  );
}
