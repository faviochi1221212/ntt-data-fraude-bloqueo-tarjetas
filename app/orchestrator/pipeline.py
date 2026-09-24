"""Flujo de /chat: historial -> classifier -> retriever -> backend simulado -> generación ->
guardrails -> ChatResponse (+ metadatos del turno para persistir)."""

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, Sequence

from app.api.mock_backend import BlockStatus, TransactionStatus, simulate_block_request, simulate_transaction_status
from app.guardrails.action_validators import (
    FORMAL_CASE_WHILE_PENDING_GUARDRAIL,
    GUARDRAIL_STATE_DROPPED_AFTER_RETRY,
    PENDING_TRANSACTION_FALLBACK,
    TRANSACTION_PENDING_HOLD,
    BlockStatusGuard,
    block_operation_fact,
    block_status_fallback,
    reports_operation_state,
)
from app.guardrails.faithfulness import FAITHFULNESS_UNVERIFIED, FaithfulnessGuard, FaithfulnessVerifier
from app.guardrails.response_validators import RESPONSE_GUARDRAILS_BY_SEVERITY
from app.models.schemas import (
    ActionResult,
    ChatRequest,
    ChatResponse,
    Citation,
    ClassificationResult,
    ClassifiedIntent,
    Intent,
    IntentOrigin,
    ResponseSource,
    StoredMessage,
)
from app.orchestrator.classifier import LLMClassifier, classify
from app.orchestrator.generator import GenerationError, TurnContext
from app.orchestrator.hard_triggers import detect_hard_triggers
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

# Señal mínima de que el cliente respondió con sus datos de identidad: un número de documento
# (6+ dígitos). Los datos no se validan (el backend es simulado); ver ChatPipeline.process.
_IDENTITY_NUMBER = re.compile(r"(?<!\d)\d{6,}(?!\d)")

# Metadatos del mensaje del asistente que marcan el flujo de bloqueo en curso.
AWAITING_IDENTITY_FOR_BLOCK = "awaiting_identity_for_block"
BLOCK_INTENTS = "block_intents"


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


def pending_block_intents(history: Sequence[StoredMessage]) -> Optional[frozenset[Intent]]:
    """Intents del bloqueo en curso si la ÚLTIMA respuesta del asistente pidió datos de
    identidad para bloquear. Lo decide el historial guardado, no el texto del mensaje actual."""
    last_assistant = next((m for m in reversed(history) if m.role == "assistant"), None)
    if last_assistant is None or not last_assistant.metadata.get(AWAITING_IDENTITY_FOR_BLOCK):
        return None
    return frozenset(Intent(i) for i in last_assistant.metadata.get(BLOCK_INTENTS, [Intent.BLOQUEAR_TARJETA.value]))


def contains_identity_data(message: str) -> bool:
    return bool(_IDENTITY_NUMBER.search(message))


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
        context: Optional[TurnContext] = None,
    ) -> str: ...


@dataclass
class TurnResult:
    """Respuesta del turno + lo necesario para persistirlo y continuar el flujo."""

    response: ChatResponse
    intents: list[str] = field(default_factory=list)
    # True si esta respuesta pidió datos de identidad para un bloqueo: el próximo mensaje de la
    # sesión con esos datos ejecuta el bloqueo.
    awaiting_identity_for_block: bool = False
    block_intents: list[str] = field(default_factory=list)
    block_executed: bool = False

    def assistant_metadata(self) -> dict[str, Any]:
        r = self.response
        return {
            "intents": self.intents,
            "source": r.source.value,
            "guardrail_triggered": r.guardrail_triggered,
            "action": r.action.model_dump() if r.action else None,
            "citations": [c.model_dump() for c in r.citations],
            "requires_human": r.requires_human,
            AWAITING_IDENTITY_FOR_BLOCK: self.awaiting_identity_for_block,
            BLOCK_INTENTS: self.block_intents,
            "block_executed": self.block_executed,
        }


