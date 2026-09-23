### CHUNK 1 — Registro vs. dictamen
Intent: reportar_fraude
Contenido: Todo reporte de una transacción no reconocida se registra y genera un
número de caso de forma inmediata, dentro de las primeras 24 horas. Esto constituye
únicamente la confirmación de recepción del reclamo, no un resultado. El área de
fraude cuenta con un plazo de hasta 30 días calendario, conforme a la normativa
vigente, para emitir el dictamen que determina si la transacción fue o no
fraudulenta.
Metadatos: {"zona": "plazos", "escenario": "3, 7", "tipo": "informativo",
"estado": "vigente"}

### CHUNK 2 — Guardrail de no confirmar devolución
Intent: reportar_fraude
Contenido: Mientras el caso esté en investigación, el asistente no debe confirmar
ni negar la devolución del monto reportado, ni prometer un plazo o monto de
devolución. Si el dictamen confirma fraude, la devolución se procesa según las
políticas de la marca de la tarjeta y el reglamento interno.
Metadatos: {"zona": "guardrail", "escenario": "3, 7", "tipo": "regla_dura",
"estado": "vigente"}

### CHUNK 3 — Denuncia policial
Intent: reportar_fraude
Contenido: No es obligatorio presentar una denuncia policial para iniciar el
reclamo de una transacción no reconocida.
Metadatos: {"zona": "requisitos", "escenario": "3", "tipo": "informativo",
"estado": "vigente"}

### CHUNK 4 — Transacciones en estado pendiente
Intent: reportar_fraude
Contenido: Antes de generar un ID de reclamo formal, se valida el estado de la
transacción reportada. Si está en estado pendiente o no liquidada (hold,
autorización no asentada), no se abre el caso de disputa formal de inmediato —
las marcas de tarjeta no procesan contracargos sobre autorizaciones no liquidadas.
Se informa al cliente el plazo estimado de liquidación y se registra el reporte
como preliminar; el caso formal se genera solo cuando la transacción liquide o
caiga.
Metadatos: {"zona": "guardrail", "escenario": "3, 7", "tipo": "regla_dura",
"estado": "vigente", "origen": "auditoria_r4"}
