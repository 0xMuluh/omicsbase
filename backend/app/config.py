"""OmicsBase backend configuration."""

from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    # LLM Provider Configuration
    llm_provider: str = "anthropic"  # "anthropic", "openai", "qwen", "gemini", "openrouter", "orcarouter", "deepseek", "groq", "grok", "xai", "ollama"
    anthropic_api_key: str = Field(default="", repr=False)
    openai_api_key: str = Field(default="", repr=False)
    dashscope_api_key: str = Field(default="", repr=False)
    qwen_api_key: str = Field(default="", repr=False)
    gemini_api_key: str = Field(default="", repr=False)
    openrouter_api_key: str = Field(default="", repr=False)
    orcarouter_api_key: str = Field(default="", repr=False)
    groq_api_key: str = Field(default="", repr=False)
    grok_api_key: str = Field(default="", repr=False)
    xai_api_key: str = Field(default="", repr=False)
    openai_base_url: str = ""        # Optional custom base_url for Ollama, vLLM, DeepSeek, Groq, OpenRouter, OrcaRouter
    qwen_base_url: str = ""
    llm_model: str = "claude-sonnet-4-20250514"
    # Native Gemini SDK (google-genai) instead of the OpenAI-compatible endpoint.
    gemini_native: bool = True
    # Thinking budget for Gemini thinking models; 0 = provider default (auto).
    gemini_thinking_budget: int = 0
    llm_input_cost_per_million: float = 0.0
    llm_output_cost_per_million: float = 0.0
    # Per-task model targets: "provider:model" (empty provider = the global
    # LLM_PROVIDER; empty value = fall back to the global LLM_MODEL).
    llm_agent_target: str = ""    # agent tool loops (workspace + notes)
    llm_fast_target: str = ""     # fast-intent path (no tools)
    llm_planner_target: str = ""  # analysis planning
    llm_title_target: str = ""    # project title generation

    # Database
    database_url: str = Field(
        default="postgresql://omicsbase:omicsbase@localhost:5433/omicsbase",
        repr=False,
    )

    # Redis / task queue
    redis_url: str = Field(default="redis://localhost:6379/0", repr=False)
    task_backend: str = "celery"  # celery or background

    # Project storage
    projects_dir: str = "./projects"
    # Optional administrator-managed catalog of additional ReportPacks. Packs
    # are selected by manifest ID; plans never provide raw filesystem paths.
    report_packs_dir: str = ""

    # Paths
    registry_path: str = str(Path(__file__).parent.parent.parent / "registry" / "decision_points.yaml")
    prompts_dir: str = str(Path(__file__).parent.parent.parent / "prompts")
    # Skills live next to prompts (same app root). PROMPTS_DIR is env-overridable
    # (see docker-compose), so an unset skills_dir follows prompts_dir's parent.
    skills_dir: str = ""

    # QMD-first Bioconductor book knowledge
    bioc_knowledge_catalog_path: str = str(Path(__file__).parent.parent / "knowledge" / "bioc_books.yaml")
    bioc_knowledge_storage_dir: str = str(Path(__file__).parent.parent / "knowledge")
    bioc_knowledge_sync_enabled: bool = True
    bioc_knowledge_sync_preview_enabled: bool = False
    bioc_knowledge_sync_interval_hours: int = 168
    # Semantic retrieval is local and optional. If the embedding runtime or
    # model is unavailable, the QMD lexical index remains authoritative.
    bioc_knowledge_semantic_enabled: bool = True
    bioc_knowledge_embedding_model: str = "BAAI/bge-small-en-v1.5"
    bioc_knowledge_embedding_batch_size: int = 32
    bioc_knowledge_semantic_candidate_limit: int = 64
    bioc_knowledge_embedding_cache_dir: str = ""

    # Server
    backend_port: int = 8000
    frontend_url: str = "http://localhost:3000"

    # Optional shared-deployment auth (leave empty for local dev)
    api_key: str = Field(default="", repr=False)

    # Development mode — when True, permits empty api_key, default tenant/user
    # headers, and sandbox bypass. MUST be False in production.
    dev_mode: bool = False

    # Execution Sandboxing
    use_docker_sandbox: bool = True
    docker_image: str = "omicsbase-runner:latest"
    docker_memory_limit: str = "2g"
    docker_cpu_limit: str = "2.0"
    docker_pids_limit: int = 100

    # Interactive note cell execution
    note_execution_default_timeout_seconds: int = 120
    note_execution_max_timeout_seconds: int = 1800
    note_execution_output_preview_chars: int = 200_000
    note_execution_cache_enabled: bool = True
    note_execution_capture_plots: bool = True
    note_execution_shared_workspace: bool = True
    note_execution_quiet_package_startup: bool = True
    note_execution_agent_wait_enabled: bool = True
    note_execution_max_output_artifacts: int = 25
    note_execution_max_output_artifact_bytes: int = 25 * 1024 * 1024

    # Local-development persistent R kernel: one long-lived host R process per
    # thread keeps the workspace in memory between cells. This optimization is
    # ignored unless dev_mode is True; deployed execution uses the isolated
    # one-shot runner while preserving shared state through workspace.RData.
    note_kernel_enabled: bool = True
    note_kernel_idle_ttl_seconds: int = 1800

    # Workspace agent autonomy
    agent_max_steps: int = 10  # generic fallback; the workspace lens uses the project envelope
    agent_max_budget_units: int = 20
    agent_max_tool_calls: int = 36
    agent_max_mutations: int = 6
    agent_max_llm_calls: int = 12
    agent_max_generated_tokens: int = 20000
    agent_max_retrieved_chars: int = 80000
    agent_run_stale_after_seconds: int = 300
    agent_continuation_max_attempts: int = 2
    agent_allow_acquisition: bool = True

    # Legacy OpenHands-era flag still read by the project agent's bash policy.
    openhands_unconstrained_bash: bool = False

    # One persistent project orchestrator. Zero-valued limits mean
    # that OmicsBase does not impose a project-wide token/call/step budget;
    # completion is governed by runtime-declared artifact contracts instead.
    project_agent_max_budget_units: int = 0
    project_agent_max_tool_calls: int = 0
    project_agent_max_mutations: int = 0
    project_agent_max_llm_calls: int = 0
    project_agent_max_generated_tokens: int = 0
    project_agent_max_retrieved_chars: int = 0
    project_agent_max_input_tokens: int = 0
    project_agent_max_total_tokens: int = 0
    project_agent_max_steps: int = 0
    project_agent_max_output_tokens: int = 0
    project_agent_use_retry_guard: bool = True
    project_agent_max_tool_retries: int = 2
    # Fallback step ceiling for the workspace/project loop when
    # PROJECT_AGENT_MAX_STEPS is zero/unset.
    project_agent_default_steps: int = 48

    # Note agent: generous per-turn step budget (each step is one LLM
    # roundtrip, possibly with several tool calls). Lower with
    # NOTE_AGENT_MAX_STEPS if runaway turns are a concern.
    note_agent_max_steps: int = 32
    # Notes has a larger resource envelope than Workspace because a single
    # notebook request may inspect state, retrieve methodology, run R, wait for
    # an asynchronous execution, and then interpret the result. These remain
    # per-turn circuit breakers, not lifetime notebook limits.
    note_agent_max_budget_units: int = 32
    note_agent_max_tool_calls: int = 48
    note_agent_max_mutations: int = 8
    note_agent_max_llm_calls: int = 16
    note_agent_max_generated_tokens: int = 40000
    note_agent_max_retrieved_chars: int = 160000
    note_agent_max_generated_code_cells: int = 8

    # Output token ceilings per LLM call. High defaults so answers are never
    # cut off mid-reply; the model stops naturally when finished.
    agent_max_output_tokens: int = 16000
    # Presentation gate: how many LLM repair rounds run on language findings
    # before remaining findings are reported rather than fixed.
    qa_repair_rounds: int = 1

    # Agent Engine Backend: "opencode" (native coding agent) or "legacy" (in-house loop)
    agent_backend: str = "opencode"
    opencode_bin: str = "/home/simple/.opencode/bin/opencode"
    opencode_server_url: str = ""

    # Project coding-agent path toggle (legacy naming).
    project_agent_enabled: bool = True

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Prefer this project's .env over unrelated shell variables."""
        return init_settings, dotenv_settings, env_settings, file_secret_settings

    model_config = {"env_file": "../.env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
