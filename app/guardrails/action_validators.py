"""Guardrails que dependen del estado devuelto por el backend (simulado) en ese turno.

Se crean por petición, porque dependen del estado real de la operación:

- block_status_mismatch: la respuesta no puede afirmar un bloqueo que el backend no confirmó
  (BLOCK_REQUESTED, BLOCK_PENDING, BLOCK_FAILED), ni presentar como operación nueva un
  bloqueo que ya existía (ALREADY_BLOCKED).
- formal_case_while_pending (POL-FRD-2026-4): con la transacción en estado pending, la
  respuesta no puede afirmar que se abrió un caso formal ni dar un número de caso.
- state_dropped_after_retry: red de seguridad del pipeline, no un guardrail con reintento. Si
  en el turno hubo una operación y el reintento de cualquier guardrail ya no informa su estado
  (p. ej. queda solo "¿Hay algo más en lo que pueda ayudarte?"), se usa el texto fijo del estado.

Mismo mecanismo que los demás guardrails de salida (ResponseGuardrail): un reintento con
corrección y, si falla, un texto fijo.
"""

import re
from typing import Optional

from app.api.mock_backend import BlockStatus, TransactionStatus
from app.guardrails.response_validators import (
    _NEVER_SHARE,
    _ONLY_REQUESTED_DATA,
    _RETRY_AS_FIRST_ANSWER,
    GuardrailStats,
    Intents,
    ResponseGuardrail,
    _unnegated,
    normalize,
)

GUARDRAIL_BLOCK_STATUS_MISMATCH = "block_status_mismatch"
GUARDRAIL_FORMAL_CASE_WHILE_PENDING = "formal_case_while_pending"
# Marca informativa: el reporte de fraude corresponde a una transacción en hold (siempre se
# agrega cuando el estado es pending, aunque la respuesta sea correcta).
TRANSACTION_PENDING_HOLD = "transaction_pending_hold"
GUARDRAIL_STATE_DROPPED_AFTER_RETRY = "state_dropped_after_retry"

# --- Estado del bloqueo -----------------------------------------------------------------

# Afirmaciones de que la tarjeta YA está bloqueada. Las formas futuras o condicionales
# ("cuando quede bloqueada", "una vez que esté bloqueada") no coinciden.
_CLAIMS_CARD_IS_BLOCKED = re.compile(
    r"\b(?:ha sido|fue|quedo|queda|esta|se encuentra|ya esta|ya se encuentra)\s+(?:correctamente\s+)?bloquead[ao]s?\b"
    r"|\bbloqueo\s+(?:fue\s+|ha sido\s+|quedo\s+)?(?:exitoso|efectivo|confirmado|realizado|completado|aplicado)\b"
    r"|\b(?:hemos|he|ya)\s+bloqueado\b|\bbloqueamos\s+(?:tu|su|la)\b|\bacabamos de bloquear\b"
    r"|\bse\s+(?:realizo|completo|aplico|efectuo|confirmo|hizo)\s+el\s+bloqueo\b"
)
# Afirmaciones de una operación de bloqueo NUEVA (incorrectas si la tarjeta ya estaba bloqueada).
_CLAIMS_NEW_BLOCK_OPERATION = re.compile(
    r"\b(?:hemos|he)\s+bloqueado\b|\bbloqueamos\s+(?:tu|su|la)\b|\bacabamos de bloquear\b"
    r"|\bse\s+(?:realizo|completo|aplico|efectuo|hizo)\s+el\s+bloqueo\b"
    r"|\bbloqueo\s+(?:fue\s+|ha sido\s+)?(?:exitoso|realizado|completado|aplicado)\b"
)

