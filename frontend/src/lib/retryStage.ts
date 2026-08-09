export type RetryStage = "plan" | "generate" | "render";

const RENDER_STAGE_JOB_TYPES = new Set([
  "render",
  "rendering",
  "review",
  "repair",
  "recipe",
  "edit",
]);

export function retryStageForFailure(
  jobType: string | null | undefined,
  options: { hasPlan: boolean; hasWorkspace: boolean },
): RetryStage {
  const normalized = (jobType || "").trim().toLowerCase();
  if (normalized === "plan" || normalized === "planning") return "plan";
  if (normalized === "generate" || normalized === "generation") return "generate";
  if (RENDER_STAGE_JOB_TYPES.has(normalized)) return "render";

  // Unknown/legacy jobs fall back to the furthest completed prerequisite.
  // Planning is only retried when no plan exists.
  if (!options.hasPlan) return "plan";
  return options.hasWorkspace ? "render" : "generate";
}

export const retryStageCopy: Record<RetryStage, { label: string; detail: string }> = {
  plan: {
    label: "Retry planning",
    detail: "Planning failed, so retry only the planning stage.",
  },
  generate: {
    label: "Resume generation",
    detail: "Generation failed. Keep the approved plan and resume source generation.",
  },
  render: {
    label: "Retry rendering",
    detail: "The generated source is present, so retry rendering and validation only.",
  },
};
