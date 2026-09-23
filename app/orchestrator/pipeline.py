"""Flujo de /chat: classifier -> retriever -> generación -> ChatResponse."""

import logging
import re
import unicodedata
from typing import Callable, Optional, Protocol

from app.models.schemas import (
    ChatRequest,
    ChatResponse,
    Citation,
    ClassificationResult,
    ClassifiedIntent,
    Intent,
    ResponseSource,
)
from app.guardrails.response_validators import (
    AUTH_CHANNEL_FALLBACK_ANSWER,
    AUTH_CHANNEL_RETRY_INSTRUCTION,
    GUARDRAIL_AUTH_CHANNEL,
    auth_channel_stats,
    describes_auth_channel,
)
from app.orchestrator.classifier import LLMClassifier, classify
from app.orchestrator.generator import GenerationError
from app.rag.retriever import RetrievedChunk

logger = logging.getLogger(__name__)

OUT_OF_SCOPE_ANSWER = (
    "Puedo ayudarte con bloqueo de tarjetas, reportes de fraude, reposición o phishing. "
    "¿En qué te ayudo?"
)
FALLBACK_ANSWER = (
    "En este momento no puedo procesar tu solicitud. Te derivaremos con un asesor. "
    "Si tu tarjeta está en riesgo, comunícate de inmediato con banca telefónica."
)

# Heurística simple para "el cliente ya entregó datos" en un reporte de phishing.
_DATA_ALREADY_GIVEN = re.compile(
    r"\b(?:le|les|ya)\s+(?:di|dicte|pase|entregue|comparti|proporcione|envie)\b"
    r"|\b(?:entregue|comparti|proporcione|dicte)\b"
)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def requires_human(intents: list[ClassifiedIntent], message: str) -> bool:
    """Casos obvios de derivación a humano (versión simple, a refinar):

    - Phishing con evidencia explícita de que el cliente ya entregó datos.
    - Desbloqueo: la política lo deriva siempre a banca telefónica o atención presencial.
    """
    found = {i.intent for i in intents}
    if Intent.DESBLOQUEAR_TARJETA in found:
        return True
    return Intent.REPORTAR_INTENTO_PHISHING in found and bool(
        _DATA_ALREADY_GIVEN.search(_normalize(message))
    )


class Retriever(Protocol):
    def search_by_intent(self, query: str, intents: list[ClassifiedIntent]) -> list[RetrievedChunk]: ...


class Generator(Protocol):
    def generate(
        self,
        message: str,
        chunks: list[RetrievedChunk],
        previous_answer: Optional[str] = None,
        correction: Optional[str] = None,
    ) -> str: ...


class ChatPipeline:
    def __init__(
        self,
        retriever: Retriever,
        generator: Generator,
        classifier_llm: Optional[LLMClassifier] = None,
        classify_fn: Callable[..., ClassificationResult] = classify,
    ):
        self._retriever = retriever
        self._generator = generator
        self._classifier_llm = classifier_llm
        self._classify = classify_fn

    def handle(self, request: ChatRequest) -> ChatResponse:
        result = self._classify(request.message, llm=self._classifier_llm)
        intents = result.intents

        if not intents:
            # Groq falló y ningún hard_trigger disparó: no sabemos qué quiere el cliente.
            logger.error("Sin intents para session_id=%s (llm_error=%s)", request.session_id, result.llm_error)
            return self._static(request, FALLBACK_ANSWER, requires_human=True)

        real_intents = [i for i in intents if i.intent != Intent.FUERA_DE_ALCANCE]
        if not real_intents:
            return self._static(request, OUT_OF_SCOPE_ANSWER, requires_human=False)

        chunks = self._retriever.search_by_intent(request.message, real_intents)
        if not chunks:
            logger.error("Sin chunks para intents %s", [i.intent.value for i in real_intents])
            return self._static(request, FALLBACK_ANSWER, requires_human=True)

        try:
            answer = self._generator.generate(request.message, chunks)
        except GenerationError:
            return self._static(request, FALLBACK_ANSWER, requires_human=True)

        guardrails: list[str] = []
        if self._auth_channel_guardrail_applies(real_intents):
            answer = self._enforce_no_auth_channel(request.message, chunks, answer, guardrails)

        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            source=ResponseSource.KB,
            citations=[
                Citation(chunk_id=r.chunk.chunk_id, source=r.chunk.source_policy, score=round(r.score, 4))
                for r in chunks
            ],
            action=None,  # Se completa con la API simulada en un paso posterior.
            guardrail_triggered=guardrails,
            requires_human=requires_human(real_intents, request.message),
        )

    @staticmethod
    def _auth_channel_guardrail_applies(intents: list[ClassifiedIntent]) -> bool:
        # Solo en el flujo de bloqueo: su texto fijo pide documento + clave telefónica, que es lo
        # correcto para el bloqueo estándar pero NO para phishing (solo documento, POL-SEG-2026-2)
        # ni desbloqueo (no se capturan datos, POL-BLQ-2026-5). En phishing, además, frases como
        # "si te llegó un correo" son legítimas y el patrón las marcaría.
        return {i.intent for i in intents} == {Intent.BLOQUEAR_TARJETA}

    def _enforce_no_auth_channel(
        self, message: str, chunks: list[RetrievedChunk], answer: str, guardrails: list[str]
    ) -> str:
        """Respaldo estructural de AUTH_DATA_RULE: un reintento y, si falla, texto fijo."""
        auth_channel_stats.record_check()
        if not describes_auth_channel(answer):
            return answer

        auth_channel_stats.record_retry()
        try:
            retried = self._generator.generate(
                message, chunks, previous_answer=answer, correction=AUTH_CHANNEL_RETRY_INSTRUCTION
            )
        except GenerationError:
            retried = None

        if retried is not None and not describes_auth_channel(retried):
            return retried

        auth_channel_stats.record_fallback(
            "el reintento falló" if retried is None else "el reintento también describe el canal"
        )
        guardrails.append(GUARDRAIL_AUTH_CHANNEL)
        return AUTH_CHANNEL_FALLBACK_ANSWER

    @staticmethod
    def _static(request: ChatRequest, answer: str, requires_human: bool) -> ChatResponse:
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            source=ResponseSource.STATIC,
            requires_human=requires_human,
        )
