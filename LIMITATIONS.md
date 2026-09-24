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
agresión, amenaza o coacción física de forma explícita, nunca por pérdida simple. La regla
solo se incluye cuando `allows_phone_key(intents)` es verdadero (bloqueo, solo o combinado,
sin phishing); en el resto de los flujos no aplica y se omite.

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

1. Prompt (primera línea): `AUTH_DATA_RULE` en `app/orchestrator/generator.py`.
2. Código (respaldo estructural): `app/guardrails/response_validators.py`. Si la respuesta
   coincide con `DESCRIBES_AUTH_CHANNEL`, se regenera una vez con una instrucción de
   corrección; si el reintento también falla, se responde con un texto fijo y se agrega
   `auth_channel_disclosure` a `guardrail_triggered`. Reintentos y fallbacks se registran en
   logs (WARNING) con contadores acumulados del proceso.

Limitaciones de este guardrail:

- Alcance: se aplica en todos los intents. El patrón exige el dato de autenticación antes
  que el canal ("la clave que recibe por SMS"), para no marcar la educación sobre phishing
  ("si te llega un SMS pidiendo tu clave"). Si el reintento falla, el texto fijo depende del
  flujo: documento + clave telefónica solo donde `allows_phone_key(intents)`; en el resto, el
  texto de datos sensibles (solo documento en phishing, ningún dato en los demás). Forma parte
  de un conjunto de tres guardrails de salida con revalidación cruzada (ver
  `ChatPipeline._apply_guardrails`).
- En bloqueo tampoco distingue robo con violencia (ver sección anterior):
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

Mitigación activa: ninguna, ni de prompt ni de código. `AUTH_DATA_RULE` no lo cubre:
prohíbe describir cómo llega un dato de autenticación, pero no impide pedirle al cliente que
declare o confirme su línea. El guardrail `auth_channel_disclosure` tampoco lo detecta
(ninguna de las frases observadas coincide con `DESCRIBES_AUTH_CHANNEL`), ni el guardrail de
fidelidad `unsupported_claim`, que aprueba la frase porque parafrasea POL-BLQ-2026-2 (ver la
sección siguiente).

Trabajo futuro: ampliar `AUTH_DATA_RULE` en el prompt para que la verificación de línea se presente
como un control interno del banco y nunca como un dato que el cliente declara o confirma, o
agregar un guardrail de código dedicado que detecte esas solicitudes en respuestas con
chunks de verificación de línea (POL-BLQ-2026-2 / 3B).

## Generación: inversión de relación condicional en respuestas generadas

Observado: en una prueba de reposición ("quiero solicitar una tarjeta nueva", corrida 5#1,
con la primera respuesta forzada a pedir un dato sensible y el reintento generado por Groq con
`openai/gpt-oss-120b`), el reintento respondió: "Para solicitar la reposición de su tarjeta
debemos abrir un caso de investigación de fraude". POL-REP-2026-2 dice otra cosa en cuanto a
causalidad: la exoneración de costos aplica CUANDO ya existe un caso de fraude o un reporte de
robo con violencia; no dice que abrir un caso sea un requisito para obtener la reposición. El
modelo no inventó un dato nuevo: invirtió la relación condicional de un chunk real,
convirtiendo la condición de una exoneración en un requisito del trámite.

Por qué es un riesgo: puede llevar a un cliente a creer que necesita iniciar un trámite de
fraude que no le corresponde, o desviar el flujo innecesariamente hacia fraude.

Mitigación activa: guardrail de fidelidad `unsupported_claim` (`app/guardrails/faithfulness.py`,
commit `b61142a`). Un segundo LLM (Groq, el mismo modelo `openai/gpt-oss-120b`, con
`json_schema` estricto) compara el answer contra el contenido completo de los chunks citados,
no solo contra patrones de texto. Su prompt pide revisar tanto datos inventados como
relaciones condicionales invertidas, con esta misma inversión como ejemplo few-shot. Si
encuentra afirmaciones sin respaldo, se reintenta la generación una vez señalándolas; si el
reintento también falla, se responde con un texto fijo según el flujo y se agrega
`unsupported_claim` a `guardrail_triggered`. Corre al final del pipeline y participa de la
revalidación cruzada con los guardrails de patrones.

Resultados contra Groq real:

