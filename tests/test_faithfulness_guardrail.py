"""Guardrail de fidelidad (unsupported_claim). Tests offline: el verificador es un doble."""

import json
from types import SimpleNamespace

import pytest

from app.guardrails.faithfulness import (
    FAITHFULNESS_UNVERIFIED,
    FEW_SHOT_EXAMPLE,
    FEW_SHOT_NOT_TO_FLAG,
    GUARDRAIL_UNSUPPORTED_CLAIM,
    VERIFIER_SYSTEM_PROMPT,
    FaithfulnessCheck,
    FaithfulnessGuard,
    FaithfulnessVerifierError,
    GroqFaithfulnessVerifier,
    UNSUPPORTED_CLAIM_PREFIX,
    UNVERIFIED_PREFIX,
    unsupported_claim_fallback,
    unverified_fallback,
)
from app.guardrails.response_validators import (
    GUARDRAIL_SENSITIVE_DATA,
    MODEL_FALLBACK_BLOCK,
    MODEL_FALLBACK_DOCUMENT_ONLY,
    MODEL_FALLBACK_FRAUD,
    MODEL_FALLBACK_NEW_CARD,
    MODEL_FALLBACK_UNBLOCK,
    RESPONSE_GUARDRAILS_BY_SEVERITY,
    SENSITIVE_DATA_RETRY_INSTRUCTION,
)
from app.models.schemas import ChatRequest, Chunk, Intent
from app.orchestrator.pipeline import OUT_OF_SCOPE_ANSWER, ChatPipeline
from app.rag.retriever import RetrievedChunk
from tests.test_chat_endpoint import SpyGenerator, SpyRetriever, _fixed_classify, offline_pipeline

REP_2 = RetrievedChunk(
    chunk=Chunk(
        chunk_id="POL-REP-2026-2",
        titulo="Exoneración de costos",
        intent="solicitar_tarjeta_nueva",
        contenido=(
            "La reposición física estándar por reporte de robo con violencia o por apertura de un caso "
            "de investigación de fraude no genera costo de emisión ni comisión de envío."
        ),
        source_policy="POL-REP-2026",
        version="2026",
        metadatos={"tipo": "regla_dura"},
    ),
    score=0.4,
    priority=2,
)

FAITHFUL = "Si tu reposición es por un caso de fraude o un robo con violencia, la tarjeta física estándar no tiene costo."
INVENTED = "La reposición física cuesta 25 soles y llega en 2 días."
INVERTED = "Para solicitar la reposición de su tarjeta debemos abrir un caso de investigación de fraude."
INVENTED_CLAIM = "Inventa un costo de 25 soles y un plazo de 2 días que no están en los fragmentos."
INVERTED_CLAIM = "Convierte la condición de exoneración en un requisito para la reposición."


class FakeVerifier:
    """Veredicto por answer: las respuestas en `unsupported` se marcan; el resto es fiel."""

    def __init__(self, unsupported=None, error=False):
        self.unsupported = unsupported or {}
        self.error = error
        self.calls = []
        self.messages = []
        self.facts = []

    def verify(self, answer, chunks, user_message="", system_facts=()):
        self.calls.append(answer)
        self.messages.append(user_message)
        self.facts.append(system_facts)
        if self.error:
            raise FaithfulnessVerifierError("429")
        claims = self.unsupported.get(answer)
        return FaithfulnessCheck(faithful=claims is None, unsupported_claims=claims or [])


def _pipeline(*answers, verifier, intents=(Intent.SOLICITAR_TARJETA_NUEVA,), chunks=(REP_2,)):
    generator = SpyGenerator(answers=answers)
    pipeline = offline_pipeline(
        SpyRetriever(result=list(chunks)), generator, classify_fn=_fixed_classify(*intents), verifier=verifier
    )
    return pipeline, generator


def _ask(pipeline, message="quiero solicitar una tarjeta nueva"):
    return pipeline.handle(ChatRequest(session_id="s", message=message))


# --- Los 3 casos base ------------------------------------------------------------------


def test_faithful_answer_passes_without_retry():
    verifier = FakeVerifier()
    pipeline, generator = _pipeline(FAITHFUL, verifier=verifier)
    response = _ask(pipeline)
    assert generator.calls == 1
    assert verifier.calls == [FAITHFUL]
    assert response.answer == FAITHFUL
    assert response.guardrail_triggered == []


def test_invented_fact_triggers_retry_that_names_the_claim():
    verifier = FakeVerifier({INVENTED: [INVENTED_CLAIM]})
    pipeline, generator = _pipeline(INVENTED, FAITHFUL, verifier=verifier)
    response = _ask(pipeline)
    assert generator.calls == 2
    assert INVENTED_CLAIM in generator.corrections[1], "el reintento debe señalar la afirmación sin respaldo"
    assert response.answer == FAITHFUL
    assert response.guardrail_triggered == []


