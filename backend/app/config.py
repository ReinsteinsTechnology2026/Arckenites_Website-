from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str
    jwt_ttl_min: int = 60
    cors_origins: str = "http://localhost:5500,http://127.0.0.1:5500"
    env: str = "development"

    seed_admin1_username: str = "akadmin@001"
    seed_admin1_password: str = "admin#001"
    seed_admin2_username: str = "akadmin@002"
    seed_admin2_password: str = "admin#002"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
