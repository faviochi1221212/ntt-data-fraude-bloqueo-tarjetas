"""Capa 1 del classifier: disparadores deterministas por palabra clave.

ESTA CAPA ES INTENCIONALMENTE RÍGIDA Y DE BAJA COBERTURA.

Existe solo para garantizar que ciertos casos de seguridad NUNCA dependan de un
modelo (LLM o embeddings): solicitudes de datos sensibles (phishing) y pedidos de
desbloqueo. No pretende cubrir todas las formas de expresar esos casos; la
cobertura general es responsabilidad de la capa LLM (app/orchestrator/classifier.py).

Criterios de diseño:
- Sin modelos, sin red, sin estado: misma entrada -> misma salida.
- Preferimos falsos positivos a falsos negativos. Un falso positivo solo agrega un
  intent (p. ej. "no quiero desbloquear" dispara desbloquear_tarjeta, cuya política
  es negarse y derivar, lo cual es inocuo).
- No se usa la palabra "datos" sola ni "clave" sin un verbo de solicitud, para no
  disparar con "olvidé mi clave" o "actualizar mis datos".
"""

import re
import unicodedata

from app.models.schemas import Intent


def _normalize(text: str) -> str:
    """Minúsculas y sin tildes, para que 'contraseña'/'contrasena' o 'código'/'codigo' coincidan."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


# --- reportar_intento_phishing ---------------------------------------------

# Datos que el banco nunca pide: su sola mención dispara el intent.
_SENSITIVE_DATA = re.compile(
    r"\b(?:"
    r"cvv2?|cvc2?|cv2"
    r"|codigo de seguridad"
    r"|clave completa"
    r"|numero completo de (?:la |mi )?tarjeta"
    r")\b"
)

# PAN se evalúa sobre el texto original y en mayúsculas, porque "pan" en minúsculas
# es una palabra común en español.
_PAN = re.compile(r"\bPAN\b")

# Credenciales que disparan solo si aparecen cerca de un verbo de solicitud
# ("me pidieron mi clave", "me están solicitando el token").
_REQUEST_VERB = r"\b(?:pid\w*|pedi\w*|piden|pide|solicit\w*|exig\w*)\b"
_CREDENTIAL = r"\b(?:clave|claves|contrasena|pin|token|codigo(?: de verificacion| sms| otp)?|otp)\b"
_CREDENTIAL_REQUEST = re.compile(
    rf"{_REQUEST_VERB}.{{0,40}}{_CREDENTIAL}|{_CREDENTIAL}.{{0,40}}{_REQUEST_VERB}"
)

# --- desbloquear_tarjeta ----------------------------------------------------

_UNBLOCK = re.compile(
    r"\bdesbloque\w*"
    r"|\b(?:quit|sac|levant|retir)\w*\s+(?:el |ese |este |dicho )?bloqueo"
    r"|\b(?:reactiv|rehabilit)\w*\s+(?:mi |la |nuevamente |de nuevo )?(?:mi )?tarjeta"
)


def detect_hard_triggers(text: str) -> list[Intent]:
    """Retorna los intents disparados por coincidencia de palabras clave, sin duplicados."""
    normalized = _normalize(text)
    intents: list[Intent] = []

    if (
        _SENSITIVE_DATA.search(normalized)
        or _PAN.search(text)
        or _CREDENTIAL_REQUEST.search(normalized)
    ):
        intents.append(Intent.REPORTAR_INTENTO_PHISHING)

    if _UNBLOCK.search(normalized):
        intents.append(Intent.DESBLOQUEAR_TARJETA)

    return intents
