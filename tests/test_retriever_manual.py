"""Prueba manual de clasificación + recuperación (no es un test automatizado).

Encadena: classifier (hard_triggers + Groq) -> retriever.search_by_intent().

Uso:
    .venv\\Scripts\\python -m tests.test_retriever_manual
    .venv\\Scripts\\python -m tests.test_retriever_manual "me robaron la tarjeta con violencia"
    .venv\\Scripts\\python -m tests.test_retriever_manual -i      (pide preguntas por consola)

Sin argumentos, corre las preguntas de tests/test_classifier_manual.py.
No define funciones test_*, así que pytest no ejecuta nada de este archivo.
"""

import logging
import sys


def _print_results(retriever, question: str) -> None:
    from app.orchestrator.classifier import classify

    result = classify(question)
    print(f'\nPregunta: "{question}"')

    intents = ", ".join(
        f"{i.intent.value} ({i.origin.value}"
        + (f" {i.confidence:.2f})" if i.confidence is not None else ")")
        for i in result.intents
    )
    print(f"  Intents: {intents or '(ninguno)'}")
    if result.llm_error:
        print(f"  [error groq] {result.llm_error}")

    chunks = retriever.search_by_intent(question, result.intents)
    if not chunks:
        print("  Chunks: (ninguno)")
    for rank, r in enumerate(chunks, start=1):
        kind = f"N{r.priority} " + ("obligatorio" if r.mandatory else "por score  ")
        tipo = r.chunk.metadatos.get("tipo", "-")
        print(
            f"  {rank}. {r.chunk.chunk_id:<16} {kind} score={r.score:.4f}  "
            f"[{r.chunk.intent} / {tipo}] {r.chunk.titulo}"
        )


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="  [%(levelname)s %(name)s] %(message)s")

    from app.rag.retriever import build_retriever
    from tests.test_classifier_manual import QUESTIONS

    print("Construyendo índice...")
    retriever = build_retriever()

    if sys.argv[1:] == ["-i"]:
        while True:
            question = input("\nPregunta (Enter para salir): ").strip()
            if not question:
                break
            _print_results(retriever, question)
        return

    for question in sys.argv[1:] or QUESTIONS:
        _print_results(retriever, question)


if __name__ == "__main__":
    main()
