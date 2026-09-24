"""Backend simulado, flujo de bloqueo en dos turnos, transacción en hold y persistencia.
Tests offline: el backend se fija con force_result y el generador es un doble."""

import random
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from app.api.chat import get_chat_store, get_pipeline
from app.api.mock_backend import (
    BlockStatus,
    TransactionStatus,
    simulate_block_request,
    simulate_transaction_status,
)
from app.guardrails.action_validators import (
    BLOCK_STATUS_FALLBACK,
    GUARDRAIL_BLOCK_STATUS_MISMATCH,
    GUARDRAIL_FORMAL_CASE_WHILE_PENDING,
    GUARDRAIL_STATE_DROPPED_AFTER_RETRY,
    PENDING_TRANSACTION_FALLBACK,
    TRANSACTION_PENDING_HOLD,
    BlockStatusGuard,
    block_operation_fact,
    claims_formal_case,
    reports_operation_state,
)
from app.guardrails.faithfulness import GUARDRAIL_UNSUPPORTED_CLAIM
from app.guardrails.response_validators import MASKED_DATA, mask_sensitive_data
from app.main import app
from app.models.schemas import ChatRequest, Intent, StoredMessage
from app.orchestrator.generator import (
    _NO_ACTIONS_RULE,
    BLOCK_STATUS_RULES,
    TRANSACTION_PENDING_RULE,
    TurnContext,
    build_system_prompt,
)
from app.orchestrator.pipeline import AWAITING_IDENTITY_FOR_BLOCK, ChatPipeline, TurnResult
from app.storage.chat_store import ChatStore
from tests.test_chat_endpoint import SpyRetriever, _chunk, _fixed_classify
from tests.test_faithfulness_guardrail import FakeVerifier

# --- Mock ------------------------------------------------------------------------------


def test_force_result_makes_mocks_deterministic():
    for status in BlockStatus:
        assert simulate_block_request("card-1", force_result=status.value) == status
    for status in TransactionStatus:
        assert simulate_transaction_status("txn-1", force_result=status.value) == status


def test_mock_weights():
    rng = random.Random(42)
    blocks = Counter(simulate_block_request("c", rng=rng) for _ in range(10_000))
    assert BlockStatus.BLOCK_REQUESTED not in blocks, "peso 0: solo sale con force_result"
    assert 0.67 < blocks[BlockStatus.BLOCKED] / 10_000 < 0.73
    for status in (BlockStatus.BLOCK_PENDING, BlockStatus.BLOCK_FAILED, BlockStatus.ALREADY_BLOCKED):
        assert 0.08 < blocks[status] / 10_000 < 0.12
    txns = Counter(simulate_transaction_status("t", rng=rng) for _ in range(10_000))
    assert 0.77 < txns[TransactionStatus.SETTLED] / 10_000 < 0.83


# --- Doble de generador que registra el contexto --------------------------------------


class ContextGenerator:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.contexts: list[TurnContext] = []
        self.corrections: list = []

    def generate(self, message, chunks, intents=frozenset(), previous_answer=None, correction=None, context=None):
        self.contexts.append(context)
        self.corrections.append(correction)
        return self.answers[min(len(self.contexts), len(self.answers)) - 1]


class RecordingSimulator:
    def __init__(self, result):
        self.result, self.calls = result, []

    def __call__(self, entity_id):
        self.calls.append(entity_id)
        return self.result


ASKS_IDENTITY = "Para bloquear tu tarjeta necesito tu documento de identidad y tu clave telefónica."
CLAIMS_BLOCKED = "Listo, tu tarjeta ha sido bloqueada correctamente."
IDENTITY_REPLY = "mi documento es 45871236, mi clave es 4521"


def _block_pipeline(status, *answers, verifier=None):
    simulator = RecordingSimulator(status)
    generator = ContextGenerator(*answers)
    pipeline = ChatPipeline(
        SpyRetriever(result=[_chunk("POL-BLQ-2026-1", "regla_dura", 0.3, 2)]),
        generator,
        classify_fn=_fixed_classify(Intent.BLOQUEAR_TARJETA),
        verifier=verifier,
        block_simulator=simulator,
        transaction_simulator=RecordingSimulator(TransactionStatus.SETTLED),
    )
    return pipeline, generator, simulator


