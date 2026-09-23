"""Índice de recuperación semántica en memoria (similitud coseno)."""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from app.core.config import Settings, get_settings
from app.models.schemas import Chunk, ClassifiedIntent
from app.rag.loader import load_chunks

# Niveles de prioridad en search_by_intent(), según metadatos["tipo"]. "tipo" es el ÚNICO
# campo que decide prioridad y obligatoriedad; "zona" es solo temática
# (tests/test_kb_consistency.py lo verifica).
# Menor número = mayor prioridad. Los niveles 0 a 2 son obligatorios.
# - Nivel 0: prioridad absoluta; siempre antes que cualquier otro chunk.
# - Nivel 1: reglas de seguridad, después del nivel 0.
# - Nivel 2: reglas duras y excepciones de seguridad, después del nivel 1.
# - Nivel 3: cualquier otro tipo; entra solo por score (RAG_TOP_K / RAG_MIN_SCORE).
PRIORITY_BY_TIPO = {
    "guardrail_critico": 0,
    "regla_dura_seguridad": 1,
    "regla_dura": 2,
    "excepcion_seguridad": 2,
}
MANDATORY_TIPOS = frozenset(PRIORITY_BY_TIPO)
NON_MANDATORY_PRIORITY = 3


def priority_of(chunk: Chunk) -> int:
    return PRIORITY_BY_TIPO.get(chunk.metadatos.get("tipo"), NON_MANDATORY_PRIORITY)


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: Chunk
    score: float
    # 0 a 2: incluido por ser obligatorio; 3: incluido por score. Ver PRIORITY_BY_TIPO.
    priority: int = NON_MANDATORY_PRIORITY

    @property
    def mandatory(self) -> bool:
        return self.priority < NON_MANDATORY_PRIORITY


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

        scores = self._scores(query)
        ranked = np.argsort(-scores)[:k]
        return [
            RetrievedChunk(chunk=self._chunks[i], score=float(scores[i]))
            for i in ranked
            if scores[i] >= threshold
        ]

    def search_by_intent(
        self,
        query: str,
        intents: list[ClassifiedIntent],
        top_k: Optional[int] = None,
        min_score: Optional[float] = None,
    ) -> list[RetrievedChunk]:
        """Busca solo entre los chunks de los intents recibidos (unión).

        Qué chunks entran:
        - TODOS los chunks de esos intents con prioridad 0, 1 o 2 (sin top_k ni min_score).
        - Del resto (prioridad 3), los mejores por score, con top_k y min_score aplicados
          solo a esta parte.

        Orden: por nivel de prioridad (0 a 3) y, dentro de cada nivel, por score descendente.

        Si ningún chunk corresponde a los intents (p. ej. solo fuera_de_alcance), retorna [].
        """
        k = self._settings.rag_top_k if top_k is None else top_k
        threshold = self._settings.rag_min_score if min_score is None else min_score

        wanted = {item.intent.value for item in intents}
        candidates = [i for i, c in enumerate(self._chunks) if c.intent in wanted]
        if not candidates:
            return []

        scores = self._scores(query)
        results = [
            RetrievedChunk(chunk=self._chunks[i], score=float(scores[i]), priority=priority_of(self._chunks[i]))
            for i in candidates
        ]
        mandatory = [r for r in results if r.mandatory]
        by_score = sorted(
            (r for r in results if not r.mandatory and r.score >= threshold),
            key=lambda r: r.score,
            reverse=True,
        )[:k]

        # Por qué este orden: el LLM que redacta la respuesta tiende a darle más peso a lo que
        # lee primero, así que el orden debe reflejar relevancia real y no solo la categoría:
        # - Niveles 0 y 1 van antes por severidad, no por score: son reglas de seguridad que no
        #   pueden depender del embedding (p. ej. "nunca pedir CVV/clave" puntúa bajo frente a
        #   "me pidieron mi clave por teléfono", justo cuando es lo más urgente).
        # - Dentro del nivel 2 se ordena por score, no por tipo: así la excepción por robo con
        #   violencia queda antes que "bloqueo estándar" (que exige clave telefónica) cuando el
        #   relato es de robo con violencia.
        return sorted(mandatory + by_score, key=lambda r: (r.priority, -r.score))

    def _scores(self, query: str) -> np.ndarray:
        """Similitud coseno de la query contra todos los chunks."""
        query_vec = self._embed([self._settings.embedding_query_prefix + query])[0]
        return self._matrix @ query_vec


def build_retriever(settings: Optional[Settings] = None) -> Retriever:
    settings = settings or get_settings()
    return Retriever(load_chunks(settings.kb_path), settings)
