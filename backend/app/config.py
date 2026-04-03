"""等保测评助手 - 应用配置"""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # 应用配置
    APP_NAME: str = "等保测评助手"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8021

    # 数据库配置（默认SQLite，用于存储聊天记录和用户信息）
    DATABASE_URL: str = "sqlite:///./sec_inspector.db"

    # JWT配置（总系统统一认证）
    SECRET_KEY: str = "7b4c9e2a8f1d6c3b5e9a2f8c7b4d9e6a3f1b8c7d5e9a2f8c7b4d9e6a3f1b8c"
    ALGORITHM: str = "HS256"
    JWT_ISSUER: str = "gov-backend"
    JWT_AUDIENCE: str = "gov-platform"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8小时

    # LLM配置 (由 .env 文件统一管理)
    LLM_BASE_URL: str
    LLM_API_KEY: str
    LLM_MODEL: str
    LLM_MAX_TOKENS: int = 4096
    LLM_TEMPERATURE: float = 0.7

    # Embedding配置
    EMBEDDING_BASE_URL: str = "http://localhost:8080/v1"
    EMBEDDING_API_KEY: str = "not-needed"
    EMBEDDING_MODEL: str = "bge-large-zh-v1.5"

    # Milvus配置
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530
    MILVUS_COLLECTION: str = "sec_knowledge"

    # 文件存储
    UPLOAD_DIR: str = "./uploads"
    EXPORT_DIR: str = "./exports"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