class ChatPipeline:
    def __init__(
        self,
        retriever: Retriever,
        generator: Generator,
        classifier_llm: Optional[LLMClassifier] = None,
        classify_fn: Callable[..., ClassificationResult] = classify,
        verifier: Optional[FaithfulnessVerifier] = None,
        block_simulator: Callable[[str], BlockStatus] = simulate_block_request,
        transaction_simulator: Callable[[str], TransactionStatus] = simulate_transaction_status,
        history_limit: int = 6,
    ):
        self._retriever = retriever
        self._generator = generator
        self._classifier_llm = classifier_llm
        self._classify = classify_fn
        # Sin verificador (p. ej. tests offline que no lo inyectan) no corre el guardrail de fidelidad.
        self._verifier = verifier
        self._block_simulator = block_simulator
        self._transaction_simulator = transaction_simulator
        self._history_limit = history_limit

    def handle(self, request: ChatRequest, history: Sequence[StoredMessage] = ()) -> ChatResponse:
        return self.process(request, history).response

    def process(self, request: ChatRequest, history: Sequence[StoredMessage] = ()) -> TurnResult:
        # Historial ya enmascarado (se guarda así en SQLite); se limita a los últimos mensajes.
        history_ctx = tuple((m.role, m.content) for m in history[-self._history_limit :]) if self._history_limit else ()

        # --- Turno de EJECUCIÓN del bloqueo vs. turno de RECOLECCIÓN de datos -------------
        # Se ejecuta el bloqueo solo si el turno anterior del asistente pidió datos de identidad
        # para bloquear (estado guardado en el historial) y el mensaje actual trae la señal
        # mínima de que el cliente respondió con esos datos (un número de documento). Si no,
        # el turno es de recolección: se clasifica normalmente y, si hay bloqueo, se piden datos.
        pending = pending_block_intents(history)
        execute_block = pending is not None and contains_identity_data(request.message)

        if execute_block:
            intents = [ClassifiedIntent(intent=i, origin=IntentOrigin.SESSION) for i in sorted(pending, key=lambda i: i.value)]
            # Los hard_triggers del mensaje actual siguen contando (p. ej. si además cuenta que
            # le pidieron el CVV).
            for intent in detect_hard_triggers(request.message):
                if intent not in pending:
                    intents.append(ClassifiedIntent(intent=intent, origin=IntentOrigin.HARD_TRIGGER))
        else:
            result = self._classify(request.message, llm=self._classifier_llm, history=history_ctx)
            intents = result.intents
            if not intents:
                # Groq falló y ningún hard_trigger disparó: no sabemos qué quiere el cliente.
                logger.error("Sin intents para session_id=%s (llm_error=%s)", request.session_id, result.llm_error)
                return self._static(request, FALLBACK_ANSWER, requires_human=True)

        real_intents = [i for i in intents if i.intent != Intent.FUERA_DE_ALCANCE]
        intent_names = [i.intent.value for i in real_intents] or [i.intent.value for i in intents]
        if not real_intents:
            return self._static(request, OUT_OF_SCOPE_ANSWER, requires_human=False, intents=intent_names)

        chunks = self._retriever.search_by_intent(request.message, real_intents)
        if not chunks:
            logger.error("Sin chunks para intents %s", intent_names)
            return self._static(request, FALLBACK_ANSWER, requires_human=True, intents=intent_names)

        intent_set = frozenset(i.intent for i in real_intents)

        # --- Backend simulado -----------------------------------------------------------
        action: Optional[ActionResult] = None
        block_status: Optional[BlockStatus] = None
        transaction_status: Optional[TransactionStatus] = None
        if execute_block:
            block_status = BlockStatus(self._block_simulator(f"card-{request.session_id}"))
            action = ActionResult(
                name="block_card",
                success=block_status in (BlockStatus.BLOCKED, BlockStatus.ALREADY_BLOCKED),
                status=block_status.value,
                detail="Estado devuelto por el backend simulado (mock).",
            )
        if Intent.REPORTAR_FRAUDE in intent_set:
            # POL-FRD-2026-4: antes de hablar de un caso formal, se valida el estado de la transacción.
            transaction_status = TransactionStatus(self._transaction_simulator(f"txn-{request.session_id}"))
            if action is None:
                action = ActionResult(
                    name="check_transaction_status",
                    success=True,
                    status=transaction_status.value,
                    detail="Estado devuelto por el backend simulado (mock).",
                )

        context = TurnContext(history=history_ctx, block_status=block_status, transaction_status=transaction_status)
        try:
            answer = self._generator.generate(request.message, chunks, intents=intent_set, context=context)
        except GenerationError:
            return self._static(request, FALLBACK_ANSWER, requires_human=True, intents=intent_names)

        answer, guardrails = self._apply_guardrails(request.message, chunks, answer, intent_set, context)
        if transaction_status == TransactionStatus.PENDING:
            guardrails.insert(0, TRANSACTION_PENDING_HOLD)

        needs_human = requires_human(real_intents, request.message) or block_status == BlockStatus.BLOCK_FAILED
        response = ChatResponse(
            session_id=request.session_id,
            answer=answer,
            source=ResponseSource.KB,
            citations=[
                Citation(chunk_id=r.chunk.chunk_id, source=r.chunk.source_policy, score=round(r.score, 4))
                for r in chunks
            ],
            action=action,
            guardrail_triggered=guardrails,
            requires_human=needs_human,
        )
        awaiting = Intent.BLOQUEAR_TARJETA in intent_set and not execute_block
        return TurnResult(
            response=response,
            intents=intent_names,
            awaiting_identity_for_block=awaiting,
            block_intents=sorted(i.value for i in intent_set) if awaiting else [],
            block_executed=execute_block,
        )

    def _apply_guardrails(
        self,
        message: str,
        chunks: list[RetrievedChunk],
        answer: str,
        intents: frozenset,
        context: TurnContext,
    ) -> tuple[str, list[str]]:
        """Respaldo estructural de las reglas del prompt. Retorna (answer, guardrail_triggered).

        Orden de ejecución de las defensas:
        1. hard_triggers (entrada, en el classifier): intents de seguridad que no dependen del LLM.
        2. formal_case_while_pending (salida, solo si la transacción reportada está pending).
        3. block_status_mismatch (salida, solo en el turno en que se ejecutó el bloqueo y el
           estado no es BLOCKED).
        4. auth_channel_disclosure (salida, todos los intents).
        5. phone_key_outside_block (salida, donde el flujo no admite la clave telefónica:
           not allows_phone_key(intents), es decir sin bloqueo o con phishing).
        6. sensitive_data_request (salida, todos los intents).
        7. unsupported_claim (salida, solo si hay chunks y verificador): fidelidad al contenido
           de los chunks citados y a los hechos del sistema (estado del bloqueo o de la
           transacción), con un segundo LLM. Corre al final porque es el más caro (una llamada
           al LLM) y así verifica la respuesta que ya pasó los guardrails de patrones; si pide un
           reintento, ese reintento igual se revalida contra todos.

        Jerarquía de severidad, de mayor a menor (decide el texto fijo):
        datos sensibles (CVV/PAN/clave completa) > clave telefónica fuera de bloqueo >
        canal de autenticación > estado del bloqueo > caso formal con transacción pending >
        afirmación sin respaldo (fidelidad) > el resto.
        Los tres primeros están en RESPONSE_GUARDRAILS_BY_SEVERITY; los de estado y fidelidad
        se agregan por petición porque dependen del backend simulado y de los chunks.

        Revalidación cruzada: cada guardrail que detecta un problema pide UN reintento, y la
        respuesta regenerada se valida contra TODOS los guardrails que aplican a esos intents,
        no solo contra el que la originó (un reintento puede corregir un problema e introducir
        otro, incluida una afirmación sin respaldo). Si la regenerada viola cualquiera, se usa el
        texto fijo del guardrail más severo entre los involucrados (el que originó el reintento
        y los que violó la regenerada), y todos ellos se reportan en guardrail_triggered. El
        texto fijo depende de los intents y de si el dato sensible lo introdujo el cliente o el
        modelo (ver response_validators). Excepción: si en el turno hubo una operación
        simulada, el texto fijo es siempre el del estado real (bloqueo o transacción pending),
        para no volver a pedir datos ni contradecir lo que devolvió el backend.

        Red de seguridad tras un reintento aceptado: si en el turno hubo una operación y la
        respuesta regenerada ya no la menciona (reports_operation_state), se usa el texto fijo
        del estado real y se agrega state_dropped_after_retry a guardrail_triggered. Un
        reintento que pasa todos los guardrails porque no afirma nada no es una respuesta válida.

        Si el verificador de fidelidad falla (API o veredicto inválido) en cualquier momento de
        la petición, la respuesta generada se bloquea (fail-closed): se reemplaza por el texto
        fijo "no verificada" (siguiente paso según el flujo, distinto del texto de alucinación
        detectada) y se agrega faithfulness_unverified a guardrail_triggered. Si la
        respuesta ya era el texto fijo de otro guardrail (no generado), se mantiene y solo se
        agrega la marca.
        """
        state_guardrails = []
        system_facts: list[str] = []
        if context.block_status is not None:
            state_guardrails.append(BlockStatusGuard(context.block_status).guardrail)
            system_facts.append(f"Estado del bloqueo de la tarjeta devuelto por el sistema: {context.block_status.value}.")
            system_facts.append(block_operation_fact(context.block_status))
        if context.transaction_status == TransactionStatus.PENDING:
            state_guardrails.append(FORMAL_CASE_WHILE_PENDING_GUARDRAIL)
        if context.transaction_status is not None:
            system_facts.append(
                f"Estado de liquidación de la transacción reportada: {context.transaction_status.value}."
            )

        pattern_guardrails = [
            g for g in list(RESPONSE_GUARDRAILS_BY_SEVERITY) + state_guardrails if g.applies(intents)
        ]
        faithfulness = (
            FaithfulnessGuard(self._verifier, chunks, tuple(system_facts)) if self._verifier and chunks else None
        )
        faithfulness_guardrails = [faithfulness.guardrail] if faithfulness else []

        applicable = pattern_guardrails + faithfulness_guardrails  # Por severidad, de mayor a menor.
        execution_order = list(reversed(pattern_guardrails)) + faithfulness_guardrails
        state_fallback = self._state_fallback(context)

        answer, triggered = self._run_guardrails(
            message, chunks, answer, intents, context, applicable, execution_order, state_fallback
        )
        if faithfulness and faithfulness.unverified:
            if not triggered:  # La respuesta es generada y no se pudo verificar: se bloquea.
                faithfulness.guardrail.stats.record_fallback("el verificador falló; respuesta bloqueada")
                answer = state_fallback or faithfulness.unverified_fallback(message, intents)
            triggered.append(FAITHFULNESS_UNVERIFIED)
        return answer, triggered

    @staticmethod
    def _state_fallback(context: TurnContext) -> Optional[str]:
        """Texto fijo del estado real devuelto por el backend simulado, si hubo una operación."""
        if context.block_status is not None:
            return block_status_fallback(context.block_status)
        if context.transaction_status == TransactionStatus.PENDING:
            return PENDING_TRANSACTION_FALLBACK
        return None

    def _run_guardrails(
        self,
        message: str,
        chunks: list[RetrievedChunk],
        answer: str,
        intents: frozenset,
        context: TurnContext,
        applicable: list,
        execution_order: list,
        state_fallback: Optional[str],
    ) -> tuple[str, list[str]]:
        for guardrail in execution_order:
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
                    context=context,
                )
            except GenerationError:
                retried = None

            if retried is None:
                violated = []  # Sin reintento (error de API): solo el guardrail que lo pidió.
            else:
                violated = [g for g in applicable if g.detect(retried, message, intents)]
                if not violated:
                    if state_fallback and not reports_operation_state(
                        retried, context.block_status, context.transaction_status
                    ):
                        # Red de seguridad: el reintento pasó todos los guardrails porque ya no
                        # afirma nada (p. ej. "¿Hay algo más en lo que pueda ayudarte?"), pero
                        # dejó al cliente sin el estado de su operación.
                        guardrail.stats.record_fallback("el reintento omitió el estado de la operación")
                        return state_fallback, [guardrail.name, GUARDRAIL_STATE_DROPPED_AFTER_RETRY]
                    answer = retried
                    continue

            involved = [g for g in applicable if g is guardrail or g in violated]  # Por severidad.
            most_severe = involved[0]
            guardrail.stats.record_fallback(
                "el reintento falló" if retried is None
                else f"el reintento viola {', '.join(g.name for g in violated)}; texto fijo de {most_severe.name}"
            )
            return state_fallback or most_severe.fallback(message, intents), [g.name for g in involved]

        return answer, []

    @staticmethod
    def _static(
        request: ChatRequest, answer: str, requires_human: bool, intents: Optional[list[str]] = None
    ) -> TurnResult:
        response = ChatResponse(
            session_id=request.session_id,
            answer=answer,
            source=ResponseSource.STATIC,
            requires_human=requires_human,
        )
        return TurnResult(response=response, intents=intents or [])
