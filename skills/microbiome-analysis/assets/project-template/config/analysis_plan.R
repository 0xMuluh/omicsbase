analysis_plan <- list(
  study = list(
    key = "analysis_key",
    title = "ANALYSIS_TITLE",
    domain = "microbiome",
    status = "draft"
  ),
  paths = list(
    feature_table = "<required: path to count/profile table csv/tsv/rds>",
    taxonomy_table = "<optional: path to taxonomy table csv/tsv>",
    metadata = "<required: path to sample metadata csv/tsv/xlsx>",
    functional_tables = list(
      pathabundance = "<optional: HUMAnN3 path abundance table>",
      metacyc = "<optional: HUMAnN3 MetaCyc table>"
    ),
    derived_dir = "derived",
    results_dir = "results",
    report_dir = "report",
    package_manifest = "config/r_package_manifest.csv"
  ),
  identifiers = list(
    sample_id = "<required: sample identifier column>",
    subject_id = "<optional: subject identifier column>",
    visit = "<optional: visit/time column>",
    feature_id = "<required only when features are rows>",
    taxon_id = "<optional: taxonomy feature identifier column>"
  ),
  preprocessing = list(
    input_kind = "feature_table",
    metadata_sheet = 1,
    feature_id_column = "<required only when features are rows>",
    sample_name_cleanup_regex = "",
    sample_name_cleanup_replacement = "",
    remove_feature_patterns = character(0),
    rank_filter_terminal = TRUE,
    allow_unmatched_samples = FALSE,
    create_bioc_object_when_possible = TRUE,
    package_policy = "check_only"
  ),
  features = list(
    feature_orientation = "samples_as_rows",
    input_scale = "counts",
    taxonomy_rank = "species",
    assay_name = "relabundance",
    prevalence_min = 0.10,
    abundance_min = 0,
    zero_handling = "pseudo_count",
    pseudo_count = 0.5,
    transformation = "clr_for_differential_abundance"
  ),
  variables = list(
    groups = c("<required: primary group or exposure column>"),
    exposures = c("<required: primary group or exposure column>"),
    outcomes = character(0),
    factors = character(0),
    covariates = list(
      primary = character(0),
      sensitivity = character(0)
    )
  ),
  analyses = list(
    alpha_metrics = c("observed", "shannon", "simpson"),
    beta_distances = c("bray"),
    ordination = "pcoa",
    differential_abundance_method = "maaslin3",
    fdr_method = "BH",
    permutations = 999,
    seed = 1,
    default_min_n = 20
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