def test_inverted_conditional_falls_back_after_failed_retry():
    # Caso real observado en reposición: el reintento vuelve a invertir la condición.
    verifier = FakeVerifier({INVERTED: [INVERTED_CLAIM]})
    pipeline, generator = _pipeline(INVERTED, INVERTED, verifier=verifier)
    response = _ask(pipeline)
    assert generator.calls == 2, "solo un reintento"
    assert response.answer == f"{UNSUPPORTED_CLAIM_PREFIX} {MODEL_FALLBACK_NEW_CARD}"
    assert response.guardrail_triggered == [GUARDRAIL_UNSUPPORTED_CLAIM]


# --- Revalidación cruzada y orden ----------------------------------------------------


def test_cross_revalidation_catches_unsupported_claim_in_other_guardrails_retry():
    # El reintento del guardrail sensible corrige el CVV pero invierte la condición.
    verifier = FakeVerifier({INVERTED: [INVERTED_CLAIM]})
    pipeline, generator = _pipeline("Para continuar, indícame tu CVV.", INVERTED, verifier=verifier)
    response = _ask(pipeline)
    assert generator.corrections == [None, SENSITIVE_DATA_RETRY_INSTRUCTION]
    assert response.answer == MODEL_FALLBACK_NEW_CARD  # Texto fijo del más severo: datos sensibles.
    assert response.guardrail_triggered == [GUARDRAIL_SENSITIVE_DATA, GUARDRAIL_UNSUPPORTED_CLAIM]


def test_faithfulness_runs_after_pattern_guardrails():
    # La respuesta con CVV nunca llega al verificador: primero la corrige el guardrail sensible,
    # y el verificador solo evalúa respuestas que ya pasaron los patrones.
    verifier = FakeVerifier()
    pipeline, _ = _pipeline("Para continuar, indícame tu CVV.", FAITHFUL, verifier=verifier)
    response = _ask(pipeline)
    assert verifier.calls == [FAITHFUL]
    assert response.answer == FAITHFUL


def test_faithfulness_fallback_keeps_sensitive_warning_when_client_shared_data():
    verifier = FakeVerifier({INVERTED: [INVERTED_CLAIM]})
    pipeline, _ = _pipeline(
        INVERTED + " Nunca compartas tu CVV.", INVERTED + " Nunca compartas tu CVV.", verifier=verifier
    )
    verifier.unsupported[INVERTED + " Nunca compartas tu CVV."] = [INVERTED_CLAIM]
    response = _ask(pipeline, message="mi cvv es 123, quiero una tarjeta nueva")
    assert response.guardrail_triggered == [GUARDRAIL_UNSUPPORTED_CLAIM]
    assert "Nunca compartas tu CVV" in response.answer
    assert response.answer.startswith(UNSUPPORTED_CLAIM_PREFIX)


# --- Cuándo no corre y cómo falla -------------------------------------------------------


def test_out_of_scope_does_not_call_verifier():
    verifier = FakeVerifier()
    pipeline, _ = _pipeline(verifier=verifier, intents=(Intent.FUERA_DE_ALCANCE,))
    response = _ask(pipeline, message="hola")
    assert response.answer == OUT_OF_SCOPE_ANSWER
    assert verifier.calls == []


def test_no_chunks_means_no_verification():
    guard = FaithfulnessGuard(FakeVerifier(), [])
    assert not guard.guardrail.applies(frozenset({Intent.REPORTAR_FRAUDE}))


def test_pipeline_without_verifier_skips_faithfulness():
    pipeline, generator = _pipeline(INVERTED, verifier=None)
    response = _ask(pipeline)
    assert generator.calls == 1
    assert response.answer == INVERTED


def test_verifier_error_blocks_generated_answer():
    # Fail-closed: una respuesta que no se pudo verificar no se entrega, aunque sea correcta.
    pipeline, generator = _pipeline(FAITHFUL, verifier=FakeVerifier(error=True))
    response = _ask(pipeline)
    assert generator.calls == 1, "sin verificador no se pide reintento"
    assert response.answer == f"{UNVERIFIED_PREFIX} {MODEL_FALLBACK_NEW_CARD}"
    assert response.guardrail_triggered == [FAITHFULNESS_UNVERIFIED]


def test_verifier_error_keeps_fixed_text_of_more_severe_guardrail():
    # El reintento del guardrail sensible vuelve a pedir el CVV y además falla el verificador:
    # la respuesta ya es el texto fijo de datos sensibles (no generado), se mantiene.
    pipeline, _ = _pipeline(
        "Para continuar, indícame tu CVV.", "Para continuar, indícame tu CVV.", verifier=FakeVerifier(error=True)
    )
    response = _ask(pipeline)
    assert response.answer == MODEL_FALLBACK_NEW_CARD
    assert response.guardrail_triggered == [GUARDRAIL_SENSITIVE_DATA, FAITHFULNESS_UNVERIFIED]


