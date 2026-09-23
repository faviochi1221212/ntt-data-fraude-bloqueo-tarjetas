# Limitaciones conocidas

## Retriever: robo con violencia vs. pérdida simple (intent `bloquear_tarjeta`)

El retriever no distingue robo con violencia de pérdida simple dentro del intent
`bloquear_tarjeta`, porque ambos comparten el mismo intent y la entidad `hubo_agresion`
no se extrae todavía.

Consecuencia: en una pérdida simple (p. ej. "perdí mi tarjeta, ¿me pueden desactivar?")
el resultado de `search_by_intent()` incluye igual los chunks propios de la agresión
(POL-BLQ-2026-2, excepción de autenticación reducida; POL-BLQ-2026-3B, verificación de
línea contra spoofing), porque son obligatorios para todo el intent.

Mitigación activa: el prompt del sistema del endpoint `/api/v1/chat`
(`AGGRESSION_RULE` en `app/orchestrator/generator.py`) instruye explícitamente al LLM a
aplicar la excepción de autenticación reducida solo cuando el usuario describe violencia,
agresión, amenaza o coacción física de forma explícita, nunca por pérdida simple.

Esta mitigación sigue siendo una instrucción de prompt, no una regla de código verificada
estructuralmente: depende de que el LLM la obedezca en cada respuesta. El test
`test_case_6_lost_card_does_not_skip_phone_key` la comprueba sobre una sola pregunta y con
heurísticas de texto, no la garantiza. (El guardrail de código de la sección siguiente
cubre otro problema, la descripción del canal de la clave; no verifica esta regla.)

Trabajo futuro: extraer `hubo_agresion` como entidad real en el orquestador, para decidir
en código (y no en el prompt) si la excepción aplica y excluir los chunks de agresión del
contexto cuando no aplica.

## Generación: descripción del canal de los datos de autenticación

Riesgo: el LLM describía cómo o por dónde llega la clave telefónica ("la clave que recibe
por SMS", "que recibe en el número registrado"), información que no está en la KB y que un
atacante de phishing puede usar para hacerse pasar por el banco.

Defensa en dos capas (ya no es mitigación de prompt únicamente):

1. Prompt (primera línea): regla 3, `AUTH_DATA_RULE` en `app/orchestrator/generator.py`.
2. Código (respaldo estructural): `app/guardrails/response_validators.py`. Si la respuesta
   coincide con `DESCRIBES_AUTH_CHANNEL`, se regenera una vez con una instrucción de
   corrección; si el reintento también falla, se responde con un texto fijo y se agrega
   `auth_channel_disclosure` a `guardrail_triggered`. Reintentos y fallbacks se registran en
   logs (WARNING) con contadores acumulados del proceso.

Limitaciones de este guardrail:

- Alcance: se aplica solo cuando el único intent es `bloquear_tarjeta`. Su texto fijo pide
  documento de identidad y clave telefónica, lo que contradice la política en phishing
  (solo documento, POL-SEG-2026-2) y en desbloqueo (no se capturan datos, POL-BLQ-2026-5);
  y en phishing frases como "si te llegó un correo" son legítimas. Esos flujos no tienen
  todavía este respaldo de código.
- Dentro de `bloquear_tarjeta` tampoco distingue robo con violencia (ver sección anterior):
  si se llegara al texto fijo en ese caso, pediría la clave telefónica que la excepción
  suspende. En las pruebas el guardrail no se disparó en ese caso.
- Detección por patrones de texto: atrapa las formulaciones conocidas, no cualquier
  paráfrasis posible.
- Los contadores viven en memoria del proceso: se reinician con cada arranque y no se
  agregan entre réplicas.

## Generación: verificación de línea delegada al propio cliente (robo con violencia)

Observado: en el caso "me robaron la tarjeta con violencia" (5 corridas contra Groq con
`openai/gpt-oss-120b`), el LLM traslada al cliente la verificación de la línea registrada:

- 1 de 5 le pidió declarar su número: "por favor indique el número desde el que nos está
  llamando".
- 3 de 5 le pidieron confirmarlo: "confirme que está contactándonos desde su número de
  teléfono registrado".
- 1 de 5 lo planteó como verificación propia del banco: "Verificaremos que la comunicación
  provenga del número de línea registrado".

Por qué es un riesgo: POL-BLQ-2026-3B establece que la verificación de línea bajo robo con
violencia debe hacerse contra el operador de telecomunicaciones (nivel de red), no solo con
la cabecera de identificador de llamada, porque esta se puede falsificar con troncales
VoIP/SIP conociendo únicamente el documento de la víctima. Pedirle al cliente que declare o
confirme su número es todavía más débil que la cabecera: el LLM genera justo la instrucción
que el chunk advierte que no basta. Como en este flujo no se exige clave telefónica, esa
verificación es el principal control de identidad además del documento. El mismo riesgo
aplica al caso combinado robo + phishing, que recupera los mismos chunks.

Mitigación activa: ninguna, ni de prompt ni de código. `AUTH_DATA_RULE` (regla 3) no lo
cubre: prohíbe describir cómo llega un dato de autenticación, pero no impide pedirle al
cliente que declare o confirme su línea. El guardrail `auth_channel_disclosure` tampoco lo
detecta (ninguna de las frases observadas coincide con `DESCRIBES_AUTH_CHANNEL`).

Trabajo futuro: ampliar la regla 3 del prompt para que la verificación de línea se presente
como un control interno del banco y nunca como un dato que el cliente declara o confirma, o
agregar un guardrail de código dedicado que detecte esas solicitudes en respuestas con
chunks de verificación de línea (POL-BLQ-2026-2 / 3B).
