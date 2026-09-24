"""Configuración centralizada de la aplicación.

Todas las variables de entorno se leen aquí mediante pydantic-settings.
El resto del código debe importar `get_settings()` y nunca leer `os.environ` directamente.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Aplicación
    app_name: str = "fraud-assistant"
    app_env: Literal["local", "dev", "staging", "prod"] = "local"
    log_level: str = "INFO"

    # LLM (Groq)
    groq_api_key: Optional[SecretStr] = None
    # Debe soportar structured outputs estrictos (json_schema strict) en Groq.
    groq_model: str = "openai/gpt-oss-120b"
    llm_timeout_seconds: float = 20.0

    # Embeddings / RAG
    # Modelo de fastembed/ONNX (local, multilingüe; no requiere API key).
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    # Prefijos que exigen algunos modelos (p. ej. e5: "query: " / "passage: ").
    embedding_query_prefix: str = ""
    embedding_document_prefix: str = ""
    kb_path: Path = Path("app/kb")
    rag_top_k: int = 4
    rag_min_score: float = 0.0

    # API simulada (bloqueo de tarjetas)
    mock_api_base_url: str = "http://localhost:8001"

    # Persistencia de conversaciones (SQLite)
    database_path: Path = Path("data/chat.db")
    # Mensajes anteriores de la sesión que se pasan como contexto al classifier y al generador.
    history_context_messages: int = 6


@lru_cache
def get_settings() -> Settings:
    return Settings()
