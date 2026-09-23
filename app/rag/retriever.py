"""Índice de recuperación semántica en memoria (similitud coseno)."""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from app.core.config import Settings, get_settings
from app.models.schemas import Chunk
from app.rag.loader import load_chunks


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: Chunk
    score: float


class Retriever:
    """Embebe el campo `contenido` de cada chunk y busca por similitud coseno.

    Los embeddings se guardan normalizados, así el coseno es un producto punto.
    """

    def __init__(self, chunks: list[Chunk], settings: Optional[Settings] = None):
        if not chunks:
            raise ValueError("Retriever requiere al menos un chunk")
        self._settings = settings or get_settings()
        if not self._settings.embedding_model:
            raise ValueError("EMBEDDING_MODEL no está configurado")

        # Import diferido: cargar onnxruntime solo cuando realmente se construye el índice.
        from fastembed import TextEmbedding

        self._model = TextEmbedding(self._settings.embedding_model)
        self._chunks = chunks
        self._matrix = self._embed(
            [self._settings.embedding_document_prefix + c.contenido for c in chunks]
        )

    def _embed(self, texts: list[str]) -> np.ndarray:
        vectors = np.array(list(self._model.embed(texts)), dtype=np.float32)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        min_score: Optional[float] = None,
    ) -> list[RetrievedChunk]:
        k = self._settings.rag_top_k if top_k is None else top_k
        threshold = self._settings.rag_min_score if min_score is None else min_score

        query_vec = self._embed([self._settings.embedding_query_prefix + query])[0]
        scores = self._matrix @ query_vec
        ranked = np.argsort(-scores)[:k]
        return [
            RetrievedChunk(chunk=self._chunks[i], score=float(scores[i]))
            for i in ranked
            if scores[i] >= threshold
        ]


def build_retriever(settings: Optional[Settings] = None) -> Retriever:
    settings = settings or get_settings()
    return Retriever(load_chunks(settings.kb_path), settings)
