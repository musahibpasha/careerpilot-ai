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

    # Application Assistant browser support.
    assistant_browser_headless: bool = False
    assistant_keep_open_seconds: int = 1800
    assistant_allow_local_test_form: bool = False
    assistant_browser_path: str = ""
    assistant_glassdoor_profile_dir: str = ""

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


settings = Settings()