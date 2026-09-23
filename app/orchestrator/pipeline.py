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
from app.guardrails.response_validators import RESPONSE_GUARDRAILS_BY_SEVERITY
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
        intents: frozenset = frozenset(),
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

        intent_set = frozenset(i.intent for i in real_intents)
        try:
            answer = self._generator.generate(request.message, chunks, intents=intent_set)
        except GenerationError:
            return self._static(request, FALLBACK_ANSWER, requires_human=True)

        answer, guardrails = self._apply_guardrails(request.message, chunks, answer, intent_set)

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

    def _apply_guardrails(
        self, message: str, chunks: list[RetrievedChunk], answer: str, intents: frozenset
    ) -> tuple[str, list[str]]:
        """Respaldo estructural de las reglas del prompt. Retorna (answer, guardrail_triggered).

        Orden de ejecución de las defensas:
        1. hard_triggers (entrada, en el classifier): intents de seguridad que no dependen del LLM.
        2. auth_channel_disclosure (salida, todos los intents).
        3. phone_key_outside_block (salida, donde el flujo no admite la clave telefónica:
           not allows_phone_key(intents), es decir sin bloqueo o con phishing).
        4. sensitive_data_request (salida, todos los intents): corre al final, como última
           barrera antes de responder.

        Jerarquía de severidad (RESPONSE_GUARDRAILS_BY_SEVERITY), de mayor a menor:
        datos sensibles (CVV/PAN/clave completa) > clave telefónica fuera de bloqueo >
        canal de autenticación > el resto. La ejecución va en orden inverso para que el más
        severo sea la última barrera.

        Revalidación cruzada: cada guardrail que detecta un problema pide UN reintento, y la
        respuesta regenerada se valida contra TODOS los guardrails que aplican a esos intents,
        no solo contra el que la originó (un reintento puede corregir un problema e introducir
        otro). Si la regenerada viola cualquiera, se usa el texto fijo del guardrail más severo
        entre los involucrados (el que originó el reintento y los que violó la regenerada), y
        todos ellos se reportan en guardrail_triggered. El texto fijo depende de los intents y
        de si el dato sensible lo introdujo el cliente o el modelo (ver response_validators).
        """
        applicable = [g for g in RESPONSE_GUARDRAILS_BY_SEVERITY if g.applies(intents)]

        for guardrail in reversed(applicable):  # Ejecución: el más severo al final.
            guardrail.stats.record_check()
            if not guardrail.detect(answer, message, intents):
                continue

            guardrail.stats.record_retry()
            try:
                retried = self._generator.generate(
                    message,
                    chunks,
                    intents=intents,
                    previous_answer=answer,
                    correction=guardrail.retry_instruction(intents),
                )
            except GenerationError:
                retried = None

            if retried is None:
                violated = []  # Sin reintento (error de API): solo el guardrail que lo pidió.
            else:
                violated = [g for g in applicable if g.detect(retried, message, intents)]
                if not violated:
                    answer = retried
                    continue

            involved = [g for g in applicable if g is guardrail or g in violated]  # Por severidad.
            most_severe = involved[0]
            guardrail.stats.record_fallback(
                "el reintento falló" if retried is None
                else f"el reintento viola {', '.join(g.name for g in violated)}; texto fijo de {most_severe.name}"
            )
            return most_severe.fallback(message, intents), [g.name for g in involved]

        return answer, []

    @staticmethod
    def _static(request: ChatRequest, answer: str, requires_human: bool) -> ChatResponse:
        return ChatResponse(
            session_id=request.session_id,
            answer=answer,
            source=ResponseSource.STATIC,
            requires_human=requires_human,
        )
