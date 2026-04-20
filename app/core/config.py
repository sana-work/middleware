from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # App Settings
    APP_NAME: str = "agentic-middleware"
    APP_ENV: str = "local"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    LOG_FILE_PATH: str = "logs/app.log"
    CORS_ALLOW_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    # MongoDB Settings
    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_DATABASE: str = "agentic_middleware"

    # Kafka Settings
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    KAFKA_TOPIC: str = "agentic_events"
    KAFKA_CONSUMER_GROUP: str = "agentic-middleware-recon"
    KAFKA_SECURITY_PROTOCOL: str = "PLAINTEXT"
    KAFKA_SSL_CAFILE: Optional[str] = None
    KAFKA_SSL_CERTFILE: Optional[str] = None
    KAFKA_SSL_KEYFILE: Optional[str] = None
    KAFKA_SSL_PASSWORD: Optional[str] = None
    KAFKA_DEBUG: Optional[str] = None

    # Token Settings
    TOKEN_URL: str = "http://localhost:8080/token"
    TOKEN_CLIENT_SECRET: str = "placeholder"
    TOKEN_TIMEOUT_SECONDS: int = 30
    TOKEN_VERIFY_SSL: bool = True

    # Backend Executor Settings
    BACKEND_EXECUTOR_BASE_URL: str = "http://localhost:8081"
    BACKEND_EXECUTOR_PATH: str = "/api/v1/native-conversational-task-executor"
    BACKEND_CONFIG_ID: str = "placeholder"
    BACKEND_APPLICATION_ID: str = "placeholder"
    BACKEND_REQUEST_TIMEOUT_SECONDS: int = 60
    BACKEND_VERIFY_SSL: bool = True

    # WebSocket Settings
    WEBSOCKET_HEARTBEAT_SECONDS: int = 15
    WEBSOCKET_IDLE_TIMEOUT_SECONDS: int = 300

    # HTTP Retry
    HTTP_RETRY_ATTEMPTS: int = 3
    HTTP_RETRY_BACKOFF_SECONDS: int = 2

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ALLOW_ORIGINS.split(",") if o.strip()]

settings = Settings()
