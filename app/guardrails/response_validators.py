"""Validaciones de código sobre la respuesta generada por el LLM.

Son el respaldo estructural de las reglas del prompt (app/orchestrator/generator.py):
el prompt es la primera línea de defensa y estas validaciones atrapan lo que se le escapa.
Cada guardrail define cómo detectar el problema, una corrección para un único reintento y
un texto fijo (no generado) para cuando el reintento también falla. Reintento y texto fijo
dependen de los intents, porque lo que la política permite pedir cambia según el flujo.
"""

import logging
import re
import threading
import unicodedata
from dataclasses import dataclass
from typing import Callable, FrozenSet

from app.models.schemas import Intent

logger = logging.getLogger(__name__)

Intents = FrozenSet[Intent]
_NO_INTENTS: Intents = frozenset()


def allows_phone_key(intents: Intents) -> bool:
    """Si el flujo puede pedir la clave telefónica. Criterio único para todos los guardrails.

    Sí cuando hay un paso de bloqueo (POL-BLQ-2026-1), solo o acompañado de otros intents
    (p. ej. "perdí mi tarjeta, quiero una nueva"). No cuando hay phishing, aunque también haya
    bloqueo: POL-SEG-2026-2 limita la identidad al documento, sin clave, incluso para el
    bloqueo preventivo. Sin bloqueo, ningún fragmento de fraude, reposición o desbloqueo la pide.
    """
    return Intent.BLOQUEAR_TARJETA in intents and Intent.REPORTAR_INTENTO_PHISHING not in intents


