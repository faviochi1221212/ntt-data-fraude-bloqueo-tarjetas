"""Guardrails de salida: datos sensibles (POL-SEG-2026-1), clave telefónica fuera del bloqueo
estándar y canal de autenticación. Tests offline, sin llamar a Groq."""

import itertools

import pytest

from app.guardrails.response_validators import (
    AUTH_CHANNEL_FALLBACK_ANSWER,
    AUTH_CHANNEL_RETRY_INSTRUCTION,
    AUTH_CHANNEL_RETRY_INSTRUCTION_GENERIC,
    GUARDRAIL_AUTH_CHANNEL,
    GUARDRAIL_PHONE_KEY_OUTSIDE_BLOCK,
    GUARDRAIL_SENSITIVE_DATA,
    MODEL_FALLBACK_BLOCK,
    MODEL_FALLBACK_DOCUMENT_ONLY,
    MODEL_FALLBACK_FRAUD,
    MODEL_FALLBACK_NEW_CARD,
    MODEL_FALLBACK_NO_DATA,
    MODEL_FALLBACK_UNBLOCK,
    PHONE_KEY_OUTSIDE_BLOCK_GUARDRAIL,
    PHONE_KEY_RETRY_INSTRUCTION,
    RESPONSE_GUARDRAILS_BY_SEVERITY,
    SENSITIVE_DATA_FALLBACK_ANSWER,
    SENSITIVE_DATA_RETRY_INSTRUCTION,
    allows_phone_key,
    client_asks_own_sensitive_data,
    client_involved_sensitive_data,
    client_shares_sensitive_data,
    describes_auth_channel,
    extract_shared_secrets,
    requests_or_reveals_sensitive_data,
    requests_phone_key,
    flow_next_step,
    sensitive_data_fallback,
)
from app.models.schemas import ChatRequest, Intent
from app.orchestrator.pipeline import ChatPipeline
from tests.test_chat_endpoint import SpyGenerator, SpyRetriever, _chunk, _fixed_classify, offline_pipeline

SHARED_KEY_MESSAGE = "mi clave es 4521, ayúdame a bloquear mi tarjeta"

BLOCK = frozenset({Intent.BLOQUEAR_TARJETA})
PHISHING = frozenset({Intent.REPORTAR_INTENTO_PHISHING})
FRAUD = frozenset({Intent.REPORTAR_FRAUDE})
NEW_CARD = frozenset({Intent.SOLICITAR_TARJETA_NUEVA})
UNBLOCK = frozenset({Intent.DESBLOQUEAR_TARJETA})
PHISHING_BLOCK = frozenset({Intent.REPORTAR_INTENTO_PHISHING, Intent.BLOQUEAR_TARJETA})
BLOCK_NEW_CARD = frozenset({Intent.BLOQUEAR_TARJETA, Intent.SOLICITAR_TARJETA_NUEVA})
ALL_INTENT_SETS = [BLOCK, PHISHING, FRAUD, NEW_CARD, UNBLOCK, PHISHING_BLOCK, BLOCK_NEW_CARD]
INTENT_IDS = ["bloqueo", "phishing", "fraude", "reposicion", "desbloqueo", "phishing+bloqueo", "bloqueo+reposicion"]
# Flujos donde la clave telefónica no se admite (allows_phone_key es False).
NO_PHONE_KEY_SETS = [PHISHING, FRAUD, NEW_CARD, UNBLOCK, PHISHING_BLOCK]

# --- Detector de datos sensibles ---------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "Por favor, indíqueme su CVV.",
        "Necesito el código de seguridad de su tarjeta.",
        "Dime los 3 dígitos de atrás de tu tarjeta.",
        "Proporcióneme el número completo de su tarjeta para verificar.",
        "Confírmame tu clave de banca por internet completa.",
        "¿Podría indicarme su CVV?",
        "No te preocupes, indícame tu CVV y lo reviso.",
        "Envíame tu PAN.",
        "Tu CVV es correcto.",
    ],
)
def test_detects_requests_and_confirmations(answer):
    assert requests_or_reveals_sensitive_data(answer)


@pytest.mark.parametrize(
    "answer",
    [
        "El banco nunca te pedirá tu CVV, bajo ninguna circunstancia.",
        "No, nunca le solicitaremos el CVV ni la clave completa.",
        "No compartas tu código de seguridad con nadie.",
        "No necesito tu CVV.",
        "Por favor, indíqueme su documento de identidad y la clave telefónica.",
        "Nunca compartas tu CVV, tu clave completa ni el número completo de tu tarjeta.",
        "La reposición estándar tarda de 3 a 5 días hábiles.",
        "Compré pan ayer.",
    ],
)
def test_ignores_policy_statements_and_legit_requests(answer):
    assert not requests_or_reveals_sensitive_data(answer)


