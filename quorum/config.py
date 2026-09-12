"""Runtime configuration. Everything comes from the environment (.env is loaded by python-dotenv)."""
from __future__ import annotations

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- chat platform -------------------------------------------------------------------------
    slack_bot_token: str = ""
    slack_app_token: str = ""
    # comma-separated channel ids where the passive features (auto-suggest, memory recall) are allowed; empty = all channels the bot is in
    watch_channels: str = ""

    # --- LLM -----------------------------------------------------------------------------------
    openai_api_key: str = ""
    openai_base_url: str | None = None
    llm_model_fast: str = "gpt-5.4-mini"   # extractor, classifier, memory recall
    llm_model_smart: str = "gpt-5.4"       # decision record writer
    llm_reasoning_effort: str = "low"
    llm_timeout_s: float = 60.0

    # --- plugins (each one disables itself when its key is empty) ------------------------------
    exa_key: str = Field(default="", alias="EXA_KEY")
    # one Atlassian account for both Jira and Confluence (per-product vars below override these)
    atlassian_email: str = ""
    atlassian_token: str = Field(default="", validation_alias=AliasChoices("ATLASSIAN_TOKEN", "ATTLASIAN_TOKEN", "ATLASSIAN_API_TOKEN"))
    confluence_base_url: str = ""          # https://<site>.atlassian.net/wiki (default: JIRA_BASE_URL + /wiki)
    confluence_email: str = ""
    confluence_api_token: str = ""
    confluence_space_key: str = ""
    confluence_parent_page_id: str = ""
    jira_base_url: str = ""                # https://<site>.atlassian.net
    jira_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = ""
    jira_issue_type: str = "Task"          # matched case-insensitively against the project's (localised) types
    markdown_records_dir: str = "records"  # fallback recorder: ADR markdown files
    slack_canvas_enabled: bool = False     # needs canvases:write; free plans may reject

    # --- behaviour -----------------------------------------------------------------------------
    lang: str = Field(default="en", alias="QUORUM_LANG")   # ui language of the card: en | ru
    coalesce_seconds: float = 4.0          # debounce window before the LLM sees a burst of messages
    silence_minutes: int = 30              # open question addressed to X, X silent for this long -> one DM
    silence_messages: int = 5              # ... or this many messages by other people after the question
    stall_hours: float = 6.0               # no messages while deliberating -> Stalled
    expire_hours: float = 48.0             # Stalled for this long -> Expired
    autosuggest_every_n: int = 8           # passive classifier cadence per channel (0 = off)
    scheduler_tick_seconds: float = 20.0
    db_path: str = "data/quorum.db"
    log_level: str = "INFO"
    timezone: str = "Asia/Tashkent"     # for deadlines typed as dates and for the LLM ("by Wednesday")
    demo_time_scale: float = 1.0           # >1 speeds up timers for a demo (e.g. 60 => a minute is a second)
    demo_personas: str = ""                # comma-separated bot usernames (chat:write.customize) treated as humans in seeded demo threads

    @model_validator(mode="after")
    def _atlassian_defaults(self) -> Settings:
        self.jira_email = self.jira_email or self.atlassian_email
        self.jira_api_token = self.jira_api_token or self.atlassian_token
        self.confluence_email = self.confluence_email or self.atlassian_email or self.jira_email
        self.confluence_api_token = self.confluence_api_token or self.atlassian_token or self.jira_api_token
        if not self.confluence_base_url and self.jira_base_url:
            self.confluence_base_url = self.jira_base_url.rstrip("/") + "/wiki"
        return self


settings = Settings()
