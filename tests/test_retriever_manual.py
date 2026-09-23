"""Prueba manual de recuperación (no es un test automatizado).

Uso:
    .venv\\Scripts\\python -m tests.test_retriever_manual
    .venv\\Scripts\\python -m tests.test_retriever_manual "me robaron la tarjeta con violencia"

Sin argumentos, pide preguntas por consola (línea vacía para salir).
No define funciones test_*, así que pytest no ejecuta nada de este archivo.
"""

import sys

TOP_N = 3


def _print_results(retriever, question: str) -> None:
    # min_score=-1.0 para mostrar siempre los 3 mejores, sin el filtro RAG_MIN_SCORE.
    results = retriever.search(question, top_k=TOP_N, min_score=-1.0)
    print(f'\nPregunta: "{question}"')
    for rank, r in enumerate(results, start=1):
        print(f"  {rank}. {r.chunk.chunk_id:<18} score={r.score:.4f}  {r.chunk.titulo}")


def main() -> None:
    from app.rag.retriever import build_retriever

    print("Construyendo índice...")
    retriever = build_retriever()

    if len(sys.argv) > 1:
        for question in sys.argv[1:]:
            _print_results(retriever, question)
        return

    while True:
        question = input("\nPregunta (Enter para salir): ").strip()
        if not question:
            break
        _print_results(retriever, question)


if __name__ == "__main__":
    main()