IGNORES_WITHOUT_WARNING = "Entiendo. Para continuar, indícame tu documento de identidad."
WARNS = "Por seguridad, no vuelvas a escribir tu clave por este canal. Indícame tu documento de identidad."


def test_classifies_shared_secrets_by_level():
    bare = extract_shared_secrets(SHARED_KEY_MESSAGE)
    assert (bare.strict, bare.bare) == (frozenset(), frozenset({"4521"}))
    strict = extract_shared_secrets("mi cvv es 123 y mi tarjeta 4111-1111-1111-1111")
    assert strict.strict == {"123", "4111111111111111"}


@pytest.mark.parametrize("intents", [BLOCK, PHISHING], ids=["bloqueo", "phishing"])
def test_repeating_or_confirming_a_shared_secret_is_always_blocked(intents):
    # Ni siquiera la posible clave telefónica del bloqueo se repite o confirma.
    assert requests_or_reveals_sensitive_data("Recibí tu código 4521, procedo.", SHARED_KEY_MESSAGE, intents)
    assert requests_or_reveals_sensitive_data("Gracias, recibí tu clave.", SHARED_KEY_MESSAGE, intents)
    assert not requests_or_reveals_sensitive_data(WARNS, SHARED_KEY_MESSAGE, intents)


@pytest.mark.parametrize(
    "intents, allowed",
    [
        (BLOCK, True),
        (BLOCK_NEW_CARD, True),
        (frozenset({Intent.BLOQUEAR_TARJETA, Intent.REPORTAR_FRAUDE}), True),
        (PHISHING_BLOCK, False),
        (PHISHING, False),
        (FRAUD, False),
        (NEW_CARD, False),
        (UNBLOCK, False),
    ],
)
def test_allows_phone_key(intents, allowed):
    assert allows_phone_key(intents) is allowed


def test_bare_key_needs_warning_only_where_phone_key_is_not_allowed():
    # Punto 1: bloqueo + reposición admite la clave (no exige advertencia); phishing + bloqueo no.
    for intents in [BLOCK, BLOCK_NEW_CARD]:
        assert not requests_or_reveals_sensitive_data(IGNORES_WITHOUT_WARNING, SHARED_KEY_MESSAGE, intents), intents
    for intents in NO_PHONE_KEY_SETS:
        assert requests_or_reveals_sensitive_data(IGNORES_WITHOUT_WARNING, SHARED_KEY_MESSAGE, intents), intents


def test_explicit_cvv_or_pan_needs_warning_even_in_block():
    for message in ["mi cvv es 123, bloquea mi tarjeta", "mi tarjeta es 4111 1111 1111 1111, bloquéala"]:
        assert requests_or_reveals_sensitive_data(IGNORES_WITHOUT_WARNING, message, BLOCK)
        assert not requests_or_reveals_sensitive_data(
            "Nunca compartas ese dato por este canal. Indícame tu documento de identidad.", message, BLOCK
        )


def test_detects_repeating_a_pan_with_different_separators():
    message = "mi tarjeta es 4111-1111-1111-1111"
    assert requests_or_reveals_sensitive_data("Validé la tarjeta 4111 1111 1111 1111.", message)


# --- Detectores de clave telefónica y de canal ---------------------------------------


@pytest.mark.parametrize(
    "answer, expected",
    [
        ("Indícame tu documento de identidad y la clave telefónica.", True),
        ("Necesito validar su identidad con:\n- **Documento de identidad**\n- **Clave telefónica**", True),
        ("No compartas tu clave telefónica con nadie.", False),
        ("Para registrar el incidente, indícame tu documento de identidad.", False),
    ],
)
def test_phone_key_detector(answer, expected):
    assert requests_phone_key(answer) is expected


@pytest.mark.parametrize(
    "answer, expected",
    [
        ("Indícame la clave telefónica que recibes en tu móvil.", True),
        ("Necesito la clave telefónica que recibe por mensaje.", True),
        ("Necesito la clave que recibe por SMS.", True),
        ("Indíqueme la clave telefónica que recibe en su número registrado.", True),
        # Educación sobre phishing: el canal va antes que el dato; es legítimo.
        ("Si te llega un SMS pidiendo tu clave, es phishing.", False),
        ("Si recibiste un correo pidiendo tu código, no respondas.", False),
        ("Indícame tu documento de identidad y tu clave telefónica.", False),
    ],
)
def test_auth_channel_detector(answer, expected):
    assert describes_auth_channel(answer) is expected


