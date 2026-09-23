# Limitaciones conocidas

## Retriever: robo con violencia vs. pérdida simple (intent `bloquear_tarjeta`)

El retriever no distingue robo con violencia de pérdida simple dentro del intent
`bloquear_tarjeta`, porque ambos comparten el mismo intent y la entidad `hubo_agresion`
no se extrae todavía.

Consecuencia: en una pérdida simple (p. ej. "perdí mi tarjeta, ¿me pueden desactivar?")
el resultado de `search_by_intent()` incluye igual los chunks propios de la agresión
(POL-BLQ-2026-2, excepción de autenticación reducida; POL-BLQ-2026-3B, verificación de
línea contra spoofing), porque son obligatorios para todo el intent.

Mitigación prevista (pendiente de implementar en el endpoint `/chat`): el prompt del
sistema instruirá explícitamente al LLM a aplicar la excepción de autenticación reducida
solo cuando el usuario describa violencia o agresión de forma explícita, nunca por pérdida
simple. Hasta que eso exista, no hay ninguna mitigación activa.

Trabajo futuro: extracción robusta de la entidad `hubo_agresion` en el orquestador.
