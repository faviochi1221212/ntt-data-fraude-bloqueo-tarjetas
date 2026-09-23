from functools import lru_cache

from fastapi import APIRouter, Depends

from app.models.schemas import ChatRequest, ChatResponse
from app.orchestrator.classifier import GroqClassifier, LLMClassifierError
from app.orchestrator.generator import GroqGenerator
from app.orchestrator.pipeline import ChatPipeline
from app.rag.retriever import build_retriever

router = APIRouter(tags=["chat"])


@lru_cache
def get_pipeline() -> ChatPipeline:
    """Construye el pipeline una sola vez (el índice de embeddings se carga en el primer request)."""
    try:
        classifier_llm = GroqClassifier()
    except LLMClassifierError:
        # classify() reintenta construirlo y reporta el motivo en llm_error.
        classifier_llm = None
    return ChatPipeline(
        retriever=build_retriever(),
        generator=GroqGenerator(),
        classifier_llm=classifier_llm,
    )


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, pipeline: ChatPipeline = Depends(get_pipeline)) -> ChatResponse:
    return pipeline.handle(request)
