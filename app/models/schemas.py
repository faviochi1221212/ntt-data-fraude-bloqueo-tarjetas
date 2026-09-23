"""Schemas Pydantic, incluido el contrato de payload de respuesta del asistente."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ResponseSource(str, Enum):
    """Origen de la respuesta que decide el orquestador."""

    KB = "kb"  # Base de conocimiento (RAG)
    API = "api"  # API simulada (p. ej. bloqueo de tarjeta)
    GUARDRAIL = "guardrail"  # Respuesta bloqueada/forzada por una validación dura


class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Identificador de la conversación")
    message: str = Field(..., min_length=1, description="Mensaje del usuario")


class Citation(BaseModel):
    chunk_id: str
    source: str
    score: Optional[float] = None


class ActionResult(BaseModel):
    name: str = Field(..., description="Acción ejecutada, p. ej. 'block_card'")
    success: bool
    reference_id: Optional[str] = None
    detail: Optional[str] = None


class ChatResponse(BaseModel):
    """Contrato de payload de respuesta del asistente."""

    session_id: str
    answer: str
    source: ResponseSource
    citations: list[Citation] = Field(default_factory=list)
    action: Optional[ActionResult] = None
    guardrail_triggered: bool = False
    requires_human: bool = False