# --- Textos fijos --------------------------------------------------------------------


@pytest.mark.parametrize(
    "message, intents, expected",
    [
        ("perdí mi tarjeta", BLOCK, MODEL_FALLBACK_BLOCK),
        # Punto 2: bloqueo + reposición usa el texto de bloqueo; phishing + bloqueo, solo documento.
        ("perdí mi tarjeta, quiero una nueva", BLOCK_NEW_CARD, MODEL_FALLBACK_BLOCK),
        ("me llegó un correo raro", PHISHING, MODEL_FALLBACK_DOCUMENT_ONLY),
        ("me llegó un correo raro", PHISHING_BLOCK, MODEL_FALLBACK_DOCUMENT_ONLY),
        ("no reconozco un cargo", FRAUD, MODEL_FALLBACK_FRAUD),
        ("quiero una tarjeta nueva", NEW_CARD, MODEL_FALLBACK_NEW_CARD),
        ("quiero desbloquear mi tarjeta", UNBLOCK, MODEL_FALLBACK_UNBLOCK),
        ("no reconozco un cargo y quiero una nueva", FRAUD | NEW_CARD, MODEL_FALLBACK_NO_DATA),
        ("cuál es mi CVV", PHISHING, SENSITIVE_DATA_FALLBACK_ANSWER),
        (SHARED_KEY_MESSAGE, BLOCK, SENSITIVE_DATA_FALLBACK_ANSWER),
    ],
)
def test_sensitive_fallback_depends_on_intent_and_origin(message, intents, expected):
    assert sensitive_data_fallback(message, intents) == expected


@pytest.mark.parametrize(
    "message", ["perdí mi tarjeta", SHARED_KEY_MESSAGE, "mi cvv es 123", "mi tarjeta es 4111 1111 1111 1111"]
)
@pytest.mark.parametrize("intents", ALL_INTENT_SETS, ids=INTENT_IDS)
def test_every_fallback_passes_every_applicable_guardrail(message, intents):
    applicable = [g for g in RESPONSE_GUARDRAILS_BY_SEVERITY if g.applies(intents)]
    for source, checker in itertools.product(applicable, applicable):
        fallback = source.fallback(message, intents)
        assert not checker.detect(fallback, message, intents), (source.name, checker.name, fallback)


def test_fallback_never_asks_phone_key_where_not_allowed():
    for intents in NO_PHONE_KEY_SETS:
        for guardrail in RESPONSE_GUARDRAILS_BY_SEVERITY:
            assert "clave telefónica" not in guardrail.fallback("consulta", intents), (guardrail.name, intents)


# --- Ramas en el pipeline -------------------------------------------------------

CLEAN = "Nunca compartas tu CVV. Para continuar, indícame tu documento de identidad."
ASKS_CVV = "Para continuar, indícame tu CVV."
CHANNEL_LEAK = "Necesito su documento de identidad y la clave telefónica que recibe en su móvil."
ASKS_PHONE_KEY = "Para registrar tu reporte, indícame tu documento de identidad y tu clave telefónica."


def _pipeline(*answers, intents=(Intent.BLOQUEAR_TARJETA,)):
    retriever = SpyRetriever(result=[_chunk("POL-SEG-2026-1", "guardrail_critico", 0.2, 0)])
    generator = SpyGenerator(answers=answers)
    return offline_pipeline(retriever, generator, classify_fn=_fixed_classify(*intents)), generator


