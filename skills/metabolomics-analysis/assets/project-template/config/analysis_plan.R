analysis_plan <- list(
  study = list(
    key = "analysis_key",
    title = "ANALYSIS_TITLE",
    domain = "metabolomics",
    status = "draft"
  ),
  paths = list(
    metadata = "<required: path to sample metadata csv/tsv>",
    features = "<required: path to metabolite feature table csv/tsv>",
    derived_dir = "derived",
    results_dir = "results",
    report_dir = "report"
  ),
  identifiers = list(
    subject_id = "<required: subject identifier column>",
    sample_id = "<required: sample identifier column>",
    visit = "<optional: visit/time column>",
    feature_id = "<required only when features are rows>"
  ),
  data = list(
    feature_orientation = "features_as_columns",
    feature_scale = "as_provided",
    transform = "none",
    scaling = "none",
    missing_values = c("", "NA", "NaN", "nan", ".")
  ),
  variables = list(
    exposures = c("<required: primary exposure column>"),
    outcomes = character(0),
    factors = character(0),
    covariates = list(
      primary = character(0),
      sensitivity = character(0)
    )
  ),
  models = list(
    complete_case_rule = "model_specific",
    default_min_n = 20,
    fdr_method = "BH",
    fdr_family = "analysis_id"
  ),
  report = list(
    pages = c("study_overview", "analysis_plan", "data_summary", "primary_results", "covariate_diagnostics"),
    render_command = "quarto render report/code"
  ),
  decision_policy = list(
    stop_if_scientific_decision_missing = TRUE,
    append_decision_log = TRUE
  )
)
