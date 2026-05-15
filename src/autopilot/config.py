from __future__ import annotations

try:
    from pydantic import BaseSettings, Field

    class Settings(BaseSettings):
        # GitHub
        GITHUB_TOKEN: str | None = Field(None, description="Personal access token or app installation token")
        GITHUB_API_URL: str = Field("https://api.github.com", description="GitHub API base URL")
        GITHUB_WEBHOOK_SECRET: str | None = Field(None, description="GitHub webhook secret for signature verification")

        # Redis/Postgres
        REDIS_URL: str | None = Field(None, description="Redis URL, e.g. redis://localhost:6379/0")
        POSTGRES_DSN: str | None = Field(None, description="Postgres DSN, e.g. postgresql://user:pass@host:5432/db")

        # Notification tokens
        SLACK_BOT_TOKEN: str | None = None
        TEAMS_WEBHOOK_URL: str | None = None
        SLACK_WEBHOOK_URL: str | None = None

        # Third-party API keys
        TAVILY_API_KEY: str | None = None
        OMIUM_API_KEY: str | None = None

        # Docs
        NOTION_TOKEN: str | None = None
        CONFLUENCE_TOKEN: str | None = None

        # Project management
        JIRA_URL: str | None = None
        JIRA_TOKEN: str | None = None
        LINEAR_TOKEN: str | None = None

        # Cloud infra
        AWS_PROFILE: str | None = None
        GCP_PROJECT: str | None = None
        AZURE_SUBSCRIPTION_ID: str | None = None

        # Runtime options
        SQLITE_PATH: str | None = None

        class Config:
            env_file = ".env"
            env_file_encoding = "utf-8"

    settings = Settings()
except Exception:
    # Fallback lightweight settings for environments without pydantic BaseSettings
    class Settings:
        GITHUB_TOKEN: str | None = None
        GITHUB_API_URL: str = "https://api.github.com"
        GITHUB_WEBHOOK_SECRET: str | None = None

        REDIS_URL: str | None = None
        POSTGRES_DSN: str | None = None

        SLACK_BOT_TOKEN: str | None = None
        TEAMS_WEBHOOK_URL: str | None = None
        SLACK_WEBHOOK_URL: str | None = None

        TAVILY_API_KEY: str | None = None
        OMIUM_API_KEY: str | None = None

        NOTION_TOKEN: str | None = None
        CONFLUENCE_TOKEN: str | None = None

        JIRA_URL: str | None = None
        JIRA_TOKEN: str | None = None
        LINEAR_TOKEN: str | None = None

        AWS_PROFILE: str | None = None
        GCP_PROJECT: str | None = None
        AZURE_SUBSCRIPTION_ID: str | None = None

        SQLITE_PATH: str | None = None

    settings = Settings()
