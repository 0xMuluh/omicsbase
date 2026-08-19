export type RetryStage = "generate" | "render";

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
  options: { hasWorkspace: boolean },
): RetryStage {
  const normalized = (jobType || "").trim().toLowerCase();
  if (normalized === "generate" || normalized === "generation" || normalized === "plan" || normalized === "planning") {
    return "generate";
  }
  if (RENDER_STAGE_JOB_TYPES.has(normalized)) return "render";
  return options.hasWorkspace ? "render" : "generate";
}

export const retryStageCopy: Record<RetryStage, { label: string; detail: string }> = {
  generate: {
    label: "Retry build",
    detail: "The build failed before source or output was ready.",
  },
  render: {
    label: "Retry rendering",
    detail: "Source is present, so retry rendering and validation only.",
  },
};