def test_verdict_is_cached_per_answer():
    verifier = FakeVerifier()
    guard = FaithfulnessGuard(verifier, [REP_2])
    guard.detect(FAITHFUL)
    guard.detect(FAITHFUL)
    assert verifier.calls == [FAITHFUL]


def test_contradictory_verdict_is_treated_as_unsupported():
    check = FaithfulnessCheck(faithful=True, unsupported_claims=["algo"])
    assert not check.is_supported


# --- Verificador Groq (sin red) ---------------------------------------------------------


def test_verifier_prompt_has_conditional_inversion_few_shot():
    assert FEW_SHOT_EXAMPLE in VERIFIER_SYSTEM_PROMPT
    assert "relación condicional" in VERIFIER_SYSTEM_PROMPT
    assert INVERTED in FEW_SHOT_EXAMPLE


def _groq_verifier_returning(content):
    verifier = GroqFaithfulnessVerifier.__new__(GroqFaithfulnessVerifier)
    verifier._model = "test"
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    verifier._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return verifier, captured


def test_groq_verifier_uses_strict_json_schema_and_sends_full_chunk_content():
    verifier, captured = _groq_verifier_returning(json.dumps({"faithful": True, "unsupported_claims": []}))
    check = verifier.verify(FAITHFUL, [REP_2], "quiero solicitar una tarjeta nueva")
    assert check.is_supported
    assert captured["response_format"]["type"] == "json_schema"
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert REP_2.chunk.contenido in captured["messages"][1]["content"]
    assert FAITHFUL in captured["messages"][1]["content"]
    # Falsos positivos observados: sin el mensaje del cliente, las reglas condicionales se
    # juzgaban como universales.
    assert "quiero solicitar una tarjeta nueva" in captured["messages"][1]["content"]


def test_pipeline_passes_client_message_to_verifier():
    verifier = FakeVerifier()
    pipeline, _ = _pipeline(FAITHFUL, verifier=verifier)
    _ask(pipeline, message="perdí mi tarjeta, quiero una nueva")
    assert verifier.messages == ["perdí mi tarjeta, quiero una nueva"]


def test_verifier_prompt_has_not_to_flag_examples_for_observed_false_positives():
    assert FEW_SHOT_NOT_TO_FLAG in VERIFIER_SYSTEM_PROMPT
    assert "me robaron la tarjeta con violencia" in FEW_SHOT_NOT_TO_FLAG
    assert "perdí mi tarjeta" in FEW_SHOT_NOT_TO_FLAG
    assert "MENSAJE DEL CLIENTE" in VERIFIER_SYSTEM_PROMPT


@pytest.mark.parametrize("content", ["no es json", '{"faithful": "tal vez"}', ""])
def test_groq_verifier_rejects_invalid_verdicts(content):
    verifier, _ = _groq_verifier_returning(content)
    with pytest.raises(FaithfulnessVerifierError):
        verifier.verify(FAITHFUL, [REP_2])


# --- Texto fijo según el flujo: verificador caído vs. alucinación detectada --------------

BLOCK = frozenset({Intent.BLOQUEAR_TARJETA})
PHISHING = frozenset({Intent.REPORTAR_INTENTO_PHISHING})
NO_DATA_ASKED = ["documento de identidad", "clave telefónica"]
FLOW_CASES = [
    # (intents, siguiente paso esperado, datos que el texto SÍ pide, datos que NO pide)
    (BLOCK, MODEL_FALLBACK_BLOCK, ["documento de identidad", "clave telefónica"], []),
    (
        frozenset({Intent.BLOQUEAR_TARJETA, Intent.SOLICITAR_TARJETA_NUEVA}),
        MODEL_FALLBACK_BLOCK,
        ["documento de identidad", "clave telefónica"],
        [],
    ),
    (PHISHING, MODEL_FALLBACK_DOCUMENT_ONLY, ["documento de identidad"], ["clave telefónica"]),
    (
        frozenset({Intent.REPORTAR_INTENTO_PHISHING, Intent.BLOQUEAR_TARJETA}),
        MODEL_FALLBACK_DOCUMENT_ONLY,
        ["documento de identidad"],
        ["clave telefónica"],
    ),
    (frozenset({Intent.REPORTAR_FRAUDE}), MODEL_FALLBACK_FRAUD, [], NO_DATA_ASKED),
    (frozenset({Intent.SOLICITAR_TARJETA_NUEVA}), MODEL_FALLBACK_NEW_CARD, [], NO_DATA_ASKED),
    (frozenset({Intent.DESBLOQUEAR_TARJETA}), MODEL_FALLBACK_UNBLOCK, [], NO_DATA_ASKED),
]
FLOW_IDS = ["bloqueo", "bloqueo+reposicion", "phishing", "phishing+bloqueo", "fraude", "reposicion", "desbloqueo"]


