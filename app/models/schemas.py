"""Schemas Pydantic, incluido el contrato de payload de respuesta del asistente."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Intent(str, Enum):
    BLOQUEAR_TARJETA = "bloquear_tarjeta"
    DESBLOQUEAR_TARJETA = "desbloquear_tarjeta"
    REPORTAR_FRAUDE = "reportar_fraude"
    SOLICITAR_TARJETA_NUEVA = "solicitar_tarjeta_nueva"
    REPORTAR_INTENTO_PHISHING = "reportar_intento_phishing"
    FUERA_DE_ALCANCE = "fuera_de_alcance"


class IntentOrigin(str, Enum):
    HARD_TRIGGER = "hard_trigger"
    GROQ = "groq"


class ClassifiedIntent(BaseModel):
    intent: Intent
    origin: IntentOrigin
    # Confianza reportada por Groq; None si el intent vino solo de hard_trigger.
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class ClassificationResult(BaseModel):
    intents: list[ClassifiedIntent] = Field(default_factory=list)
    # Motivo por el que la capa Groq no aportó resultados (sin key, error de API, JSON inválido).
    llm_error: Optional[str] = None
    # Problemas parciales en la respuesta de Groq (p. ej. un intent inválido descartado).
    llm_warnings: list[str] = Field(default_factory=list)


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
    guardrail_triggered: list[str] = Field(
        default_factory=list,
        description="Guardrails activados; lista vacía si no se activó ninguno",
    )
    requires_human: bool = False
