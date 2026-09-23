"""Tests de POST /api/v1/chat.

- Tests offline: lógica del pipeline con dobles (sin red).
- Tests de integración: API real de Groq + índice real de embeddings. Se saltan si no hay
  GROQ_API_KEY. Cada pregunta se envía una sola vez por corrida (respuestas cacheadas).
"""

import re
import unicodedata

import pytest
from fastapi.testclient import TestClient

from app.api.chat import get_pipeline
from app.core.config import get_settings
from app.main import app
from app.models.schemas import (
    ChatRequest,
    Chunk,
    ClassificationResult,
    ClassifiedIntent,
    Intent,
    IntentOrigin,
    ResponseSource,
)
from app.orchestrator.generator import (
    AGGRESSION_RULE,
    AUTH_DATA_RULE,
    GenerationError,
    build_system_prompt,
)
from app.guardrails.response_validators import (
    AUTH_CHANNEL_FALLBACK_ANSWER,
    AUTH_CHANNEL_RETRY_INSTRUCTION,
    DESCRIBES_AUTH_CHANNEL,
    GUARDRAIL_AUTH_CHANNEL,
)
from app.orchestrator.pipeline import FALLBACK_ANSWER, OUT_OF_SCOPE_ANSWER, ChatPipeline
from app.rag.retriever import RetrievedChunk
from tests.test_classifier_manual import QUESTIONS


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


class SpyRetriever:
    def __init__(self, inner=None, result=None):
        self.inner, self.result, self.calls = inner, result or [], 0

    def search_by_intent(self, query, intents):
        self.calls += 1
        return self.inner.search_by_intent(query, intents) if self.inner else self.result


class SpyGenerator:
    """Envuelve un generador real, o devuelve `answers` en secuencia (uno por llamada)."""

    def __init__(self, inner=None, answers=("respuesta",), error=None):
        self.inner, self.answers, self.error = inner, list(answers), error
        self.calls, self.corrections = 0, []

    def generate(self, message, chunks, previous_answer=None, correction=None):
        self.calls += 1
        self.corrections.append(correction)
        if self.error:
            raise self.error
        if self.inner:
            return self.inner.generate(message, chunks, previous_answer=previous_answer, correction=correction)
        return self.answers[min(self.calls, len(self.answers)) - 1]


def _chunk(chunk_id: str, tipo: str, score: float, priority: int) -> RetrievedChunk:
    chunk = Chunk(
        chunk_id=chunk_id,
        titulo=f"titulo {chunk_id}",
        intent="bloquear_tarjeta",
        contenido=f"contenido {chunk_id}",
        source_policy=chunk_id.rsplit("-", 1)[0],
        version="2026",
        metadatos={"tipo": tipo},
    )
    return RetrievedChunk(chunk=chunk, score=score, priority=priority)


def _fixed_classify(*intents: Intent):
    result = ClassificationResult(
        intents=[ClassifiedIntent(intent=i, origin=IntentOrigin.GROQ, confidence=0.9) for i in intents]
    )
    return lambda text, llm=None: result


# --- Offline -----------------------------------------------------------------


def test_system_prompt_keeps_retriever_order_and_aggression_rule():
    chunks = [
        _chunk("POL-SEG-2026-1", "guardrail_critico", 0.1, 0),
        _chunk("POL-BLQ-2026-3B", "regla_dura_seguridad", 0.4, 1),
        _chunk("POL-BLQ-2026-2", "excepcion_seguridad", 0.5, 2),
    ]
    prompt = build_system_prompt(chunks)

    positions = [prompt.index(c.chunk.chunk_id) for c in chunks]
    assert positions == sorted(positions), "los chunks deben aparecer en el orden recibido"
    assert AGGRESSION_RULE in prompt
    assert AUTH_DATA_RULE in prompt


