from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException

from app.core.config import get_settings
from app.guardrails.faithfulness import FaithfulnessVerifierError, GroqFaithfulnessVerifier
from app.guardrails.response_validators import mask_sensitive_data
from app.models.schemas import ChatRequest, ChatResponse, ChatSessionDetail, ChatSessionSummary
from app.orchestrator.classifier import GroqClassifier, LLMClassifierError
from app.orchestrator.generator import GroqGenerator
from app.orchestrator.pipeline import ChatPipeline
from app.rag.retriever import build_retriever
from app.storage.chat_store import ChatStore

router = APIRouter(tags=["chat"])


@lru_cache
def get_pipeline() -> ChatPipeline:
    """Construye el pipeline una sola vez (el índice de embeddings se carga en el primer request)."""
    try:
        classifier_llm = GroqClassifier()
    except LLMClassifierError:
        # classify() reintenta construirlo y reporta el motivo en llm_error.
        classifier_llm = None
    try:
        verifier = GroqFaithfulnessVerifier()
    except FaithfulnessVerifierError:
        # Sin key la generación tampoco funciona; el pipeline responde con su fallback.
        verifier = None
    return ChatPipeline(
        retriever=build_retriever(),
        generator=GroqGenerator(),
        classifier_llm=classifier_llm,
        verifier=verifier,
        history_limit=get_settings().history_context_messages,
    )


@lru_cache
def get_chat_store() -> ChatStore:
    return ChatStore(get_settings().database_path)


@router.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    pipeline: ChatPipeline = Depends(get_pipeline),
    store: ChatStore = Depends(get_chat_store),
) -> ChatResponse:
    # El historial se recupera ANTES de procesar: decide si este turno ejecuta un bloqueo
    # pendiente y da contexto al classifier y al generador.
    history = store.history(request.session_id)
    result = pipeline.process(request, history)
    # El mensaje del cliente se guarda enmascarado: claves, CVV, tarjeta y documento no se persisten.
    store.append_turn(
        request.session_id,
        user_content=mask_sensitive_data(request.message),
        assistant_content=result.response.answer,
        assistant_metadata=result.assistant_metadata(),
    )
    return result.response


@router.get("/chats", response_model=list[ChatSessionSummary])
def list_chats(store: ChatStore = Depends(get_chat_store)) -> list[ChatSessionSummary]:
    """Sesiones guardadas, la más reciente primero."""
    return store.list_sessions()


@router.get("/chats/{session_id}", response_model=ChatSessionDetail)
def get_chat(session_id: str, store: ChatStore = Depends(get_chat_store)) -> ChatSessionDetail:
    """Historial completo de una sesión, para cargarla y continuarla desde el frontend."""
    if not store.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    return ChatSessionDetail(session_id=session_id, messages=store.history(session_id))
