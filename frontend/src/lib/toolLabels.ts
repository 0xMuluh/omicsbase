// User-facing labels for internal agent tool names. Raw identifiers like
// "run_r_cell" must never surface in the UI; this map mirrors the backend's
// friendly_tool_label as a defensive fallback.
const TOOL_LABELS: Record<string, string> = {
  // Note lens
  inspect_note: "Reviewing the notebook",
  run_r_cell: "Running R cell",
  add_note: "Adding a note",
  promote_to_workspace: "Promoting to workspace",
  inspect_data_files: "Checking data files",
  // Workspace lens
  inspect_project: "Inspecting the project",
  list_recipes: "Listing analysis recipes",
  list_importable_datasets: "Finding example datasets",
  list_files: "Listing workspace files",
  search_workspace: "Searching the workspace",
  search_bioc_books: "Searching Bioconductor books",
  recall_memory: "Recalling project memory",
  read_file: "Reading file",
  read_results: "Reading results",
  compare_results: "Comparing results",
  inspect_failures: "Checking failed jobs",
  validate_report: "Validating report",
  run_r: "Running R inspection",
  ask_user: "Asking you a question",
  import_package_data: "Importing example dataset",
  fetch_url: "Fetching file from URL",
  plan_analysis: "Planning the analysis",
  set_recipe_enabled: "Updating recipe settings",
  update_recipe_parameters: "Updating recipe parameters",
  set_analysis_variables: "Updating analysis variables",
  run_recipe: "Running recipe",
  run_analysis: "Running the analysis",
  undo_project_edit: "Undoing project edit",
  render_report: "Rendering the report",
  repair_report: "Repairing the report",
  rollback_analysis_configuration: "Rolling back configuration",
  edit_project: "Editing project files",
  queue_guidance: "Queuing guidance",
};

export function friendlyToolLabel(toolName: string | null | undefined): string | null {
  if (!toolName) return null;
  const known = TOOL_LABELS[toolName];
  if (known) return known;
  const humanized = toolName.replace(/_/g, " ").trim();
  if (!humanized) return null;
  return humanized.charAt(0).toUpperCase() + humanized.slice(1);
}