def test_out_of_scope_skips_retriever_and_generator():
    retriever, generator = SpyRetriever(), SpyGenerator()
    pipeline = ChatPipeline(retriever, generator, classify_fn=_fixed_classify(Intent.FUERA_DE_ALCANCE))

    response = pipeline.handle(ChatRequest(session_id="s", message="hola"))

    assert (retriever.calls, generator.calls) == (0, 0)
    assert response.answer == OUT_OF_SCOPE_ANSWER
    assert response.source == ResponseSource.STATIC
    assert response.citations == []


def test_generation_error_returns_fallback_and_escalates():
    retriever = SpyRetriever(result=[_chunk("POL-BLQ-2026-1", "regla_dura", 0.3, 2)])
    generator = SpyGenerator(error=GenerationError("boom"))
    pipeline = ChatPipeline(retriever, generator, classify_fn=_fixed_classify(Intent.BLOQUEAR_TARJETA))

    response = pipeline.handle(ChatRequest(session_id="s", message="perdí mi tarjeta"))

    assert response.answer == FALLBACK_ANSWER
    assert response.source == ResponseSource.STATIC
    assert response.citations == []
    assert response.requires_human is True


CLEAN_ANSWER = "Necesito su documento de identidad y su clave telefónica."
LEAKY_ANSWER = "Necesito su documento de identidad y la clave telefónica que recibe por SMS."


def _block_pipeline(*answers, intents=(Intent.BLOQUEAR_TARJETA,)):
    retriever = SpyRetriever(result=[_chunk("POL-BLQ-2026-1", "regla_dura", 0.3, 2)])
    generator = SpyGenerator(answers=answers)
    return ChatPipeline(retriever, generator, classify_fn=_fixed_classify(*intents)), generator