def test_clean_answer_passes_without_retry():
    pipeline, generator = _pipeline(CLEAN)
    response = pipeline.handle(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    assert generator.calls == 1
    assert response.answer == CLEAN
    assert response.guardrail_triggered == []


def test_retries_once_with_system_correction():
    pipeline, generator = _pipeline(ASKS_CVV, CLEAN)
    response = pipeline.handle(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    assert generator.calls == 2
    assert generator.corrections == [None, SENSITIVE_DATA_RETRY_INSTRUCTION]
    assert response.answer == CLEAN
    assert response.guardrail_triggered == []


def test_model_originated_fallback_in_block_tells_how_to_continue():
    pipeline, generator = _pipeline(ASKS_CVV, ASKS_CVV)
    response = pipeline.handle(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    assert generator.calls == 2, "solo un reintento"
    assert response.answer == MODEL_FALLBACK_BLOCK
    assert response.guardrail_triggered == [GUARDRAIL_SENSITIVE_DATA]


def test_client_originated_fallback_is_categorical_rejection():
    pipeline, _ = _pipeline("Recibí tu clave 4521.", "Recibí tu clave 4521.")
    response = pipeline.handle(ChatRequest(session_id="s", message=SHARED_KEY_MESSAGE))
    assert "4521" not in response.answer
    assert response.answer == SENSITIVE_DATA_FALLBACK_ANSWER
    assert response.guardrail_triggered == [GUARDRAIL_SENSITIVE_DATA]


@pytest.mark.parametrize("intents", ALL_INTENT_SETS, ids=INTENT_IDS)
def test_sensitive_guardrail_applies_to_every_intent(intents):
    pipeline, generator = _pipeline(ASKS_CVV, ASKS_CVV, intents=tuple(intents))
    response = pipeline.handle(ChatRequest(session_id="s", message="consulta"))
    assert generator.calls == 2
    assert response.answer == sensitive_data_fallback("consulta", intents)
    assert response.guardrail_triggered == [GUARDRAIL_SENSITIVE_DATA]


@pytest.mark.parametrize(
    "intents", [PHISHING, FRAUD, NEW_CARD, UNBLOCK], ids=["phishing", "fraude", "reposicion", "desbloqueo"]
)
def test_phone_key_guardrail_fires_without_block(intents):
    pipeline, generator = _pipeline(ASKS_PHONE_KEY, ASKS_PHONE_KEY, intents=tuple(intents))
    response = pipeline.handle(ChatRequest(session_id="s", message="consulta"))
    assert generator.corrections == [None, PHONE_KEY_RETRY_INSTRUCTION]
    assert response.guardrail_triggered == [GUARDRAIL_PHONE_KEY_OUTSIDE_BLOCK]
    assert "clave telefónica" not in response.answer


def test_phone_key_guardrail_does_not_fire_for_block_plus_new_card():
    # "perdí mi tarjeta, quiero una nueva": el paso de bloqueo necesita la clave telefónica.
    pipeline, generator = _pipeline(ASKS_PHONE_KEY, intents=tuple(BLOCK_NEW_CARD))
    response = pipeline.handle(ChatRequest(session_id="s", message="perdí mi tarjeta, quiero una nueva"))
    assert generator.calls == 1
    assert response.answer == ASKS_PHONE_KEY
    assert response.guardrail_triggered == []


@pytest.mark.parametrize("intents", [BLOCK, BLOCK_NEW_CARD], ids=["bloqueo", "bloqueo+reposicion"])
def test_phone_key_guardrail_does_not_apply_when_block_is_present_without_phishing(intents):
    assert not PHONE_KEY_OUTSIDE_BLOCK_GUARDRAIL.applies(intents)


def test_phone_key_guardrail_fires_for_phishing_plus_block():
    # POL-SEG-2026-2: ante phishing la identidad es solo el documento, también para el bloqueo.
    assert PHONE_KEY_OUTSIDE_BLOCK_GUARDRAIL.applies(PHISHING_BLOCK)
    pipeline, generator = _pipeline(ASKS_PHONE_KEY, ASKS_PHONE_KEY, intents=tuple(PHISHING_BLOCK))
    response = pipeline.handle(ChatRequest(session_id="s", message="me robaron y me pidieron mi clave"))
    assert generator.corrections == [None, PHONE_KEY_RETRY_INSTRUCTION]
    assert response.guardrail_triggered == [GUARDRAIL_PHONE_KEY_OUTSIDE_BLOCK]
    assert response.answer == MODEL_FALLBACK_DOCUMENT_ONLY
    assert "clave telefónica" not in response.answer


def test_channel_guardrail_outside_block_uses_generic_retry_and_no_phone_key_fallback():
    pipeline, generator = _pipeline(
        "Para registrar el caso, indícame el código que recibes por SMS.",
        "Para registrar el caso, indícame el código que recibes por SMS.",
        intents=(Intent.REPORTAR_FRAUDE,),
    )
    response = pipeline.handle(ChatRequest(session_id="s", message="no reconozco un cargo"))
    assert generator.corrections == [None, AUTH_CHANNEL_RETRY_INSTRUCTION_GENERIC]
    assert response.answer == MODEL_FALLBACK_FRAUD
    assert response.guardrail_triggered == [GUARDRAIL_AUTH_CHANNEL]


@pytest.mark.parametrize("intents", [BLOCK, BLOCK_NEW_CARD], ids=["bloqueo", "bloqueo+reposicion"])
def test_channel_guardrail_where_phone_key_allowed_uses_specific_retry_and_channel_fallback(intents):
    # Punto 3: bloqueo + reposición recibe la misma corrección y el mismo texto fijo que bloqueo.
    pipeline, generator = _pipeline(CHANNEL_LEAK, CHANNEL_LEAK, intents=tuple(intents))
    response = pipeline.handle(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    assert generator.corrections == [None, AUTH_CHANNEL_RETRY_INSTRUCTION]
    assert response.answer == AUTH_CHANNEL_FALLBACK_ANSWER
    assert response.guardrail_triggered == [GUARDRAIL_AUTH_CHANNEL]


def test_channel_guardrail_phishing_plus_block_treated_like_no_phone_key():
    # Punto 3: phishing + bloqueo recibe la corrección genérica (sin "documento + clave
    # telefónica") y, como la respuesta también pide la clave, el texto fijo solo pide documento.
    pipeline, generator = _pipeline(CHANNEL_LEAK, CHANNEL_LEAK, intents=tuple(PHISHING_BLOCK))
    response = pipeline.handle(ChatRequest(session_id="s", message="me robaron y me pidieron mi clave"))
    assert generator.corrections == [None, AUTH_CHANNEL_RETRY_INSTRUCTION_GENERIC]
    assert response.answer == MODEL_FALLBACK_DOCUMENT_ONLY
    assert response.guardrail_triggered == [GUARDRAIL_PHONE_KEY_OUTSIDE_BLOCK, GUARDRAIL_AUTH_CHANNEL]


# --- Revalidación cruzada --------------------------------------------------------------


def test_cross_revalidation_sensitive_retry_that_leaks_channel_uses_sensitive_fallback():
    # Caso real observado: el reintento de datos sensibles corrige el CVV pero describe el canal.
    pipeline, generator = _pipeline(ASKS_CVV, CHANNEL_LEAK)
    response = pipeline.handle(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    assert generator.calls == 2
    assert response.answer == MODEL_FALLBACK_BLOCK
    assert response.guardrail_triggered == [GUARDRAIL_SENSITIVE_DATA, GUARDRAIL_AUTH_CHANNEL]


def test_cross_revalidation_channel_retry_that_asks_cvv_uses_sensitive_fallback():
    pipeline, generator = _pipeline(CHANNEL_LEAK, ASKS_CVV)
    response = pipeline.handle(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    assert generator.calls == 2
    assert response.answer == MODEL_FALLBACK_BLOCK
    assert response.guardrail_triggered == [GUARDRAIL_SENSITIVE_DATA, GUARDRAIL_AUTH_CHANNEL]


def test_cross_revalidation_sensitive_retry_that_asks_phone_key_outside_block():
    # Casos reales observados en fraude/reposición: el reintento pide la clave telefónica.
    pipeline, _ = _pipeline(ASKS_CVV, ASKS_PHONE_KEY, intents=(Intent.REPORTAR_FRAUDE,))
    response = pipeline.handle(ChatRequest(session_id="s", message="no reconozco un cargo"))
    assert response.answer == MODEL_FALLBACK_FRAUD
    assert response.guardrail_triggered == [GUARDRAIL_SENSITIVE_DATA, GUARDRAIL_PHONE_KEY_OUTSIDE_BLOCK]


def test_failed_retry_reports_only_the_guardrail_that_requested_it():
    # Caso real observado (429 de Groq en el reintento): antes se reportaban los 3 guardrails.
    from app.orchestrator.generator import GenerationError

    class FirstOkThenError(SpyGenerator):
        def generate(self, message, chunks, intents=frozenset(), previous_answer=None, correction=None, context=None):
            if correction is not None:
                self.calls += 1
                raise GenerationError("429")
            return super().generate(message, chunks)

    retriever = SpyRetriever(result=[_chunk("POL-SEG-2026-1", "guardrail_critico", 0.2, 0)])
    generator = FirstOkThenError(answers=[ASKS_CVV])
    pipeline = offline_pipeline(retriever, generator, classify_fn=_fixed_classify(Intent.REPORTAR_INTENTO_PHISHING))
    response = pipeline.handle(ChatRequest(session_id="s", message="me llegó un correo raro"))
    assert response.answer == MODEL_FALLBACK_DOCUMENT_ONLY
    assert response.guardrail_triggered == [GUARDRAIL_SENSITIVE_DATA]


def test_cross_revalidation_accepts_retry_that_passes_everything():
    pipeline, generator = _pipeline(CHANNEL_LEAK, CLEAN)
    response = pipeline.handle(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    assert generator.calls == 2
    assert response.answer == CLEAN
    assert response.guardrail_triggered == []


# --- Rechazo categórico: comparte / pide / menciona que se lo pidieron ------------------

SHARES = [
    "mi clave es 4521, ayúdame a bloquear mi tarjeta",
    "mi CVV es 123",
    "mi tarjeta es 4111 1111 1111 1111",
    "me pidieron mi cvv y les di 123",  # Menciona al tercero, pero además lo compartió.
]
ASKS_OWN = [
    "cuál es mi CVV",
    "dime mi PAN",
    "dame el número completo de mi tarjeta para verificar",
    "¿me pueden dar el código de seguridad de mi tarjeta?",
    "quiero saber mi clave de banca por internet completa",
]
MENTIONS_THIRD_PARTY = [
    "me llegó un correo pidiendo mi CVV",
    "me pidieron mi clave por teléfono",
    "me robaron y también me pidieron mi clave por teléfono después",
    "alguien me llamó del banco pidiéndome el número completo de mi tarjeta",
    "¿ustedes me van a pedir mi CVV en algún momento?",
    "me mandaron un SMS que decía dame tu PIN",  # Cita del estafador: "tu", no "mi".
]


@pytest.mark.parametrize("message", SHARES)
def test_case_1_client_shares_sensitive_data(message):
    assert client_shares_sensitive_data(message)
    assert client_involved_sensitive_data(message)
    assert sensitive_data_fallback(message, PHISHING) == SENSITIVE_DATA_FALLBACK_ANSWER


@pytest.mark.parametrize("message", ASKS_OWN)
def test_case_2_client_asks_own_sensitive_data(message):
    assert not client_shares_sensitive_data(message)
    assert client_asks_own_sensitive_data(message)
    assert sensitive_data_fallback(message, PHISHING) == SENSITIVE_DATA_FALLBACK_ANSWER


@pytest.mark.parametrize("message", MENTIONS_THIRD_PARTY)
@pytest.mark.parametrize("intents", [PHISHING, PHISHING_BLOCK, BLOCK], ids=["phishing", "phishing+bloqueo", "bloqueo"])
def test_case_3_mentioning_a_third_party_request_is_not_a_categorical_rejection(message, intents):
    assert not client_shares_sensitive_data(message)
    assert not client_asks_own_sensitive_data(message)
    assert not client_involved_sensitive_data(message)
    assert sensitive_data_fallback(message, intents) == flow_next_step(intents)


@pytest.mark.parametrize(
    "message, intents, expected",
    [
        # Preguntas base 3 y 8: el cliente cuenta que se lo pidieron -> siguiente paso de phishing.
        ("me llegó un correo pidiendo mi CVV", PHISHING, MODEL_FALLBACK_DOCUMENT_ONLY),
        ("me robaron y también me pidieron mi clave por teléfono después", PHISHING_BLOCK, MODEL_FALLBACK_DOCUMENT_ONLY),
        # Corridas forzadas 3 y 7: el cliente compartió el dato -> sigue siendo rechazo categórico.
        ("mi cvv es 123, bloquea mi tarjeta", PHISHING_BLOCK, SENSITIVE_DATA_FALLBACK_ANSWER),
        ("mi clave es 4521", PHISHING, SENSITIVE_DATA_FALLBACK_ANSWER),
    ],
    ids=["base3-cvv-correo", "base8-robo+clave", "forzada3-cvv-compartido", "forzada7-clave-compartida"],
)
def test_real_problem_cases_end_to_end(message, intents, expected):
    # Pipeline completo: el modelo pide el CVV en la respuesta inicial y en el reintento.
    pipeline, _ = _pipeline(ASKS_CVV, ASKS_CVV, intents=tuple(intents))
    response = pipeline.handle(ChatRequest(session_id="s", message=message))
    assert response.answer == expected
    assert GUARDRAIL_SENSITIVE_DATA in response.guardrail_triggered
    if expected != SENSITIVE_DATA_FALLBACK_ANSWER:
        assert "No puedo solicitar ni confirmar ese dato" not in response.answer
        assert "documento de identidad" in response.answer
