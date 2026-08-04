"use client";

import { useParams } from "next/navigation";
import PlanReviewPanel from "@/components/PlanReviewPanel";

export default function PlanningPage() {
  const params = useParams();
  return <PlanReviewPanel projectId={params.id as string} />;
}