BLOCK_STATUS_FALLBACK = {
    # BLOCKED no tiene guardrail de estado, pero su texto se usa si otro guardrail llega al
    # texto fijo en el turno en que se ejecutó el bloqueo.
    BlockStatus.BLOCKED: "El bloqueo de tu tarjeta fue confirmado. " + _NEVER_SHARE,
    BlockStatus.BLOCK_REQUESTED: (
        "Tu solicitud de bloqueo fue recibida y está en proceso. Te confirmaremos cuando el bloqueo "
        "sea efectivo. " + _NEVER_SHARE
    ),
    BlockStatus.BLOCK_PENDING: (
        "Tu solicitud de bloqueo está en proceso. Te confirmaremos cuando el bloqueo sea efectivo. "
        + _NEVER_SHARE
    ),
    BlockStatus.BLOCK_FAILED: (
        "Hubo un inconveniente al procesar el bloqueo de tu tarjeta. Un asesor dará seguimiento a tu "
        "caso. " + _NEVER_SHARE
    ),
    BlockStatus.ALREADY_BLOCKED: (
        "Tu tarjeta ya se encontraba bloqueada; no fue necesario realizar una nueva operación. "
        + _NEVER_SHARE
    ),
}


def block_status_fallback(status: BlockStatus) -> str:
    return BLOCK_STATUS_FALLBACK[status]


def block_operation_fact(status: BlockStatus) -> str:
    """Hecho del sistema para el verificador: si el bloqueo del turno es nuevo o repetido.

    Sin este hecho, el verificador aplicó la regla de idempotencia (POL-BLQ-2026-4, "una
    segunda solicitud no genera una nueva operación") a una primera solicitud en BLOCK_PENDING
    y marcó como sin respaldo una respuesta correcta; el reintento perdió el estado.
    """
    if status == BlockStatus.ALREADY_BLOCKED:
        return (
            "La tarjeta ya estaba bloqueada antes de esta solicitud: es una solicitud repetida y no "
            "genera una operación nueva."
        )
    return (
        "Esta solicitud de bloqueo es una operación nueva, no una solicitud repetida: el sistema no "
        "reportó un bloqueo previo de la tarjeta. Las reglas para solicitudes repetidas "
        "(idempotencia) no aplican a este turno."
    )


block_status_stats = GuardrailStats(GUARDRAIL_BLOCK_STATUS_MISMATCH)


class BlockStatusGuard:
    """Guardrail para el turno en que se ejecutó el bloqueo simulado con un estado dado."""

    def __init__(self, status: BlockStatus):
        self.status = status

    def detect(self, answer: str, user_message: str = "", intents: Intents = frozenset()) -> bool:
        text = normalize(answer)
        if self.status == BlockStatus.ALREADY_BLOCKED:
            return _unnegated(_CLAIMS_NEW_BLOCK_OPERATION, text)
        return _unnegated(_CLAIMS_CARD_IS_BLOCKED, text)

    def retry_instruction(self, intents: Intents) -> str:
        if self.status == BlockStatus.ALREADY_BLOCKED:
            problem = (
                "presentó el bloqueo como una operación nueva, pero la tarjeta ya estaba bloqueada "
                "(estado ALREADY_BLOCKED). Informa solo el estado actual: ya estaba bloqueada."
            )
        else:
            problem = (
                f"afirmó que la tarjeta está bloqueada, pero el sistema devolvió {self.status.value}: el "
                "bloqueo NO está confirmado. No digas que la tarjeta está bloqueada."
            )
        return f"Tu respuesta anterior {problem}" + _ONLY_REQUESTED_DATA + _RETRY_AS_FIRST_ANSWER

    def fallback(self, user_message: str, intents: Intents) -> str:
        return block_status_fallback(self.status)

    @property
    def guardrail(self) -> ResponseGuardrail:
        return ResponseGuardrail(
            name=GUARDRAIL_BLOCK_STATUS_MISMATCH,
            detect=self.detect,
            # Con BLOCKED, afirmar el bloqueo es correcto: no hay nada que verificar.
            applies=lambda intents: self.status != BlockStatus.BLOCKED,
            retry_instruction=self.retry_instruction,
            fallback=self.fallback,
            stats=block_status_stats,
        )