def test_auth_channel_guardrail_passes_clean_answer_without_retry():
    pipeline, generator = _block_pipeline(CLEAN_ANSWER)
    response = pipeline.handle(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    assert generator.calls == 1
    assert response.answer == CLEAN_ANSWER
    assert response.guardrail_triggered == []


def test_auth_channel_guardrail_retries_once_and_uses_clean_retry():
    pipeline, generator = _block_pipeline(LEAKY_ANSWER, CLEAN_ANSWER)
    response = pipeline.handle(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    assert generator.calls == 2
    assert generator.corrections == [None, AUTH_CHANNEL_RETRY_INSTRUCTION]
    assert response.answer == CLEAN_ANSWER
    assert response.guardrail_triggered == []


def test_auth_channel_guardrail_falls_back_to_fixed_text_after_failed_retry():
    pipeline, generator = _block_pipeline(LEAKY_ANSWER, LEAKY_ANSWER)
    response = pipeline.handle(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    assert generator.calls == 2, "solo un reintento"
    assert response.answer == AUTH_CHANNEL_FALLBACK_ANSWER
    assert response.guardrail_triggered == [GUARDRAIL_AUTH_CHANNEL]


def test_auth_channel_guardrail_does_not_apply_to_phishing():
    # El texto fijo pide clave telefónica, lo que contradice POL-SEG-2026-2 en phishing.
    leaky_phishing = "Si te llegó un correo pidiendo datos, no respondas."
    pipeline, generator = _block_pipeline(
        leaky_phishing, intents=(Intent.REPORTAR_INTENTO_PHISHING, Intent.BLOQUEAR_TARJETA)
    )
    response = pipeline.handle(ChatRequest(session_id="s", message="me pidieron mi clave"))
    assert generator.calls == 1
    assert response.answer == leaky_phishing
    assert response.guardrail_triggered == []


# --- Integración (Groq real) --------------------------------------------------

requires_groq = pytest.mark.skipif(
    get_settings().groq_api_key is None, reason="GROQ_API_KEY no configurada"
)


@pytest.fixture(scope="module")
def live():
    real = get_pipeline()
    retriever = SpyRetriever(inner=real._retriever)
    generator = SpyGenerator(inner=real._generator)
    pipeline = ChatPipeline(retriever, generator, classifier_llm=real._classifier_llm)
    app.dependency_overrides[get_pipeline] = lambda: pipeline
    client = TestClient(app)
    cache: dict[str, dict] = {}
    calls: dict[str, tuple[int, int]] = {}

    def ask(question: str, fresh: bool = False) -> dict:
        """Envía la pregunta; con fresh=True no usa la caché (nueva llamada a Groq)."""
        if fresh or question not in cache:
            before = (retriever.calls, generator.calls)
            response = client.post("/api/v1/chat", json={"session_id": "test", "message": question})
            assert response.status_code == 200, response.text
            if fresh:
                return response.json()
            cache[question] = response.json()
            calls[question] = (retriever.calls - before[0], generator.calls - before[1])
        return cache[question]

    ask.calls = calls
    yield ask
    app.dependency_overrides.pop(get_pipeline, None)


def _citation_ids(body: dict) -> list[str]:
    return [c["chunk_id"] for c in body["citations"]]


@requires_groq
@pytest.mark.parametrize("question", QUESTIONS)
def test_contract_for_base_questions(live, question):
    body = live(question)
    assert body["session_id"] == "test"
    assert body["answer"].strip()
    assert body["action"] is None
    assert body["guardrail_triggered"] == []
    if body["source"] == "kb":
        assert body["citations"], "una respuesta de la KB debe traer citations"
    else:
        assert body["source"] == "static" and body["citations"] == []


@requires_groq
def test_case_9_hola_does_not_call_retriever(live):
    body = live(QUESTIONS[8])
    assert live.calls[QUESTIONS[8]] == (0, 0), "no debe llamar al retriever ni al generador"
    assert body["source"] == "static"
    assert body["answer"] == OUT_OF_SCOPE_ANSWER
    assert body["citations"] == []


@requires_groq
def test_case_1_violent_theft_cites_spoofing_rule(live):
    body = live(QUESTIONS[0])
    assert body["source"] == "kb"
    assert "POL-BLQ-2026-3B" in _citation_ids(body)


# Frases que indicarían que se omite la clave telefónica (excepción por agresión).
_SKIPS_PHONE_KEY = re.compile(
    r"sin (?:necesidad de |pedirte |solicitarte |exigir(?:te)? |requerir )?(?:tu |la |una )?clave"
    r"|no (?:necesitas|necesitaras|es necesari[oa]|hace falta|se requiere|requerimos|te pediremos|te pedimos|sera necesari[oa])\b.{0,40}clave"
    r"|(?:autenticacion|verificacion) reducida"
    r"|omit\w*.{0,40}clave"
)


@requires_groq
@pytest.mark.parametrize("run", range(5))
def test_case_6_lost_card_does_not_skip_phone_key(live, run):
    # 5 corridas nuevas contra Groq: con el guardrail de código el resultado no debe depender
    # de que el LLM obedezca el prompt en esa corrida.
    body = live(QUESTIONS[5], fresh=True)
    answer = _normalize(body["answer"])
    assert body["source"] == "kb"
    assert not _SKIPS_PHONE_KEY.search(answer), f"aplica la excepción por agresión: {body['answer']}"
    assert "clave telefonica" in answer, f"debería exigir clave telefónica: {body['answer']}"
    assert not DESCRIBES_AUTH_CHANNEL.search(answer), f"describe el canal de la clave: {body['answer']}"


# Imperativos dirigidos al cliente pidiendo el dato (no "el banco nunca te pedirá tu CVV").
_ASKS_FOR_CVV = re.compile(
    r"\b(?:ingresa|indica|indicame|proporciona|proporcioname|envia|enviame|dime|comparte|"
    r"compartenos|compartemelo|facilita|facilitame|escribe|escribeme|confirma|confirmame)\b"
    r".{0,40}\b(?:cvv|cvc|codigo de seguridad)\b"
)


@requires_groq
def test_case_3_cvv_cites_golden_rule_and_does_not_ask_for_cvv(live):
    body = live(QUESTIONS[2])
    assert body["source"] == "kb"
    assert _citation_ids(body)[0] == "POL-SEG-2026-1"
    assert not _ASKS_FOR_CVV.search(_normalize(body["answer"])), body["answer"]