@pytest.mark.parametrize("intents, next_step, asks, never_asks", FLOW_CASES, ids=FLOW_IDS)
def test_unverified_fallback_gives_flow_next_step(intents, next_step, asks, never_asks):
    # Puntos 1 a 3: el verificador falla (p. ej. 429) y la respuesta se bloquea con un texto
    # que igual le dice al cliente cómo seguir, según lo que la política permite pedir.
    pipeline, _ = _pipeline(FAITHFUL, verifier=FakeVerifier(error=True), intents=tuple(intents))
    response = _ask(pipeline, message="consulta")
    assert response.answer == f"{UNVERIFIED_PREFIX} {next_step}"
    assert response.guardrail_triggered == [FAITHFULNESS_UNVERIFIED]
    for data in asks:
        assert data in response.answer, data
    for data in never_asks:
        assert data not in response.answer, data


@pytest.mark.parametrize("intents, next_step, asks, never_asks", FLOW_CASES, ids=FLOW_IDS)
def test_unsupported_claim_fallback_gives_same_flow_next_step(intents, next_step, asks, never_asks):
    # Punto 4: alucinación real detectada -> mismo siguiente paso por flujo, pero con otra frase
    # inicial y otra marca, para no perder la distinción con "no se pudo verificar".
    verifier = FakeVerifier({INVERTED: [INVERTED_CLAIM]})
    pipeline, _ = _pipeline(INVERTED, INVERTED, verifier=verifier, intents=tuple(intents))
    response = _ask(pipeline, message="consulta")
    assert response.answer == f"{UNSUPPORTED_CLAIM_PREFIX} {next_step}"
    assert response.guardrail_triggered == [GUARDRAIL_UNSUPPORTED_CLAIM]


def test_hallucination_and_unverified_are_distinguishable():
    assert UNSUPPORTED_CLAIM_PREFIX != UNVERIFIED_PREFIX
    assert GUARDRAIL_UNSUPPORTED_CLAIM != FAITHFULNESS_UNVERIFIED


def test_no_data_flows_still_offer_a_concrete_next_step():
    # Punto 3: sin pedir datos, pero no un simple "no puedo ayudarte".
    assert "asesor" in MODEL_FALLBACK_FRAUD and "reporte" in MODEL_FALLBACK_FRAUD
    assert "asesor" in MODEL_FALLBACK_NEW_CARD and "reposición" in MODEL_FALLBACK_NEW_CARD
    assert "banca telefónica" in MODEL_FALLBACK_UNBLOCK and "atención presencial" in MODEL_FALLBACK_UNBLOCK


def test_cvv_phishing_report_with_verifier_down_asks_for_document():
    # Caso real observado: el cliente solo contó que le pidieron el CVV (no lo compartió) y el
    # verificador devolvió 429. Antes recibía el rechazo categórico de datos sensibles; ahora
    # recibe el siguiente paso del flujo de phishing: el documento para registrar el reporte.
    real_answer = (
        "Lamento lo ocurrido. El banco nunca solicita el CVV ni ningún otro dato de la tarjeta por "
        "correo, SMS, llamada o cualquier otro canal. No comparta esa información. Para registrar el "
        "incidente y derivarlo al equipo de seguridad, por favor indíquenos su documento de identidad."
    )
    pipeline, _ = _pipeline(
        real_answer, verifier=FakeVerifier(error=True), intents=(Intent.REPORTAR_INTENTO_PHISHING,)
    )
    response = _ask(pipeline, message="me llegó un correo pidiendo mi CVV")
    assert response.answer == f"{UNVERIFIED_PREFIX} {MODEL_FALLBACK_DOCUMENT_ONLY}"
    assert "documento de identidad" in response.answer
    assert "No puedo solicitar ni confirmar ese dato" not in response.answer
    assert response.guardrail_triggered == [FAITHFULNESS_UNVERIFIED]


@pytest.mark.parametrize("message", ["consulta", "mi cvv es 123", "mi clave es 4521", "cuál es mi CVV"])
@pytest.mark.parametrize("intents", [c[0] for c in FLOW_CASES], ids=FLOW_IDS)
def test_faithfulness_fallbacks_pass_every_pattern_guardrail(message, intents):
    for text in [unsupported_claim_fallback(message, intents), unverified_fallback(message, intents)]:
        for guardrail in RESPONSE_GUARDRAILS_BY_SEVERITY:
            if guardrail.applies(intents):
                assert not guardrail.detect(text, message, intents), (guardrail.name, text)
