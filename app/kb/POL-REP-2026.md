### CHUNK 1 — Modalidades de reposición
Intent: solicitar_tarjeta_nueva
Contenido: La tarjeta digital es la opción de reposición por defecto ante cualquier
solicitud con urgencia declarada: se emite de forma inmediata a través de la
aplicación móvil, sin costo adicional, y requiere que el cliente tenga la app
instalada y enrolada. La reposición de tarjeta física tiene dos modalidades:
estándar (3 a 5 días hábiles, a nivel nacional) y express (24 a 48 horas, solo
Lima Metropolitana).
Metadatos: {"zona": "reposicion", "escenario": "1, 2 - Rama 3b",
"tipo": "informativo", "estado": "vigente"}

### CHUNK 2 — Exoneración de costos
Intent: solicitar_tarjeta_nueva
Contenido: La reposición física estándar por reporte de robo con violencia o por
apertura de un caso de investigación de fraude no genera costo de emisión ni
comisión de envío. Esta exoneración aplica desde el momento en que se abre el caso,
no se exige esperar el dictamen de 30 días. La modalidad express mantiene su
recargo en todos los casos, incluidos fraude o robo con violencia.
Metadatos: {"zona": "costos", "escenario": "reposicion", "tipo": "regla_dura",
"estado": "vigente"}

### CHUNK 3 — Punto de entrega
Intent: solicitar_tarjeta_nueva
Contenido: La dirección de envío debe coincidir con el domicilio registrado, salvo
que el cliente prefiera recojo en cualquier agencia a nivel nacional, disponible
sin necesidad de autorización del área de Riesgos. El envío a una dirección
distinta que no sea una agencia sí requiere esa autorización y se deriva a un
canal de atención humana.
Metadatos: {"zona": "entrega", "escenario": "reposicion", "tipo": "informativo",
"estado": "vigente"}
