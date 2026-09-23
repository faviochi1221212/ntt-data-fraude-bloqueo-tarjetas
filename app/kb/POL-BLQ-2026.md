### CHUNK 1 — Bloqueo estándar
Intent: bloquear_tarjeta
Contenido: Para el bloqueo de una tarjeta en condiciones estándar (sin agresión), el
asistente valida la identidad del cliente mediante documento de identidad y clave
telefónica. El bloqueo se solicita al backend y pasa por los estados solicitado,
bloqueado, pendiente o fallido. El asistente nunca comunica el bloqueo como exitoso
hasta recibir la confirmación de bloqueo efectivo del backend.
Metadatos: {"zona": "autenticacion", "escenario": "3, 6, 7", "tipo": "regla_dura",
"estado": "vigente"}

### CHUNK 2 — Bloqueo bajo robo con violencia
Intent: bloquear_tarjeta
Contenido: Cuando el cliente reporta robo con violencia, se reduce el nivel de
autenticación exigido a documento de identidad y verificación de que la comunicación
proviene del número de línea registrado del cliente, sin exigir clave telefónica.
Se prioriza la ejecución del bloqueo. La captura de la dirección de envío para
reposición se difiere a un contacto posterior con verificación completa, a través
de app con biometría o agencia presencial — nunca por llamada telefónica de
seguimiento.
Metadatos: {"zona": "autenticacion", "escenario": "1, 2 - Rama 3a",
"tipo": "excepcion_seguridad", "estado": "vigente"}

### CHUNK 3 — Línea no coincidente bajo agresión
Intent: bloquear_tarjeta
Contenido: Si la línea desde la que se reporta un robo con violencia no coincide con
el número registrado del cliente, no se aplica la verificación reducida automática.
El caso se deriva de inmediato a un operador humano con prioridad.
Metadatos: {"zona": "autenticacion", "escenario": "1, 2 - excepcion",
"tipo": "regla_dura", "estado": "vigente"}

### CHUNK 3B — Verificación de línea contra spoofing
Intent: bloquear_tarjeta
Contenido: La verificación de línea registrada exigida bajo robo con violencia debe
validarse contra el operador de telecomunicaciones (verificación a nivel de red),
no únicamente contra la cabecera de identificador de llamada entrante, ya que esta
última puede falsificarse mediante troncales VoIP/SIP conociendo solo el documento
de identidad de la víctima. Mientras esa validación reforzada no esté implementada,
el bloqueo ejecutado por esta vía se marca como preventivo temporal sujeto a
ratificación posterior, no como bloqueo definitivo confirmado.
Metadatos: {"zona": "autenticacion", "escenario": "1, 2 - Rama 3a",
"tipo": "regla_dura_seguridad", "estado": "vigente", "origen": "auditoria_r4"}

### CHUNK 4 — Idempotencia de bloqueo
Intent: bloquear_tarjeta
Contenido: Una segunda solicitud de bloqueo sobre una tarjeta que ya está bloqueada
o en proceso de bloqueo no genera una nueva operación. El asistente solo informa el
estado actual de la tarjeta.
Metadatos: {"zona": "operacion", "escenario": "subflujo_bloqueo",
"tipo": "regla_dura", "estado": "vigente"}

### CHUNK 5 — Desbloqueo (límite funcional)
Intent: desbloquear_tarjeta
Contenido: El asistente virtual tiene deshabilitada funcionalmente la reversión o
desbloqueo de cualquier tarjeta, sin excepción. Ante cualquier solicitud de
desbloqueo, no inicia ningún subflujo de autenticación ni captura datos de
seguridad. Responde con una negativa directa y deriva a banca telefónica o atención
presencial, donde se exige validación de factores fuertes de autenticación.
Metadatos: {"zona": "alcance", "escenario": "extension_9", "tipo": "regla_dura",
"estado": "vigente"}
