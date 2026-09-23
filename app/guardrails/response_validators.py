"""Validaciones de código sobre la respuesta generada por el LLM.

Son el respaldo estructural de las reglas del prompt (app/orchestrator/generator.py):
el prompt es la primera línea de defensa y estas validaciones atrapan lo que se le escapa.
"""

import logging
import re
import threading
import unicodedata

logger = logging.getLogger(__name__)

GUARDRAIL_AUTH_CHANNEL = "auth_channel_disclosure"

# Descripciones de cómo o por dónde llega un dato de autenticación (prohibido por
# AUTH_DATA_RULE). Se evalúa sobre texto en minúsculas y sin tildes.
DESCRIBES_AUTH_CHANNEL = re.compile(
    r"\bsms\b|mensaje de texto|por mensaje|por llamada|llamada de seguridad|por correo"
    r"|que (?:recibe|recibes|le llega|te llega|le enviamos|te enviamos)"
)

AUTH_CHANNEL_RETRY_INSTRUCTION = (
    "Tu respuesta anterior mencionó cómo o por dónde se recibe la clave telefónica. Eso está "
    "prohibido. Vuelve a responder pidiendo únicamente el documento de identidad y la clave "
    "telefónica por su nombre, sin ninguna mención de SMS, llamada, correo, ni número registrado."
)

# Texto fijo (no generado). Solo es correcto según la política para el bloqueo estándar
# (POL-BLQ-2026-1); por eso el guardrail se aplica únicamente al intent bloquear_tarjeta.
AUTH_CHANNEL_FALLBACK_ANSWER = "Para continuar, necesito tu documento de identidad y tu clave telefónica."


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def describes_auth_channel(answer: str) -> bool:
    return bool(DESCRIBES_AUTH_CHANNEL.search(normalize(answer)))


class GuardrailStats:
    """Contadores en memoria del proceso para dar visibilidad en logs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.checks = 0
        self.retries = 0
        self.fallbacks = 0

    def record_check(self) -> None:
        with self._lock:
            self.checks += 1

    def record_retry(self) -> None:
        with self._lock:
            self.retries += 1
            logger.warning(
                "Guardrail %s: respuesta describe el canal de autenticación; reintentando "
                "(reintentos=%d, fallbacks=%d, validaciones=%d)",
                GUARDRAIL_AUTH_CHANNEL, self.retries, self.fallbacks, self.checks,
            )

    def record_fallback(self, reason: str) -> None:
        with self._lock:
            self.fallbacks += 1
            logger.warning(
                "Guardrail %s: se usa el texto fijo (%s) (reintentos=%d, fallbacks=%d, validaciones=%d)",
                GUARDRAIL_AUTH_CHANNEL, reason, self.retries, self.fallbacks, self.checks,
            )


auth_channel_stats = GuardrailStats()
