"""Consistencia de metadatos de la KB.

"tipo" es el único campo que decide si un chunk es obligatorio en search_by_intent();
"zona" es solo temática. Este test evita que un chunk quede en una zona de guardrail
con un tipo no obligatorio (y por lo tanto recuperable solo por score).
"""

from app.models.schemas import Chunk
from app.rag.loader import load_chunks
from app.rag.retriever import MANDATORY_TIPOS, priority_of

GUARDRAIL_ZONA_KEYWORDS = ("guardrail", "guardrail_critico", "critico")


def _violations(chunks: list[Chunk]) -> list[str]:
    violations = []
    for chunk in chunks:
        zona = str(chunk.metadatos.get("zona", "")).lower()
        tipo = chunk.metadatos.get("tipo")
        if any(word in zona for word in GUARDRAIL_ZONA_KEYWORDS) and tipo not in MANDATORY_TIPOS:
            violations.append(f"{chunk.chunk_id}: zona={zona!r} pero tipo={tipo!r} no es obligatorio")
    return violations


def test_guardrail_zones_have_mandatory_tipo():
    chunks = load_chunks()
    assert len(chunks) == 16
    assert _violations(chunks) == []


def test_golden_security_rule_is_priority_0():
    # POL-SEG-2026-1 (nunca pedir CVV/clave/PAN) debe ir siempre primero, sin depender
    # del score semántico, que la subestima frente a relatos de phishing.
    golden = next(c for c in load_chunks() if c.chunk_id == "POL-SEG-2026-1")
    assert golden.metadatos["tipo"] == "guardrail_critico"
    assert priority_of(golden) == 0


def test_detects_guardrail_zone_with_non_mandatory_tipo():
    bad = Chunk(
        chunk_id="POL-TEST-2026-1",
        titulo="t",
        intent="reportar_intento_phishing",
        contenido="c",
        source_policy="POL-TEST-2026",
        version="2026",
        metadatos={"zona": "guardrail_critico", "tipo": "informativo"},
    )
    assert _violations([bad]) == [
        "POL-TEST-2026-1: zona='guardrail_critico' pero tipo='informativo' no es obligatorio"
    ]
