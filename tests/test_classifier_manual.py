"""Prueba manual del classifier (no es un test automatizado).

Uso:
    .venv\\Scripts\\python -m tests.test_classifier_manual
    .venv\\Scripts\\python -m tests.test_classifier_manual "otra pregunta"

Sin argumentos, corre las preguntas de QUESTIONS.
No define funciones test_*, así que pytest no ejecuta nada de este archivo.
"""

import logging
import sys

QUESTIONS = [
    "me robaron la tarjeta con violencia",
    "cuándo me devuelven el dinero de un cargo que no reconozco",
    "me llegó un correo pidiendo mi CVV",
    "quiero desbloquear mi tarjeta",
    "no sé si esto es fraude pero alguien me llamó del banco pidiéndome datos",
    "perdí mi tarjeta, ¿me pueden desactivar?",
    "quiero que me quiten el bloqueo que le pusieron a mi tarjeta",
    "me robaron y también me pidieron mi clave por teléfono después",
    "hola",
]


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="   [%(levelname)s %(name)s] %(message)s")

    from app.core.config import get_settings
    from app.orchestrator.classifier import GroqClassifier, LLMClassifierError, classify

    try:
        llm = GroqClassifier()
        print(f"Modelo Groq: {get_settings().groq_model}")
    except LLMClassifierError as exc:
        llm = None
        print(f"[AVISO] Capa Groq no disponible: {exc}. Solo se muestran hard_triggers.")

    for n, question in enumerate(sys.argv[1:] or QUESTIONS, start=1):
        result = classify(question, llm=llm)
        print(f'\n{n}. "{question}"')
        if not result.intents:
            print("   (sin intents)")
        for item in result.intents:
            conf = f"confidence={item.confidence:.2f}" if item.confidence is not None else "confidence=-"
            print(f"   - {item.intent.value:<26} origen={item.origin.value:<12} {conf}")
        for warning in result.llm_warnings:
            print(f"   [warning groq] {warning}")
        if result.llm_error and llm is not None:
            print(f"   [error groq] {result.llm_error}")


if __name__ == "__main__":
    main()