# --- Transacción en hold (POL-FRD-2026-4) -----------------------------------------------

# Afirmaciones de que ya se abrió un caso formal o de un número/ID de caso. Las formas futuras
# ("el caso formal se abrirá cuando la transacción se liquide") no coinciden.
_CLAIMS_FORMAL_CASE = re.compile(
    r"\b(?:hemos|he|se ha|se han|ya)\s+(?:abierto|generado|creado)\s+(?:el|un|su|tu)\s+(?:caso|reclamo|disputa)\b"
    r"|\b(?:abrimos|abri|generamos|genere|creamos|cree)\s+(?:el|un|su|tu)\s+(?:caso|reclamo|disputa)\b"
    r"|\bse\s+(?:abrio|genero|creo)\s+(?:el|un|su|tu)\s+(?:caso|reclamo|disputa)\b"
    r"|\bcaso formal\s+(?:ha sido|fue|quedo|esta)\s+(?:abierto|creado|generado|registrado)\b"
    r"|\b(?:numero|id|codigo)\s+de\s+(?:caso|reclamo)\s*(?:es\b|:)"
    r"|\b(?:caso|reclamo)\s*(?:n[o°]\.?|#|numero)\s*[:\-]?\s*[a-z]*-?\d"
)

PENDING_TRANSACTION_FALLBACK = (
    "Recibimos tu reporte y quedó registrado como preliminar: la transacción todavía está pendiente "
    "de liquidación. El caso formal de disputa se abrirá cuando la transacción se liquide. "
    + _NEVER_SHARE
)

formal_case_stats = GuardrailStats(GUARDRAIL_FORMAL_CASE_WHILE_PENDING)


def claims_formal_case(answer: str, user_message: str = "", intents: Intents = frozenset()) -> bool:
    return _unnegated(_CLAIMS_FORMAL_CASE, normalize(answer))


FORMAL_CASE_RETRY_INSTRUCTION = (
    "Tu respuesta anterior afirmó que se abrió un caso formal o dio un número de caso, pero la "
    "transacción está PENDIENTE de liquidación: según la política, el reporte se registra como "
    "preliminar y el caso formal solo se genera cuando la transacción liquide o caiga. No afirmes "
    "que se abrió un caso ni des un número de caso."
    + _ONLY_REQUESTED_DATA
    + _RETRY_AS_FIRST_ANSWER
)

FORMAL_CASE_WHILE_PENDING_GUARDRAIL = ResponseGuardrail(
    name=GUARDRAIL_FORMAL_CASE_WHILE_PENDING,
    detect=claims_formal_case,
    applies=lambda intents: True,  # Solo se agrega al pipeline cuando la transacción está pending.
    retry_instruction=lambda intents: FORMAL_CASE_RETRY_INSTRUCTION,
    fallback=lambda user_message, intents: PENDING_TRANSACTION_FALLBACK,
    stats=formal_case_stats,
)


# --- Red de seguridad: el reintento no puede perder el estado de la operación ----------

_REPORTS_BLOCK = re.compile(r"\bbloque")
_REPORTS_PENDING_TRANSACTION = re.compile(r"\b(?:pendiente|preliminar|liquid)")


def reports_operation_state(
    answer: str, block_status: Optional[BlockStatus], transaction_status: Optional[TransactionStatus]
) -> bool:
    """La respuesta menciona la operación del turno: el bloqueo, o la transacción en hold.

    Es un chequeo de presencia, no de corrección: que el estado mencionado sea el real lo
    cubren block_status_mismatch, formal_case_while_pending y el verificador de fidelidad.
    """
    text = normalize(answer)
    if block_status is not None and not _REPORTS_BLOCK.search(text):
        return False
    if transaction_status == TransactionStatus.PENDING and not _REPORTS_PENDING_TRANSACTION.search(text):
        return False
    return True
