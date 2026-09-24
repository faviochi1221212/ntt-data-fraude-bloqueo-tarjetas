"""Integración contra Groq real con el backend simulado forzado (force_result).

Flujo completo de bloqueo en dos turnos (recolección de identidad -> ejecución) y reporte de
fraude con transacción en hold. Se saltan si no hay GROQ_API_KEY. Pausan entre llamadas por el
límite de tokens por minuto de Groq.
"""

import functools
import time

import pytest
from fastapi.testclient import TestClient

from app.api.chat import get_chat_store, get_pipeline
from app.api.mock_backend import BlockStatus, simulate_block_request, simulate_transaction_status
from app.guardrails.action_validators import TRANSACTION_PENDING_HOLD, BlockStatusGuard, claims_formal_case
from app.main import app
from app.orchestrator.pipeline import ChatPipeline
from app.storage.chat_store import ChatStore
from tests.test_chat_endpoint import INTEGRATION_PAUSE_SECONDS, _normalize, requires_groq

IDENTITY_REPLY = "mi documento es 45871236, mi clave es 4521"


@pytest.fixture
def forced_client(tmp_path):
    """Cliente cuyo pipeline real usa el backend simulado con un resultado forzado."""
    real = get_pipeline()
    store = ChatStore(tmp_path / "chat.db")

    def make(block: str = "BLOCKED", transaction: str = "settled") -> TestClient:
        pipeline = ChatPipeline(
            real._retriever,
            real._generator,
            classifier_llm=real._classifier_llm,
            verifier=real._verifier,
            block_simulator=functools.partial(simulate_block_request, force_result=block),
            transaction_simulator=functools.partial(simulate_transaction_status, force_result=transaction),
        )
        app.dependency_overrides[get_pipeline] = lambda: pipeline
        app.dependency_overrides[get_chat_store] = lambda: store
        return TestClient(app)

    yield make
    app.dependency_overrides.pop(get_pipeline, None)
    app.dependency_overrides.pop(get_chat_store, None)


def _post(client: TestClient, session_id: str, message: str) -> dict:
    time.sleep(INTEGRATION_PAUSE_SECONDS)  # Límite de 8.000 tokens por minuto de Groq.
    response = client.post("/api/v1/chat", json={"session_id": session_id, "message": message})
    assert response.status_code == 200, response.text
    body = response.json()
    print(f"\n[{session_id}] {message!r}\n  action={body['action']}\n  guardrails={body['guardrail_triggered']}\n  answer={body['answer']}")
    return body


def _two_turn_block(client: TestClient, session_id: str) -> tuple[dict, dict]:
    turn_1 = _post(client, session_id, "perdí mi tarjeta, ¿me pueden desactivar?")
    assert turn_1["action"] is None, "el turno 1 solo recolecta identidad"
    turn_2 = _post(client, session_id, IDENTITY_REPLY)
    return turn_1, turn_2


@requires_groq
def test_live_block_failed_does_not_say_blocked(forced_client):
    _, turn_2 = _two_turn_block(forced_client(block="BLOCK_FAILED"), "live-failed")
    assert turn_2["action"]["status"] == "BLOCK_FAILED"
    assert turn_2["action"]["success"] is False
    assert not BlockStatusGuard(BlockStatus.BLOCK_FAILED).detect(turn_2["answer"]), turn_2["answer"]
    assert turn_2["requires_human"] is True
    assert "45871236" not in turn_2["answer"] and "4521" not in turn_2["answer"]


@requires_groq
def test_live_block_pending_says_in_process(forced_client):
    _, turn_2 = _two_turn_block(forced_client(block="BLOCK_PENDING"), "live-pending")
    assert turn_2["action"]["status"] == "BLOCK_PENDING"
    assert not BlockStatusGuard(BlockStatus.BLOCK_PENDING).detect(turn_2["answer"]), turn_2["answer"]
    assert "proceso" in _normalize(turn_2["answer"]), turn_2["answer"]


@requires_groq
def test_live_pending_transaction_has_no_formal_case_id(forced_client):
    body = _post(forced_client(transaction="pending"), "live-fraud", "no reconozco un cargo de 300 soles en mi tarjeta")
    assert body["action"]["status"] == "pending"
    assert TRANSACTION_PENDING_HOLD in body["guardrail_triggered"]
    assert not claims_formal_case(body["answer"]), body["answer"]
