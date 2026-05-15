import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_file=".env")

    APP_NAME: str = "GreenLake Dashboard"
    GLP_CLIENT_ID: str = os.getenv("GLP_CLIENT_ID", "")
    GLP_CLIENT_SECRET: str = os.getenv("GLP_CLIENT_SECRET", "")
    GLP_ACCESS_TOKEN: str = os.getenv("GLP_ACCESS_TOKEN", "")
    GLP_COOKIE: str = os.getenv("GLP_COOKIE", "")
    
    # Path to token file if using parsing from file
    TOKEN_FILE: str = os.getenv("TOKEN_FILE", "token.yaml")


settings = Settings()
