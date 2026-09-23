### CHUNK 1 — Regla de oro de seguridad
Intent: reportar_intento_phishing
Contenido: El banco nunca solicita el CVV, la clave completa ni el número completo
de tarjeta (PAN) por ningún canal, bajo ninguna circunstancia, ni siquiera si el
cliente lo pide o dice tener autorización. El asistente rechaza cualquier
solicitud de repetir estos datos.
Metadatos: {"zona": "guardrail_critico", "escenario": "13", "tipo": "regla_dura",
"estado": "vigente"}

### CHUNK 2 — Alcance de identidad limitada en phishing
Intent: reportar_intento_phishing
Contenido: Ante un reporte de phishing, el asistente valida identidad de forma
limitada (solo documento de identidad, sin clave ni datos de tarjeta). Con esta
identidad puede: registrar el incidente, escalar a seguridad, e iniciar bloqueo
preventivo si el cliente ya entregó datos. No puede: consultar o confirmar
saldo/movimientos, abrir una disputa directamente, ni modificar datos de la
cuenta.
Metadatos: {"zona": "guardrail_critico", "escenario": "13", "tipo": "regla_dura",
"estado": "vigente"}

### CHUNK 3 — Derivación de phishing
Intent: reportar_intento_phishing
Contenido: Todo reporte de phishing, haya entregado datos el cliente o no, se
deriva y registra en el canal oficial de seguridad.
Metadatos: {"zona": "escalamiento", "escenario": "13", "tipo": "regla_dura",
"estado": "vigente"}