def _as_history(turn_message: str, result: TurnResult) -> list[StoredMessage]:
    return [
        StoredMessage(role="user", content=mask_sensitive_data(turn_message), timestamp="t1"),
        StoredMessage(role="assistant", content=result.response.answer, timestamp="t2", metadata=result.assistant_metadata()),
    ]


# --- Flujo de bloqueo en dos turnos ------------------------------------------------------


def test_turn_1_collects_identity_and_does_not_execute_block():
    pipeline, generator, simulator = _block_pipeline(BlockStatus.BLOCKED, ASKS_IDENTITY)
    result = pipeline.process(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    assert simulator.calls == []
    assert result.response.action is None
    assert result.awaiting_identity_for_block is True
    assert result.assistant_metadata()[AWAITING_IDENTITY_FOR_BLOCK] is True
    assert generator.contexts[0].block_status is None


def test_turn_2_executes_block_based_on_session_history():
    pipeline, generator, simulator = _block_pipeline(BlockStatus.BLOCKED, ASKS_IDENTITY, CLAIMS_BLOCKED)
    turn_1 = pipeline.process(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    turn_2 = pipeline.process(ChatRequest(session_id="s", message=IDENTITY_REPLY), _as_history("perdí mi tarjeta", turn_1))
    assert simulator.calls == ["card-s"]
    assert turn_2.response.action.name == "block_card"
    assert turn_2.response.action.status == "BLOCKED"
    assert turn_2.response.action.success is True
    assert turn_2.block_executed and not turn_2.awaiting_identity_for_block
    assert generator.contexts[1].block_status == BlockStatus.BLOCKED
    assert turn_2.response.answer == CLAIMS_BLOCKED


def test_identity_data_without_pending_request_does_not_execute():
    # Un número en el mensaje no basta: la ejecución la decide el historial.
    pipeline, _, simulator = _block_pipeline(BlockStatus.BLOCKED, ASKS_IDENTITY)
    result = pipeline.process(ChatRequest(session_id="s", message=IDENTITY_REPLY))
    assert simulator.calls == []
    assert result.response.action is None


def test_pending_request_without_identity_data_asks_again():
    pipeline, _, simulator = _block_pipeline(BlockStatus.BLOCKED, ASKS_IDENTITY)
    turn_1 = pipeline.process(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    turn_2 = pipeline.process(
        ChatRequest(session_id="s", message="¿para qué necesitan mis datos?"), _as_history("perdí mi tarjeta", turn_1)
    )
    assert simulator.calls == []
    assert turn_2.awaiting_identity_for_block is True


@pytest.mark.parametrize("status", list(BlockStatus), ids=[s.value for s in BlockStatus])
def test_answer_never_claims_block_unless_blocked(status):
    # El modelo afirma el bloqueo en la respuesta y en el reintento.
    pipeline, generator, _ = _block_pipeline(status, ASKS_IDENTITY, CLAIMS_BLOCKED, CLAIMS_BLOCKED)
    turn_1 = pipeline.process(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    turn_2 = pipeline.process(ChatRequest(session_id="s", message=IDENTITY_REPLY), _as_history("perdí mi tarjeta", turn_1))
    response = turn_2.response
    assert response.action.status == status.value
    if status == BlockStatus.BLOCKED:
        assert response.answer == CLAIMS_BLOCKED
        assert response.guardrail_triggered == []
    else:
        assert BlockStatusGuard(status).detect(CLAIMS_BLOCKED) or status == BlockStatus.ALREADY_BLOCKED
        if status != BlockStatus.ALREADY_BLOCKED:
            assert response.answer == BLOCK_STATUS_FALLBACK[status]
            assert response.guardrail_triggered == [GUARDRAIL_BLOCK_STATUS_MISMATCH]
            assert "bloqueada" not in response.answer.lower() or "no" in response.answer.lower()
        assert response.action.success is (status == BlockStatus.ALREADY_BLOCKED)
    assert response.requires_human is (status == BlockStatus.BLOCK_FAILED)


def test_already_blocked_rejects_new_operation_claims():
    pipeline, _, _ = _block_pipeline(BlockStatus.ALREADY_BLOCKED, ASKS_IDENTITY, "Hemos bloqueado tu tarjeta.", "Hemos bloqueado tu tarjeta.")
    turn_1 = pipeline.process(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    turn_2 = pipeline.process(ChatRequest(session_id="s", message=IDENTITY_REPLY), _as_history("perdí mi tarjeta", turn_1))
    assert turn_2.response.answer == BLOCK_STATUS_FALLBACK[BlockStatus.ALREADY_BLOCKED]
    assert turn_2.response.guardrail_triggered == [GUARDRAIL_BLOCK_STATUS_MISMATCH]


def test_block_state_fallback_does_not_ask_identity_again():
    # Tras ejecutar el bloqueo, un texto fijo de otro guardrail no debe volver a pedir datos:
    # se usa el texto del estado real.
    leaky = "Tu solicitud está en proceso; ingresa la clave telefónica que recibes por SMS."
    pipeline, _, _ = _block_pipeline(BlockStatus.BLOCK_PENDING, ASKS_IDENTITY, leaky, leaky)
    turn_1 = pipeline.process(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    turn_2 = pipeline.process(ChatRequest(session_id="s", message=IDENTITY_REPLY), _as_history("perdí mi tarjeta", turn_1))
    assert turn_2.response.answer == BLOCK_STATUS_FALLBACK[BlockStatus.BLOCK_PENDING]


def test_faithfulness_verifier_receives_real_block_status():
    # El guardrail de fidelidad es la segunda capa: recibe el estado real para poder marcar
    # una respuesta que lo contradiga.
    verifier = FakeVerifier()
    pipeline, _, _ = _block_pipeline(BlockStatus.BLOCK_FAILED, ASKS_IDENTITY, "Hubo un inconveniente; un asesor dará seguimiento.", verifier=verifier)
    turn_1 = pipeline.process(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    pipeline.process(ChatRequest(session_id="s", message=IDENTITY_REPLY), _as_history("perdí mi tarjeta", turn_1))
    assert any("BLOCK_FAILED" in fact for fact in verifier.facts[-1])


def test_faithfulness_flags_block_claim_that_contradicts_status():
    # Si el verificador marca la contradicción, el pipeline no entrega la respuesta.
    verifier = FakeVerifier({"La tarjeta ya no puede usarse.": ["Contradice BLOCK_PENDING: da el bloqueo por hecho."]})
    pipeline, _, _ = _block_pipeline(
        BlockStatus.BLOCK_PENDING, ASKS_IDENTITY, "La tarjeta ya no puede usarse.", "La tarjeta ya no puede usarse.",
        verifier=verifier,
    )
    turn_1 = pipeline.process(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    turn_2 = pipeline.process(ChatRequest(session_id="s", message=IDENTITY_REPLY), _as_history("perdí mi tarjeta", turn_1))
    assert turn_2.response.answer == BLOCK_STATUS_FALLBACK[BlockStatus.BLOCK_PENDING]
    assert "unsupported_claim" in turn_2.response.guardrail_triggered


@pytest.mark.parametrize("status", list(BlockStatus), ids=[s.value for s in BlockStatus])
def test_prompt_carries_block_status_and_drops_no_actions_rule(status):
    prompt = build_system_prompt([], frozenset({Intent.BLOQUEAR_TARJETA}), TurnContext(block_status=status))
    assert BLOCK_STATUS_RULES[status] in prompt
    assert _NO_ACTIONS_RULE not in prompt
    # Observado contra Groq: sin esta regla la respuesta decía "en proceso (BLOCK_PENDING)".
    assert "No menciones los códigos internos de estado" in prompt


# --- Transacción en hold (POL-FRD-2026-4) -----------------------------------------------

CLAIMS_FORMAL_CASE = "Hemos abierto un caso formal de disputa; su número de caso es CAS-2031."
PRELIMINARY = "Registramos tu reporte como preliminar; el caso formal se abrirá cuando la transacción se liquide."


def _fraud_pipeline(status, *answers):
    simulator = RecordingSimulator(status)
    pipeline = ChatPipeline(
        SpyRetriever(result=[_chunk("POL-FRD-2026-4", "regla_dura", 0.3, 2)]),
        ContextGenerator(*answers),
        classify_fn=_fixed_classify(Intent.REPORTAR_FRAUDE),
        block_simulator=RecordingSimulator(BlockStatus.BLOCKED),
        transaction_simulator=simulator,
    )
    return pipeline, simulator


def test_pending_transaction_never_gets_formal_case_id():
    pipeline, simulator = _fraud_pipeline(TransactionStatus.PENDING, CLAIMS_FORMAL_CASE, CLAIMS_FORMAL_CASE)
    response = pipeline.handle(ChatRequest(session_id="s", message="no reconozco un cargo de 300 soles"))
    assert simulator.calls == ["txn-s"]
    assert response.answer == PENDING_TRANSACTION_FALLBACK
    assert not claims_formal_case(response.answer)
    assert response.guardrail_triggered == [TRANSACTION_PENDING_HOLD, GUARDRAIL_FORMAL_CASE_WHILE_PENDING]
    assert response.action.name == "check_transaction_status" and response.action.status == "pending"


def test_pending_transaction_with_preliminary_answer_only_marks_hold():
    pipeline, _ = _fraud_pipeline(TransactionStatus.PENDING, PRELIMINARY)
    response = pipeline.handle(ChatRequest(session_id="s", message="no reconozco un cargo de 300 soles"))
    assert response.answer == PRELIMINARY
    assert response.guardrail_triggered == [TRANSACTION_PENDING_HOLD]


def test_settled_transaction_follows_normal_claim_flow():
    pipeline, _ = _fraud_pipeline(TransactionStatus.SETTLED, CLAIMS_FORMAL_CASE)
    response = pipeline.handle(ChatRequest(session_id="s", message="no reconozco un cargo de 300 soles"))
    assert response.answer == CLAIMS_FORMAL_CASE
    assert response.guardrail_triggered == []
    assert response.action.status == "settled"


def test_pending_prompt_forbids_inventing_settlement_time():
    prompt = build_system_prompt([], frozenset({Intent.REPORTAR_FRAUDE}), TurnContext(transaction_status=TransactionStatus.PENDING))
    assert TRANSACTION_PENDING_RULE in prompt
    assert "no des ninguna cifra" in prompt


def test_non_fraud_intents_do_not_query_transaction():
    pipeline, _, _ = _block_pipeline(BlockStatus.BLOCKED, ASKS_IDENTITY)
    pipeline.process(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    assert pipeline._transaction_simulator.calls == []


# --- Persistencia y endpoints de listado -------------------------------------------------


def test_masking_before_persisting():
    masked = mask_sensitive_data("mi documento es 45871236, mi clave es 4521 y me cobraron 300 soles")
    assert "45871236" not in masked and "4521" not in masked
    assert "300 soles" in masked and MASKED_DATA in masked


@pytest.fixture
def store(tmp_path):
    return ChatStore(tmp_path / "chat.db")


@pytest.fixture
def client(store):
    app.dependency_overrides[get_chat_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.pop(get_chat_store, None)


def test_list_chats_most_recent_first_with_truncated_summary(store, client):
    store.append_turn("old", "perdí mi tarjeta", "respuesta 1")
    long_message = "me llegó un correo muy raro que decía ser del banco y me pedía mis datos"
    store.append_turn("new", long_message, "respuesta 2")
    body = client.get("/api/v1/chats").json()
    assert [s["session_id"] for s in body] == ["new", "old"]
    assert body[0]["summary"].endswith("…") and len(body[0]["summary"]) <= 50
    assert body[1]["summary"] == "perdí mi tarjeta"
    assert body[0]["last_message_at"] >= body[1]["last_message_at"]


def test_list_chats_orders_by_last_message_not_creation(store, client):
    store.append_turn("a", "primero", "r")
    store.append_turn("b", "segundo", "r")
    store.append_turn("a", "a sigue", "r")  # "a" tiene ahora el mensaje más reciente.
    assert [s["session_id"] for s in client.get("/api/v1/chats").json()] == ["a", "b"]


def test_get_chat_returns_full_history(store, client):
    store.append_turn("s1", "perdí mi tarjeta", "pide datos", assistant_metadata={"intents": ["bloquear_tarjeta"]})
    store.append_turn("s1", f"mi documento es {MASKED_DATA}", "bloqueada", assistant_metadata={"action": {"status": "BLOCKED"}})
    body = client.get("/api/v1/chats/s1").json()
    assert body["session_id"] == "s1"
    assert [(m["role"], m["content"]) for m in body["messages"]] == [
        ("user", "perdí mi tarjeta"),
        ("assistant", "pide datos"),
        ("user", f"mi documento es {MASKED_DATA}"),
        ("assistant", "bloqueada"),
    ]
    assert body["messages"][1]["metadata"]["intents"] == ["bloquear_tarjeta"]


def test_get_unknown_chat_is_404(client):
    assert client.get("/api/v1/chats/no-existe").status_code == 404


def test_post_chat_persists_masked_turn_and_uses_history(store, client):
    pipeline, _, simulator = _block_pipeline(BlockStatus.BLOCK_PENDING, ASKS_IDENTITY, "Tu bloqueo está en proceso.")
    app.dependency_overrides[get_pipeline] = lambda: pipeline
    try:
        first = client.post("/api/v1/chat", json={"session_id": "flow", "message": "perdí mi tarjeta"}).json()
        second = client.post("/api/v1/chat", json={"session_id": "flow", "message": IDENTITY_REPLY}).json()
    finally:
        app.dependency_overrides.pop(get_pipeline, None)
    assert first["action"] is None
    assert second["action"]["status"] == "BLOCK_PENDING"
    assert simulator.calls == ["card-flow"]
    history = store.history("flow")
    assert [m.role for m in history] == ["user", "assistant", "user", "assistant"]
    assert "45871236" not in history[2].content and "4521" not in history[2].content
    assert history[1].metadata[AWAITING_IDENTITY_FOR_BLOCK] is True
    assert history[3].metadata["action"]["status"] == "BLOCK_PENDING"


# --- BLOCK_PENDING: operación nueva vs. repetida y reintento sin estado ----------------
# Caso real (integración contra Groq): en el turno 2 con BLOCK_PENDING, el verificador aplicó
# la regla de idempotencia a una primera solicitud y marcó la respuesta; el reintento quedó en
# "¿Hay algo más en lo que pueda ayudarte?", pasó todos los guardrails y se entregó.

STARTS_BLOCK = "Gracias. Iniciaremos el proceso de bloqueo de tu tarjeta."
VACUOUS_RETRY = "¿Hay algo más en lo que pueda ayudarte?"
PENDING_OK = "Tu solicitud de bloqueo está en proceso; te avisaremos cuando sea efectivo."
IDEMPOTENCY_CLAIM = ["Aplica la regla de idempotencia: una segunda solicitud no genera una nueva operación."]


def _block_turn_2(status, *answers, verifier=None):
    pipeline, generator, _ = _block_pipeline(status, ASKS_IDENTITY, *answers, verifier=verifier)
    turn_1 = pipeline.process(ChatRequest(session_id="s", message="perdí mi tarjeta"))
    turn_2 = pipeline.process(ChatRequest(session_id="s", message=IDENTITY_REPLY), _as_history("perdí mi tarjeta", turn_1))
    return turn_2.response, generator


@pytest.mark.parametrize(
    "status", [s for s in BlockStatus if s != BlockStatus.ALREADY_BLOCKED], ids=lambda s: s.value
)
def test_verifier_is_told_block_is_a_new_operation(status):
    verifier = FakeVerifier()
    _block_turn_2(status, PENDING_OK, verifier=verifier)
    facts = verifier.facts[-1]
    assert block_operation_fact(status) in facts
    assert "operación nueva, no una solicitud repetida" in block_operation_fact(status)
    assert "idempotencia" in block_operation_fact(status) and "no aplican" in block_operation_fact(status)


def test_verifier_is_told_already_blocked_is_a_repeated_request():
    verifier = FakeVerifier()
    _block_turn_2(BlockStatus.ALREADY_BLOCKED, "Tu tarjeta ya se encontraba bloqueada.", verifier=verifier)
    fact = block_operation_fact(BlockStatus.ALREADY_BLOCKED)
    assert fact in verifier.facts[-1]
    assert "solicitud repetida" in fact and "no genera una operación nueva" in fact


def test_retry_that_drops_block_state_falls_back_to_state_text():
    verifier = FakeVerifier(unsupported={STARTS_BLOCK: IDEMPOTENCY_CLAIM})
    response, generator = _block_turn_2(BlockStatus.BLOCK_PENDING, STARTS_BLOCK, VACUOUS_RETRY, verifier=verifier)
    assert generator.corrections[-1] is not None, "hubo reintento"
    assert response.answer == BLOCK_STATUS_FALLBACK[BlockStatus.BLOCK_PENDING]
    assert response.guardrail_triggered == [GUARDRAIL_UNSUPPORTED_CLAIM, GUARDRAIL_STATE_DROPPED_AFTER_RETRY]
    assert response.action.status == "BLOCK_PENDING"


def test_retry_that_keeps_block_state_is_accepted():
    verifier = FakeVerifier(unsupported={STARTS_BLOCK: IDEMPOTENCY_CLAIM})
    response, _ = _block_turn_2(BlockStatus.BLOCK_PENDING, STARTS_BLOCK, PENDING_OK, verifier=verifier)
    assert response.answer == PENDING_OK
    assert response.guardrail_triggered == []


def test_retry_that_drops_pending_transaction_state_falls_back():
    pipeline, _ = _fraud_pipeline(TransactionStatus.PENDING, CLAIMS_FORMAL_CASE, VACUOUS_RETRY)
    response = pipeline.handle(ChatRequest(session_id="s", message="no reconozco un cargo de 300 soles"))
    assert response.answer == PENDING_TRANSACTION_FALLBACK
    assert response.guardrail_triggered == [
        TRANSACTION_PENDING_HOLD,
        GUARDRAIL_FORMAL_CASE_WHILE_PENDING,
        GUARDRAIL_STATE_DROPPED_AFTER_RETRY,
    ]


def test_vacuous_retry_without_operation_is_not_affected():
    # Sin operación en el turno no hay estado que informar: la red de seguridad no aplica.
    verifier = FakeVerifier(unsupported={STARTS_BLOCK: IDEMPOTENCY_CLAIM})
    pipeline, _, simulator = _block_pipeline(BlockStatus.BLOCKED, STARTS_BLOCK, VACUOUS_RETRY, verifier=verifier)
    response = pipeline.process(ChatRequest(session_id="s", message="perdí mi tarjeta")).response
    assert simulator.calls == []
    assert response.answer == VACUOUS_RETRY
    assert response.guardrail_triggered == []


@pytest.mark.parametrize(
    "answer, block, txn, expected",
    [
        (VACUOUS_RETRY, BlockStatus.BLOCK_PENDING, None, False),
        (PENDING_OK, BlockStatus.BLOCK_PENDING, None, True),
        ("Tu tarjeta ya se encontraba BLOQUEADA.", BlockStatus.ALREADY_BLOCKED, None, True),
        (VACUOUS_RETRY, None, TransactionStatus.PENDING, False),
        ("El reporte queda como preliminar.", None, TransactionStatus.PENDING, True),
        ("La transacción aún no se liquida.", None, TransactionStatus.PENDING, True),
        (VACUOUS_RETRY, None, TransactionStatus.SETTLED, True),
        (VACUOUS_RETRY, None, None, True),
    ],
)
def test_reports_operation_state(answer, block, txn, expected):
    assert reports_operation_state(answer, block, txn) is expected