def normalize(text: str) -> str:
    """Minúsculas y sin tildes."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _prepare(text: str) -> str:
    # "PAN" solo cuenta en mayúsculas ("pan" en minúsculas es una palabra común en español).
    return normalize(re.sub(r"\bPAN\b", "numero completo de la tarjeta", text))


_NEGATION = re.compile(r"\b(?:no|nunca|jamas|ni|tampoco|ningun|ninguna)\b")
_CLAUSE_BREAK = re.compile(r"[,.;:!?\n]")


def _unnegated(pattern: re.Pattern, text: str) -> bool:
    """True si hay una coincidencia sin negación en la misma cláusula, antes de ella."""
    for match in pattern.finditer(text):
        clause_start = max((m.end() for m in _CLAUSE_BREAK.finditer(text, 0, match.start())), default=0)
        if not _NEGATION.search(text, clause_start, match.start()):
            return True
    return False


class GuardrailStats:
    """Contadores en memoria del proceso para dar visibilidad en logs."""

    def __init__(self, name: str) -> None:
        self.name = name
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
                "Guardrail %s: respuesta detectada; reintentando (reintentos=%d, fallbacks=%d, validaciones=%d)",
                self.name, self.retries, self.fallbacks, self.checks,
            )

    def record_fallback(self, reason: str) -> None:
        with self._lock:
            self.fallbacks += 1
            logger.warning(
                "Guardrail %s: se usa el texto fijo (%s) (reintentos=%d, fallbacks=%d, validaciones=%d)",
                self.name, reason, self.retries, self.fallbacks, self.checks,
            )


@dataclass(frozen=True)
class ResponseGuardrail:
    name: str  # Valor que se agrega a guardrail_triggered si se llega al texto fijo.
    # (answer, user_message, intents) -> True si la respuesta viola la regla.
    detect: Callable[[str, str, Intents], bool]
    applies: Callable[[Intents], bool]  # Si el guardrail corre para esos intents.
    retry_instruction: Callable[[Intents], str]
    fallback: Callable[[str, Intents], str]  # (user_message, intents) -> texto fijo.
    stats: GuardrailStats


# --- Piezas comunes de las instrucciones de reintento --------------------------------

# Síntoma raíz compartido por los tres guardrails: el LLM describe cómo llegan los datos.
_ONLY_REQUESTED_DATA = (
    " Pide solo los datos que indiquen los fragmentos recuperados, sin explicar ni describir "
    "por qué medio o canal se reciben, entregan o confirman."
)
# El cliente nunca ve la respuesta rechazada: el reintento no debe disculparse ni aludir a ella.
_RETRY_AS_FIRST_ANSWER = (
    " El cliente no vio tu respuesta anterior: no te disculpes ni menciones ninguna corrección; "
    "responde como si fuera tu primera respuesta."
)


# --- Datos sensibles: CVV, clave completa, PAN (POL-SEG-2026-1) --------------------

GUARDRAIL_SENSITIVE_DATA = "sensitive_data_request"

# Datos que el banco nunca solicita. No incluye "clave telefónica" (dato legítimo del
# bloqueo estándar, POL-BLQ-2026-1) ni "clave" a secas, que suele referirse a ella.
_SENSITIVE = (
    r"(?:cvv2?|cvc2?|cv2"
    r"|codigo de seguridad"
    r"|(?:los\s+)?(?:3|tres)\s+digitos(?:\s+(?:de atras|del reverso|de seguridad|de la parte (?:de )?atras))?"
    r"|clave completa"
    r"|clave (?:de )?(?:banca (?:por )?internet|internet|web|en linea)(?: completa)?"
    r"|clave secreta|clave de (?:tu|su|la) tarjeta|\bpin\b"
    r"|numero completo de (?:tu|su|la|mi) tarjeta|numero de (?:tu|su) tarjeta completo"
    r"|(?:los\s+)?16 digitos)"
)
_MENTIONS_SENSITIVE = re.compile(_SENSITIVE)

# Solicitudes dirigidas al cliente: imperativos, "necesito/requerimos" y "¿puede indicar...?".
# Los subjuntivos negativos ("no compartas", "nunca te pediremos") no están en la lista.
_REQUEST_VERB = (
    r"\b(?:"
    r"indica(?:me|nos)?|indique(?:me|nos)?|proporciona(?:me|nos)?|proporcione(?:me|nos)?"
    r"|envia(?:me|nos)?|envie(?:me|nos)?|dime|dinos|digame|diganos"
    r"|comparte(?:me|nos)?|comparta(?:me|nos)?|facilita(?:me|nos)?|facilite(?:me|nos)?"
    r"|escribe(?:me|nos)?|escriba(?:me|nos)?|ingresa|ingrese|dicta(?:me|nos)?|dicte(?:me|nos)?"
    r"|confirma(?:me|nos)?|confirme(?:me|nos)?|brinda(?:me|nos)?|brinde(?:me|nos)?"
    r"|necesito|necesitamos|requiero|requerimos"
    r"|(?:puede|puedes|podria|podrias|podra)\s+(?:usted\s+)?"
    r"(?:indicar|proporcionar|enviar|compartir|dar|facilitar|confirmar|dictar|escribir|ingresar|brindar)\w*"
    r")\b"
)
_REQUESTS_SENSITIVE = re.compile(rf"{_REQUEST_VERB}[^.!?;\n]{{0,50}}?{_SENSITIVE}")

# Confirmaciones de haber recibido/validado un dato sensible.
_CONFIRM_VERB = (
    r"\b(?:recibi|recibimos|registre|registramos|anote|anotamos|guarde|guardamos"
    r"|valide|validamos|verifique|verificamos|tengo|tenemos|confirmo|confirmamos)\b"
)
_CONFIRMS_SENSITIVE = re.compile(
    rf"{_CONFIRM_VERB}[^.!?;\n]{{0,30}}?\b(?:tu|su|el|la)\s+{_SENSITIVE}"
    rf"|{_SENSITIVE}\s+(?:\d+\s+)?(?:es|esta|son|ha sido|fue)\s+(?:correct|valid|verificad|confirmad|recibid)"
)
# Si el cliente compartió un dato sensible, "recibí tu clave/código" también cuenta.
_CONFIRMS_SHARED_SECRET = re.compile(
    rf"{_CONFIRM_VERB}[^.!?;\n]{{0,30}}?\b(?:tu|su)\s+(?:clave|codigo|contrasena|numero de tarjeta)\b"
)

# Números que el cliente escribió junto a una palabra clave. Dos niveles:
# - Estricto (CVV, código de seguridad, clave completa/de internet, PIN, "dígitos"): siempre prohibido.
# - "Clave"/"código" a secas: puede ser la clave telefónica que el bloqueo estándar sí pide.
_STRICT_SECRET_KEYWORD = (
    r"(?:cvv2?|cvc2?|cv2|codigo de seguridad|clave completa|clave secreta"
    r"|clave (?:de )?(?:banca (?:por )?internet|internet|web|en linea)|\bpin\b|digitos)"
)
_BARE_SECRET_KEYWORD = r"(?:clave|codigo|contrasena)"


def _near_keyword(keyword: str) -> re.Pattern:
    return re.compile(rf"{keyword}\D{{0,30}}?\b(\d{{3,6}})\b|\b(\d{{3,6}})\b\D{{0,15}}?{keyword}")


_STRICT_SECRET_NEAR_KEYWORD = _near_keyword(_STRICT_SECRET_KEYWORD)
_BARE_SECRET_NEAR_KEYWORD = _near_keyword(_BARE_SECRET_KEYWORD)
# Posible PAN: 13 a 19 dígitos, con espacios o guiones opcionales entre ellos.
_PAN_LIKE = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")

# Advertencia al cliente de no compartir datos ("no comparta", "nunca envíes", "evite compartir").
_WARNS_NOT_TO_SHARE = re.compile(
    r"\b(?:no|nunca|jamas)\b[^.!?\n]{0,40}?\b(?:compart|envi|escrib|proporcion|dict|revel|brind|facilit|entreg)\w*"
    r"|\bevit\w*\s+(?:compartir|enviar|escribir|proporcionar|dar|entregar)"
)


@dataclass(frozen=True)
class SharedSecrets:
    strict: frozenset  # CVV, PIN, clave completa o PAN: prohibidos en cualquier intent.
    bare: frozenset  # "clave"/"código" a secas: posible clave telefónica.

    @property
    def all(self) -> frozenset:
        return self.strict | self.bare


def extract_shared_secrets(user_message: str) -> SharedSecrets:
    """Dígitos sensibles que el cliente escribió en su mensaje, separados por nivel."""
    text = _prepare(user_message)

    def digits_near(pattern: re.Pattern) -> set[str]:
        return {g for m in pattern.finditer(text) for g in m.groups() if g}

    strict = digits_near(_STRICT_SECRET_NEAR_KEYWORD)
    strict |= {re.sub(r"\D", "", m.group()) for m in _PAN_LIKE.finditer(text)}
    bare = digits_near(_BARE_SECRET_NEAR_KEYWORD) - strict
    return SharedSecrets(strict=frozenset(strict), bare=frozenset(bare))


def client_involved_sensitive_data(user_message: str) -> bool:
    """El cliente escribió un dato sensible o preguntó por uno ("cuál es mi CVV")."""
    return bool(extract_shared_secrets(user_message).all) or bool(
        _MENTIONS_SENSITIVE.search(_prepare(user_message))
    )


def requests_or_reveals_sensitive_data(
    answer: str, user_message: str = "", intents: Intents = _NO_INTENTS
) -> bool:
    """True si la respuesta viola POL-SEG-2026-1. Aplica a todos los intents.

    - Pide: verbo de solicitud dirigido al cliente + dato sensible, sin negación en la cláusula.
    - Repite: contiene un número que el cliente escribió como CVV/clave/PIN o un PAN.
    - Confirma: "recibí/validé tu CVV", "tu código es correcto"; y si el cliente compartió un
      dato, también "recibí tu clave/código".
    - No advierte: el cliente compartió un dato que ningún paso del flujo está esperando y la
      respuesta no le advierte que no lo comparta. CVV/PIN/clave completa/PAN siempre
      requieren la advertencia; "clave" a secas la requiere salvo cuando el flujo admite la
      clave telefónica (allows_phone_key), porque ahí puede ser el dato que se pide a continuación.

    Repetir o confirmar un dato nunca se permite, tampoco la clave telefónica.
    """
    text = _prepare(answer)
    if _unnegated(_REQUESTS_SENSITIVE, text) or _unnegated(_CONFIRMS_SENSITIVE, text):
        return True

    secrets = extract_shared_secrets(user_message)
    if not secrets.all:
        return False
    if _unnegated(_CONFIRMS_SHARED_SECRET, text):
        return True
    answer_digits = re.sub(r"(?<=\d)[ -](?=\d)", "", answer)
    if any(re.search(rf"(?<!\d){s}(?!\d)", answer_digits) for s in secrets.all):
        return True

    needs_warning = bool(secrets.strict) or (bool(secrets.bare) and not allows_phone_key(intents))
    return needs_warning and not _WARNS_NOT_TO_SHARE.search(text)


SENSITIVE_DATA_RETRY_INSTRUCTION = (
    "Tu respuesta anterior pidió, repitió o confirmó un dato que el banco nunca solicita (CVV, "
    "código de seguridad, clave completa o número completo de tarjeta). Eso está prohibido. "
    "Vuelve a responder sin pedir, repetir ni confirmar esos datos. Si el cliente los escribió en "
    "su mensaje, no los menciones ni digas que los recibiste: ignóralos y adviértele que nunca "
    "comparta esa información por este canal."
    + _ONLY_REQUESTED_DATA
    + _RETRY_AS_FIRST_ANSWER
)

_NEVER_SHARE = "Nunca compartas tu CVV, tu clave completa ni el número completo de tu tarjeta."

# El cliente escribió o pidió un dato sensible: rechazo categórico.
SENSITIVE_DATA_FALLBACK_ANSWER = (
    "No puedo solicitar ni confirmar ese dato por este canal, bajo ninguna circunstancia. " + _NEVER_SHARE
)
# El problema lo originó el modelo (el cliente no escribió ni pidió nada sensible): texto que
# además indica cómo seguir, según lo que la política permite pedir en ese flujo.
MODEL_FALLBACK_BLOCK = (
    "Para continuar, necesito tu documento de identidad y tu clave telefónica. " + _NEVER_SHARE
)  # Flujos que admiten la clave telefónica (allows_phone_key; POL-BLQ-2026-1).
MODEL_FALLBACK_DOCUMENT_ONLY = (
    "Para continuar, necesito tu documento de identidad. " + _NEVER_SHARE
)  # Phishing, con o sin bloqueo: identidad limitada, solo documento (POL-SEG-2026-2).
MODEL_FALLBACK_NO_DATA = (
    _NEVER_SHARE + " Si necesitas más ayuda, puedo derivarte con un asesor."
)  # Resto: la política no indica datos a pedir; en desbloqueo no se captura ninguno (POL-BLQ-2026-5).


def sensitive_data_fallback(user_message: str, intents: Intents) -> str:
    if client_involved_sensitive_data(user_message):
        return SENSITIVE_DATA_FALLBACK_ANSWER
    if allows_phone_key(intents):
        return MODEL_FALLBACK_BLOCK
    if Intent.REPORTAR_INTENTO_PHISHING in intents:
        return MODEL_FALLBACK_DOCUMENT_ONLY
    return MODEL_FALLBACK_NO_DATA


sensitive_data_stats = GuardrailStats(GUARDRAIL_SENSITIVE_DATA)
SENSITIVE_DATA_GUARDRAIL = ResponseGuardrail(
    name=GUARDRAIL_SENSITIVE_DATA,
    detect=requests_or_reveals_sensitive_data,
    applies=lambda intents: True,  # Regla de oro: todos los intents, sin excepción.
    retry_instruction=lambda intents: SENSITIVE_DATA_RETRY_INSTRUCTION,
    fallback=sensitive_data_fallback,
    stats=sensitive_data_stats,
)


# --- Clave telefónica donde el flujo no la admite (allows_phone_key) ------------------

GUARDRAIL_PHONE_KEY_OUTSIDE_BLOCK = "phone_key_outside_block"

# Cualquier mención no negada de la clave telefónica ("indícame tu clave telefónica", un ítem
# de lista "- **Clave telefónica**"). Las negadas ("no compartas tu clave telefónica") pasan.
_MENTIONS_PHONE_KEY = re.compile(r"\bclaves? telefonicas?\b")


def requests_phone_key(answer: str, user_message: str = "", intents: Intents = _NO_INTENTS) -> bool:
    return _unnegated(_MENTIONS_PHONE_KEY, normalize(answer))


PHONE_KEY_RETRY_INSTRUCTION = (
    "Tu respuesta anterior pidió o mencionó la clave telefónica como dato a recolectar. En esta "
    "consulta no corresponde pedirla: la clave telefónica solo se solicita para bloquear una "
    "tarjeta, y nunca ante un reporte de phishing. Vuelve a responder sin pedirla ni mencionarla."
    + _ONLY_REQUESTED_DATA
    + _RETRY_AS_FIRST_ANSWER
)

phone_key_stats = GuardrailStats(GUARDRAIL_PHONE_KEY_OUTSIDE_BLOCK)
PHONE_KEY_OUTSIDE_BLOCK_GUARDRAIL = ResponseGuardrail(
    name=GUARDRAIL_PHONE_KEY_OUTSIDE_BLOCK,
    detect=requests_phone_key,
    applies=lambda intents: not allows_phone_key(intents),
    retry_instruction=lambda intents: PHONE_KEY_RETRY_INSTRUCTION,
    # Solo corre donde no se admite la clave: sus textos fijos nunca la piden.
    fallback=sensitive_data_fallback,
    stats=phone_key_stats,
)


# --- Canal de los datos de autenticación (AUTH_DATA_RULE) ------------------------

GUARDRAIL_AUTH_CHANNEL = "auth_channel_disclosure"

# Un dato de autenticación seguido de cómo o por dónde llega ("la clave telefónica que recibes
# en tu móvil", "la clave que recibe por SMS"). El orden importa: en phishing es legítimo
# describir el ataque ("si te llega un SMS pidiendo tu clave"), con el canal antes que el dato.
_AUTH_DATUM = r"\b(?:claves?|codigos?|token|contrasena|otp)\b"
_CHANNEL_DESCRIPTION = (
    r"(?:\bsms\b|mensaje de texto|por mensaje|por llamada|llamada de seguridad|por correo"
    r"|que (?:recibe|recibes|le llega|te llega|le enviamos|te enviamos)"
    r"|en (?:tu|su) (?:movil|celular|telefono|numero registrado))"
)
DESCRIBES_AUTH_CHANNEL = re.compile(rf"{_AUTH_DATUM}[^.!?\n]{{0,40}}?{_CHANNEL_DESCRIPTION}")


def describes_auth_channel(answer: str, user_message: str = "", intents: Intents = _NO_INTENTS) -> bool:
    return bool(DESCRIBES_AUTH_CHANNEL.search(normalize(answer)))


AUTH_CHANNEL_RETRY_INSTRUCTION = (
    "Tu respuesta anterior mencionó cómo o por dónde se recibe la clave telefónica. Eso está "
    "prohibido. Vuelve a responder pidiendo únicamente el documento de identidad y la clave "
    "telefónica por su nombre, sin ninguna mención de SMS, llamada, correo, ni número registrado."
    + _ONLY_REQUESTED_DATA
    + _RETRY_AS_FIRST_ANSWER
)
# Donde no se admite la clave telefónica no se puede pedir "documento + clave": corrección genérica.
AUTH_CHANNEL_RETRY_INSTRUCTION_GENERIC = (
    "Tu respuesta anterior describió cómo o por dónde se recibe un dato de autenticación. Eso "
    "está prohibido. Vuelve a responder sin ninguna mención de SMS, llamada, correo, móvil ni "
    "número registrado."
    + _ONLY_REQUESTED_DATA
    + _RETRY_AS_FIRST_ANSWER
)

# Texto fijo de canal: solo correcto donde se admite la clave telefónica (allows_phone_key).
AUTH_CHANNEL_FALLBACK_ANSWER = "Para continuar, necesito tu documento de identidad y tu clave telefónica."


def auth_channel_fallback(user_message: str, intents: Intents) -> str:
    # Donde no se admite, pedir la clave telefónica ya contradice la política; y si el cliente
    # escribió o pidió un dato sensible, el texto de canal no lleva la advertencia obligatoria.
    # En ambos casos se usa el texto de datos sensibles.
    if allows_phone_key(intents) and not client_involved_sensitive_data(user_message):
        return AUTH_CHANNEL_FALLBACK_ANSWER
    return sensitive_data_fallback(user_message, intents)


auth_channel_stats = GuardrailStats(GUARDRAIL_AUTH_CHANNEL)
AUTH_CHANNEL_GUARDRAIL = ResponseGuardrail(
    name=GUARDRAIL_AUTH_CHANNEL,
    detect=describes_auth_channel,
    applies=lambda intents: True,  # Describir el canal nunca es aceptable.
    retry_instruction=lambda intents: (
        AUTH_CHANNEL_RETRY_INSTRUCTION if allows_phone_key(intents) else AUTH_CHANNEL_RETRY_INSTRUCTION_GENERIC
    ),
    fallback=auth_channel_fallback,
    stats=auth_channel_stats,
)


# Jerarquía de severidad, de mayor a menor. Decide qué texto fijo se usa cuando una respuesta
# viola varios guardrails (ver ChatPipeline._apply_guardrails).
RESPONSE_GUARDRAILS_BY_SEVERITY: tuple[ResponseGuardrail, ...] = (
    SENSITIVE_DATA_GUARDRAIL,
    PHONE_KEY_OUTSIDE_BLOCK_GUARDRAIL,
    AUTH_CHANNEL_GUARDRAIL,
)
