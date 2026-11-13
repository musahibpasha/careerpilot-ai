from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    mongodb_url: str = "mongodb://localhost:27017"
    mongodb_db: str = "careerpilot"
    cors_origins: str = "http://localhost:5173"

    # OpenRouter AI settings
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.5-flash"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Comma-separated models tried in order if the main model keeps failing.
    openrouter_fallback_models: str = "google/gemini-2.5-flash,openrouter/free"
    openrouter_max_retries: int = 2
    # Resume parsing engine:
    #   "hybrid" (default): OpenRouter AI first, automatic fallback to the local
    #                       parser on rate limits / errors / timeouts. With no
    #                       API key it simply uses the local parser.
    #   "ai":     OpenRouter only (errors are shown to the user).
    #   "local":  offline parser only (instant, no API key).
    resume_parser_mode: str = "hybrid"
    # Max seconds to wait for the AI before falling back to the local parser.
    resume_ai_timeout_seconds: float = 25.0

    # Application Assistant browser support.
    assistant_browser_headless: bool = False
    assistant_keep_open_seconds: int = 1800
    assistant_allow_local_test_form: bool = False
    assistant_browser_cdp_endpoint: str = ""
    assistant_browser_profile_dir: str = ""
    # Kept as a fallback for existing Glassdoor profile configuration.
    assistant_glassdoor_profile_dir: str = ""
    # Diagnostic artifact storage.  Defaults to a 'diagnostics' folder next to
    # the backend package root (backend/diagnostics/).  Set an absolute path to
    # override.  Artifacts are stored under
    # {assistant_trace_dir}/{owner_uid}/{run_id}/.
    assistant_trace_dir: str = ""
    # Days to keep old diagnostic artifact directories before automatic cleanup.
    # Set to 0 to keep forever.
    assistant_artifact_retention_days: int = 7
    vercel: bool = False
    firebase_project_id: str = ""
    firebase_service_account_json: str = ""
    super_admin_emails: str = ""
    ictjob_enabled: bool = False
    arbeitnow_enabled: bool = True
    arbeitnow_timeout_seconds: float = 12.0
    arbeitnow_cache_seconds: int = 600

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    @property
    def allowed_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]

    @property
    def super_admin_email_list(self) -> set[str]:
        return {
            email.strip().casefold()
            for email in self.super_admin_emails.split(",")
            if email.strip()
        }


settings = Settings()