- Detectó la inversión original (reposición/fraude) en 5 de 5 corridas dirigidas, con la
  respuesta invertida forzada; en 4 el reintento la corrigió y en 1 se llegó al texto fijo.
- Detectó 2 alucinaciones espontáneas en 5 corridas sin forzar nada ("quiero solicitar una
  tarjeta nueva"): un canal no documentado ("el caso puede abrirse a través del portal") y un
  costo incorrecto ("la modalidad express no genera costo", cuando POL-REP-2026-2 dice que
  mantiene su recargo). En ambas el reintento corrigió la respuesta.

Límite conocido, corregido en la misma ronda: el verificador inicialmente no recibía el mensaje
del cliente, así que juzgaba las reglas condicionales como universales y marcaba respuestas
correctas por no cubrir el caso contrario. Por ejemplo, marcó como "no respaldada" la
verificación de línea registrada en un robo con violencia (una regla que solo aplica bajo
agresión, sin poder saber que el cliente la había descrito), y la clave telefónica en una
pérdida simple. En el primer caso el texto fijo llegó a pedirle la clave telefónica a la
víctima. Corregido pasando el mensaje del cliente como contexto explícito (marcado como dato,
no como instrucción) y agregando esos dos casos como ejemplos few-shot de qué NO marcar.
Revalidado: ambos casos pasan y la inversión se sigue marcando.

Falso positivo similar, corregido: idempotencia aplicada a una primera solicitud de bloqueo.
En la integración contra Groq con BLOCK_PENDING (`test_live_block_pending_says_in_process`),
el verificador marcó "iniciaré el proceso de bloqueo" invocando POL-BLQ-2026-4 ("una segunda
solicitud no genera una nueva operación"), aunque era la primera solicitud. Solo recibía el
estado (`BLOCK_PENDING`), no si la operación era nueva o repetida. El reintento quedó en
"¿Hay algo más en lo que pueda ayudarte?", que pasó todos los guardrails (no afirma nada) y se
entregó: el cliente no supo que su bloqueo estaba en proceso. Dos correcciones:

- Hecho del sistema explícito para el verificador (`block_operation_fact`): "operación nueva,
  no una solicitud repetida; la idempotencia no aplica", o "solicitud repetida" con
  ALREADY_BLOCKED.
- Red de seguridad en el pipeline (`reports_operation_state`): si en el turno hubo una
  operación (bloqueo, o transacción pending) y un reintento aceptado ya no la menciona, se usa
  el texto fijo del estado y se agrega `state_dropped_after_retry` a `guardrail_triggered`.
  Es un chequeo de presencia ("bloque…", "pendiente/preliminar/liquid…"), no de corrección, y
  solo aplica tras un reintento: una primera respuesta que omita el estado no se intercepta.

Revalidado: 3 de 3 corridas del test pasan, sin reintentos (la red de seguridad no se activó en
vivo; está cubierta por tests offline).

Límite que permanece: no cubre la "verificación de línea delegada al propio cliente"
(sección anterior). Cuando el modelo le pide al cliente "confirme que está comunicándose
desde su número de línea registrado", el verificador lo aprueba, porque la frase parafrasea
POL-BLQ-2026-2 ("verificación de que la comunicación proviene del número de línea
registrado"). El riesgo no está en la política, que exige lo contrario: POL-BLQ-2026-3B pide
validar la línea contra el operador de telecomunicaciones, no con datos que el cliente
declare. Está en que el modelo convierte un control interno del banco en algo que el cliente
confirma. El verificador compara afirmaciones factuales contra los fragmentos y no evalúa si
la respuesta respeta cómo debe ejecutarse un control, así que esta desviación le pasa
inadvertida.

Costo: cada respuesta con citations no vacías agrega al menos una llamada extra al LLM (más
una por cada reintento). Con el plan gratuito de Groq (200.000 tokens/día y 8.000 tokens/minuto),
el sistema completo alcanza para aproximadamente 30 a 50 respuestas por día (estimación a
partir de las corridas de prueba). Los tests de integración pausan 20 segundos entre llamadas
para no superar el límite por minuto.

Comportamiento ante fallo del verificador (fail-closed): si el verificador no puede ejecutarse
(error de API, límite de cuota, timeout o veredicto inválido), la respuesta generada se
bloquea y no se entrega sin verificar. Se responde con un texto fijo que da el siguiente paso
según el flujo (`flow_next_step`) con una frase inicial distinta a la de alucinación
detectada, y se agrega `faithfulness_unverified` a `guardrail_triggered`. No se reintenta.

Trabajo futuro: medir la tasa de falsos positivos y negativos del verificador sobre un conjunto
de evaluación más amplio (hoy la evidencia son corridas dirigidas); evaluar si
`openai/gpt-oss-20b` sirve como verificador más barato, probándolo antes de forma aislada.

## Generación: promesas de acciones no ejecutadas por este canal

Observado: en una prueba de fraude ("no reconozco un cargo de 300 soles en mi tarjeta",
corridas 4#1 y 4#2, también reintentos tras una primera respuesta forzada), el modelo
respondió "Vamos a registrar tu reporte … Generaremos un número de caso en las próximas 24
horas" y "Registraremos su reporte de forma inmediata y le asignaremos un número de caso". No
afirma que la acción ya ocurrió, pero promete acciones futuras que el sistema actual no
ejecuta: el registro del reporte y la asignación de un número de caso no existen, ni siquiera
en el backend simulado (que solo cubre el bloqueo y el estado de la transacción; ver
"Backend transaccional simulado"). Se volvió a observar en la prueba de transacción pending
contra Groq: "En los próximos días recibirás la confirmación de que el reporte ha sido
recibido", una promesa que el verificador de fidelidad no marcó.

Por qué el verificador no la marcó (investigado reconstruyendo el caso contra Groq: mismo
mensaje, mismos chunks de `reportar_fraude`, mismo hecho del sistema `pending`; ver abajo el
alcance de la evidencia):

- Sí vio el texto. En el pipeline, el verificador corre sobre toda respuesta generada cuando
  hay chunks, y si falla la respuesta se bloquea (`faithfulness_unverified`). Como la
  respuesta se entregó tal cual y sin esa marca, el verificador la evaluó y la aprobó.
- La causa no es la lista de exclusiones ("NO cuentes como…": advertencias, derivación a
  asesor, cortesía). En modo diagnóstico, que obliga a clasificar cada oración y a nombrar la
  exclusión aplicada, la promesa nunca se clasificó como excluida y quedó `sin_respaldo` en 4
  de 4 corridas. Esa lista le quita algo de sensibilidad al veredicto binario (con la lista
  la promesa se marcó en 1 de 7 corridas; sin la lista, en 4 de 7), pero aun sin ella se
  aprueba en casi la mitad de los casos.
- Causa 1: la promesa parafrasea un chunk. POL-FRD-2026-1 dice que el registro "constituye
  únicamente la confirmación de recepción del reclamo", así que "recibirás la confirmación de
  que el reporte ha sido recibido" no es un dato inventado sino un eco de ese chunk. Lo que
  no tiene respaldo es otra cosa: (a) el plazo ("próximos días" frente a "de forma inmediata,
  dentro de las primeras 24 horas"), (b) aplicarlo a una transacción pending, donde
  POL-FRD-2026-4 solo dice que el reporte queda como preliminar, y (c) que el sistema vaya a
  enviar algo, cuando ningún componente del MVP envía confirmaciones. El verificador contrasta
  el texto contra los fragmentos y los hechos del sistema, pero no contra lo que el sistema
  puede ejecutar. Por eso (c) no puede detectarlo, y (a) y (b) los aprueba como paráfrasis
  fiel. Es el mismo punto ciego que la "verificación de línea delegada al propio cliente".
- Causa 2: el veredicto binario sin descomposición es inestable. Con `temperature=0` y la
  misma entrada, el prompt actual aprobó la promesa en 6 de 7 corridas. Cuando se le pide
  clasificar oración por oración, la marca en todas.

Alcance de la evidencia: de la respuesta original solo quedó registrada esta frase, así que
se usó una respuesta reconstruida alrededor de ella (con y sin frases de cortesía). Las
muestras son chicas (n=7 por variante con cortesía, n=2 sin ella) y sirven para descartar
hipótesis, no para estimar tasas.

Cuarta aparición, con BLOCK_FAILED (suite de integración, `test_live_block_failed_does_not_say_blocked`,
que pasó porque solo verifica que no se afirme el bloqueo): "Un asesor se pondrá en contacto
con usted a la brevedad para completar el proceso". Este caso muestra que el patrón tiene
dos subtipos que no conviene tratar como el mismo error:

- Promesa sin ningún respaldo estructural. Ejemplos: la confirmación "en los próximos días"
  con la transacción pending, o el número de caso de fraude. Ningún componente del sistema
  (backend simulado, pipeline, estado de la sesión) hace eso: la promesa entera es falsa.
- Promesa con respaldo parcial y un detalle no verificado. El caso de BLOCK_FAILED: el hecho
  central tiene soporte, porque el pipeline marca `requires_human=True` cuando el bloqueo
  falla y el texto fijo de ese estado ya dice "Un asesor dará seguimiento a tu caso". El
  matiz no lo tiene: "a la brevedad" no aparece en ningún chunk (la KB no menciona asesores
  ni plazos de contacto) ni en el estado del sistema. Una salvedad sobre el "hecho central":
  `requires_human` es solo una marca en la respuesta y el MVP no ejecuta ninguna derivación.
  Que un asesor efectivamente contacte al cliente depende de que el consumidor de la API
  actúe sobre esa marca, así que "se pondrá en contacto" (contacto saliente) también afirma
  algo más que lo que la marca garantiza.

Por qué es un riesgo: el cliente puede quedar esperando un número de caso que nunca llega y
no reportar por otro canal. La regla del prompt que prohíbe afirmar operaciones ejecutadas
(`_NO_ACTIONS_RULE` en `app/orchestrator/generator.py`) no cubre explícitamente la promesa de
una acción futura que tampoco se va a cumplir en este MVP.

Mitigación activa: parcial. `_NO_ACTIONS_RULE` reduce el caso más grave (afirmar un éxito ya
ocurrido), pero no impide prometer algo pendiente. Ningún guardrail de código lo detecta, y
el verificador de fidelidad tampoco es una red confiable para este caso (ver causas arriba).

Trabajo futuro: reforzar `_NO_ACTIONS_RULE` para prohibir explícitamente promesas de acciones
futuras cuando no exista una integración (real o simulada) que las respalde. El bloqueo ya está
conectado al backend simulado (el asistente refleja el estado devuelto); el registro de casos
de fraude todavía no. Del lado del verificador: (1) pasar como hecho del sistema lo que el MVP
no ejecuta (p. ej. "este canal no registra casos ni envía confirmaciones") para que una promesa
de seguimiento contradiga un hecho explícito. Esos hechos deberían distinguir los dos subtipos
de arriba en vez de negar todo seguimiento: "no se registran casos ni se envían
confirmaciones" (subtipo sin respaldo, la promesa se marca entera) frente a "el turno quedó
marcado para derivación a un asesor, sin plazo de contacto definido" (subtipo con respaldo
parcial: la derivación está respaldada y solo se marca el detalle agregado, como el plazo).
Un hecho único del tipo "este canal no hace seguimiento" marcaría como falsa la derivación de
BLOCK_FAILED, que sí tiene soporte, y tendría el mismo efecto que el falso positivo de
idempotencia. (2) Evaluar si pedir clasificación por oración
antes del veredicto (el modo diagnóstico de esta investigación) mejora la sensibilidad sin
aumentar los falsos positivos ya corregidos. Tiene un costo en tokens, relevante con el
límite de Groq.

## Retriever: bloqueo estándar recuperado en phishing + bloqueo

Observado: en una prueba con CVV compartido ("mi cvv es 123, bloquea mi tarjeta", corrida
3#1, intents `reportar_intento_phishing` + `bloquear_tarjeta`), el reintento pidió la clave
telefónica ("… indíqueme su documento de identidad y la clave telefónica"), aunque
`AGGRESSION_RULE` ya no está en el prompt para esta combinación. La causa probable es que
`search_by_intent()` recupera POL-BLQ-2026-1 (bloqueo estándar: documento + clave
telefónica), que es obligatorio (`regla_dura`) para el intent de bloqueo, y el modelo lo
sigue: el retriever no excluye chunks incompatibles con phishing. En la corrida 3#2 el
reintento pidió solo el documento.

Por qué es un riesgo: es el mismo problema de fondo que motivó el guardrail
`phone_key_outside_block`, visto desde la recuperación en vez de la generación.
POL-SEG-2026-2 limita la identidad a solo el documento ante phishing, pero el contexto le
entrega al modelo un procedimiento que la contradice, y el sistema sigue generando el intento
en primer lugar.

Mitigación activa: el guardrail `phone_key_outside_block` cubre este caso de forma segura:
la respuesta terminó en texto fijo (`guardrail_triggered = ["sensitive_data_request",
"phone_key_outside_block"]`), nunca en una violación entregada al cliente. El costo es de
utilidad: el cliente recibe un texto fijo en lugar de una respuesta sobre su caso.

Trabajo futuro: excluir POL-BLQ-2026-1 (y cualquier chunk de autenticación estándar) de los
resultados de `search_by_intent()` cuando phishing esté entre los intents detectados, ya que
POL-SEG-2026-2 debe prevalecer sobre el procedimiento estándar de bloqueo en ese escenario.

## Alcance: backend transaccional simulado (mock)

Estado actual: el bloqueo de tarjeta y la consulta del estado de una transacción no están
integrados con el backend del banco. Ambos son simulados en `app/api/mock_backend.py`:

- `simulate_block_request`: BLOCKED 70 %, BLOCK_PENDING 10 %, BLOCK_FAILED 10 %,
  ALREADY_BLOCKED 10 %. BLOCK_REQUESTED tiene peso 0 (solo aparece forzándolo).
- `simulate_transaction_status`: settled 80 %, pending 20 %.
- Los identificadores de tarjeta y de transacción no existen: se usan `card-<session_id>` y
  `txn-<session_id>`.

Es un límite de alcance conocido del MVP, no un error: `action` en `ChatResponse` refleja el
estado devuelto por el mock (ya no es siempre `null`), y el asistente, los guardrails y el
verificador de fidelidad trabajan contra ese estado como si fuera real.

Flujo de bloqueo en dos turnos (POL-BLQ-2026-1/2/3B): el primer turno pide los datos de
identidad y no ejecuta nada; el bloqueo simulado se ejecuta en el siguiente turno de la misma
sesión, si la respuesta anterior del asistente quedó marcada en el historial como pedido de
datos para bloquear y el mensaje del cliente trae un número de documento (6+ dígitos). Los
datos de identidad NO se validan: cualquier número de documento y cualquier clave avanzan el
flujo. La única señal que se lee del texto actual es la presencia de ese número.

Límites asociados:

- Si el cliente responde sin un número de documento, el asistente vuelve a pedir los datos.
- Transacción en hold (POL-FRD-2026-4): la política dice que se informa el plazo estimado de
  liquidación, pero no indica cuánto es. El prompt le prohíbe al modelo dar una cifra; en la
  prueba contra Groq no mencionó el plazo en absoluto, y en cambio mencionó el plazo de 30 días
  del dictamen (POL-FRD-2026-1), que corresponde al caso formal y no al reporte preliminar.
- Cada consulta de fraude simula una transacción nueva: no hay forma de referirse a una
  transacción concreta.

## Persistencia: historial de conversaciones en SQLite

Estado actual: `/api/v1/chat` guarda cada turno en SQLite (`DATABASE_PATH`, por defecto
`data/chat.db`, fuera de git) y recupera el historial de la sesión antes de procesar; los
últimos `HISTORY_CONTEXT_MESSAGES` mensajes se pasan como contexto al classifier y al
generador. `GET /api/v1/chats` y `GET /api/v1/chats/{session_id}` exponen las sesiones para el
frontend.

Límites:

- El mensaje del cliente se guarda enmascarado (`mask_sensitive_data`): claves, CVV, PIN y
  códigos junto a su palabra clave, números de tarjeta y números de 6+ dígitos (documentos)
  quedan como `[dato omitido]`. Es una heurística de patrones: un dato sensible escrito de otra
  forma (p. ej. dígitos separados por espacios fuera de un PAN) podría persistirse.
- El mensaje actual sí se envía completo a Groq (classifier, generador y verificador), como
  antes de esta versión; solo el historial va enmascarado.
- No hay autenticación ni aislamiento por usuario: cualquiera que conozca un `session_id`
  puede leer esa sesión, y `GET /api/v1/chats` lista todas. Aceptable para el MVP local, no
  para un despliegue real.
- SQLite local, sin migraciones ni política de retención.
