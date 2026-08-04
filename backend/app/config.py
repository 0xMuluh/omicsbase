"""OmicsBase backend configuration."""

from pathlib import Path
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    # LLM Provider Configuration
    llm_provider: str = "anthropic"  # "anthropic", "openai", "qwen", "gemini", "openrouter", "deepseek", "groq", "grok", "xai", "ollama"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    dashscope_api_key: str = ""
    qwen_api_key: str = ""
    gemini_api_key: str = ""
    openrouter_api_key: str = ""
    groq_api_key: str = ""
    grok_api_key: str = ""
    xai_api_key: str = ""
    openai_base_url: str = ""        # Optional custom base_url for Ollama, vLLM, DeepSeek, Groq, OpenRouter
    qwen_base_url: str = ""
    llm_model: str = "claude-sonnet-4-20250514"
    llm_input_cost_per_million: float = 0.0
    llm_output_cost_per_million: float = 0.0
    # Fast-intent path: simple, tool-free questions answered directly with a
    # faster model (empty fast_path_model selects the provider's fast default).
    fast_path_enabled: bool = True
    fast_path_model: str = ""

    # Database
    database_url: str = "postgresql://omicsbase:omicsbase@localhost:5433/omicsbase"

    # Redis / task queue
    redis_url: str = "redis://localhost:6379/0"
    task_backend: str = "celery"  # celery or background

    # Project storage
    projects_dir: str = "./projects"

    # Paths
    registry_path: str = str(Path(__file__).parent.parent.parent / "registry" / "decision_points.yaml")
    prompts_dir: str = str(Path(__file__).parent.parent.parent / "prompts")

    # QMD-first Bioconductor book knowledge
    bioc_knowledge_catalog_path: str = str(Path(__file__).parent.parent / "knowledge" / "bioc_books.yaml")
    bioc_knowledge_storage_dir: str = str(Path(__file__).parent.parent / "knowledge")
    bioc_knowledge_sync_enabled: bool = True
    bioc_knowledge_sync_preview_enabled: bool = False
    bioc_knowledge_sync_interval_hours: int = 168

    # Server
    backend_port: int = 8000
    frontend_url: str = "http://localhost:3000"

    # Optional shared-deployment auth (leave empty for local dev)
    api_key: str = ""

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

    # Workspace agent autonomy
    agent_max_steps: int = 6
    agent_run_stale_after_seconds: int = 300
    agent_allow_acquisition: bool = True

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
