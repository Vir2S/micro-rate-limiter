from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = Field(default="rate-limiter", alias="APP_NAME")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    auth_token: str = Field(default="", alias="AUTH_TOKEN")

    policy_path: str = Field(default="policies.json", alias="POLICY_PATH")
    key_prefix: str = Field(default="rl", alias="KEY_PREFIX")

    fallback_to_ip: bool = Field(default=True, alias="FALLBACK_TO_IP")
    trust_x_forwarded_for: bool = Field(default=True, alias="TRUST_X_FORWARDED_FOR")


settings = Settings()
