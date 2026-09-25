# Centinela — asistente conversacional de prevención de fraude y bloqueo de tarjetas

Centinela es un asistente de chat para clientes de un banco. Atiende cinco situaciones:
bloquear una tarjeta, reportar un cargo que no se reconoce, pedir una tarjeta nueva, reportar
un intento de phishing y pedir un desbloqueo. Responde siguiendo las políticas internas del
banco y no permite que el modelo de lenguaje invente reglas, pida datos sensibles ni dé por
hecha una operación que no ocurrió.

Es un **MVP**: el backend transaccional del banco está simulado (ver
[Alcance y limitaciones](#alcance-y-limitaciones)).

---

## Índice

1. [Qué problema resuelve](#qué-problema-resuelve)
2. [Arquitectura](#arquitectura)
3. [Cómo funciona un turno de conversación](#cómo-funciona-un-turno-de-conversación)
4. [Base de conocimiento (políticas)](#base-de-conocimiento-políticas)
5. [Guardrails: las defensas del asistente](#guardrails-las-defensas-del-asistente)
6. [Backend simulado y flujo de bloqueo en dos turnos](#backend-simulado-y-flujo-de-bloqueo-en-dos-turnos)
7. [Historial de conversaciones](#historial-de-conversaciones)
8. [Frontend](#frontend)
9. [API](#api)
10. [Instalación y ejecución](#instalación-y-ejecución)
11. [Pruebas](#pruebas)
12. [Estructura del repositorio](#estructura-del-repositorio)
13. [Alcance y limitaciones](#alcance-y-limitaciones)
14. [Historial del desarrollo](#historial-del-desarrollo)

---

## Qué problema resuelve

En un canal de atención de fraude un error del asistente cuesta caro. Por ejemplo:

- Pedirle al cliente el CVV o la clave completa, justo lo que hace un estafador.
- Decir "tu tarjeta ya está bloqueada" cuando el bloqueo falló o sigue pendiente.
- Prometer una devolución antes de que exista un dictamen.
- Explicarle a un posible atacante por qué canal llegan los códigos de verificación.

Centinela combina **RAG** (respuestas basadas solo en las políticas del banco) con varias
capas de **guardrails en código** que revisan cada respuesta antes de enviarla. Si una
respuesta no pasa las validaciones, se regenera una vez y, si vuelve a fallar, se reemplaza
por un texto fijo y seguro.

## Arquitectura

```
┌───────────────────────┐      /api/v1/*      ┌──────────────────────────────────────────┐
│ Frontend (Next.js 16) │ ──── (rewrite) ───▶ │ Backend (FastAPI)                        │
│ chat responsive       │                     │                                          │
└───────────────────────┘                     │  ChatPipeline                            │
                                              │   ├─ Classifier: hard triggers + Groq    │
                                              │   ├─ Retriever: embeddings en memoria    │
                                              │   ├─ Backend simulado (bloqueo / txn)    │
                                              │   ├─ Generador: Groq                     │
                                              │   └─ Guardrails + verificador de         │
                                              │      fidelidad (Groq)                    │
                                              │                                          │
                                              │  SQLite: historial de conversaciones     │
                                              └──────────────────────────────────────────┘
```

| Capa | Tecnología |
| --- | --- |
| API | FastAPI, Pydantic v2, pydantic-settings |
| LLM | Groq (`openai/gpt-oss-120b` por defecto) con structured outputs estrictos |
| Embeddings | fastembed (ONNX, local, sin API key) con `paraphrase-multilingual-MiniLM-L12-v2` |
| Búsqueda | Similitud coseno en memoria con NumPy |
| Persistencia | SQLite |
| Frontend | Next.js 16 (App Router), React 19, Tailwind CSS v4, TypeScript |
| Pruebas | pytest, Playwright |

## Cómo funciona un turno de conversación

El flujo está en `app/orchestrator/pipeline.py` (`ChatPipeline.process`):

1. **Historial.** Se recuperan de SQLite los últimos mensajes de la sesión
   (`HISTORY_CONTEXT_MESSAGES`, 6 por defecto) para darles contexto al classifier y al
   generador.
2. **Clasificación de intents**, en dos capas (`app/orchestrator/classifier.py`):
   - **Hard triggers** (`hard_triggers.py`): expresiones regulares deterministas, sin modelos
     ni red. Aseguran que los casos críticos (alguien pidió el CVV, la clave o el PAN; el
     cliente quiere desbloquear) se detecten siempre, aunque el LLM falle. Esta capa prefiere
     los falsos positivos a los falsos negativos.
   - **Groq multi-intent**: clasifica el mensaje en uno o varios intents con confianza, usando
     un JSON Schema estricto (el modelo solo puede producir JSON válido).
   - El resultado es la unión de ambas capas. Intents posibles: `bloquear_tarjeta`,
     `desbloquear_tarjeta`, `reportar_fraude`, `solicitar_tarjeta_nueva`,
     `reportar_intento_phishing` y `fuera_de_alcance`.
3. **Fuera de alcance.** Si el único intent es `fuera_de_alcance` (p. ej. "hola"), se responde
   con un texto fijo de presentación, sin RAG ni LLM.
4. **Recuperación** (`app/rag/retriever.py`, `search_by_intent`): se buscan los fragmentos de
   política de los intents detectados. Los fragmentos obligatorios (reglas duras y guardrails
   críticos) entran siempre y por orden de prioridad. Los demás entran por similitud semántica
   (`RAG_TOP_K`, `RAG_MIN_SCORE`).
5. **Backend simulado.** Si el turno ejecuta un bloqueo o hay un reporte de fraude, se consulta
   el mock (`app/api/mock_backend.py`) y el estado devuelto se pasa al generador como un hecho
   del sistema.
6. **Generación** (`app/orchestrator/generator.py`): Groq redacta la respuesta usando solo los
   fragmentos recuperados, el estado real de la operación y reglas de prompt específicas por
   flujo.
7. **Guardrails de salida**: se valida la respuesta (ver la sección siguiente).
8. **Respuesta y persistencia.** Se devuelve un `ChatResponse` con la respuesta, las citas a
   las políticas, la acción ejecutada, los guardrails que se activaron y si hay que derivar a
   un humano. El turno se guarda en SQLite con el mensaje del cliente enmascarado.

Si Groq no responde y ningún hard trigger se activa, o no se recuperan fragmentos, el
asistente responde con un texto fijo que deriva a un asesor (`requires_human: true`).

## Base de conocimiento (políticas)

Las políticas están en `app/kb/` como Markdown dividido en *chunks*. Cada chunk tiene un
intent, un contenido y metadatos (`zona`, `escenario`, `tipo`, `estado`).

| Política | Tema | Ejemplos de reglas |
| --- | --- | --- |
| `POL-BLQ-2026` | Bloqueo | Bloqueo estándar con documento + clave telefónica; autenticación reducida en robo con violencia; verificación de línea contra spoofing; idempotencia; el desbloqueo no se hace por este canal |
| `POL-FRD-2026` | Fraude | Registro vs. dictamen; nunca confirmar una devolución; denuncia policial; transacciones pendientes |
| `POL-REP-2026` | Reposición | Modalidades de reposición, exoneración de costos, punto de entrega |
| `POL-SEG-2026` | Seguridad | Regla de oro (el banco nunca pide CVV, clave completa ni PAN); identidad limitada en phishing; derivación al canal de seguridad |

El campo `tipo` define la prioridad en la recuperación (`guardrail_critico` >
`regla_dura_seguridad` > `regla_dura` / `excepcion_seguridad` > resto). La consistencia de estos
metadatos se comprueba en `tests/test_kb_consistency.py`.

## Guardrails: las defensas del asistente

El prompt es la primera línea de defensa. Los guardrails son el respaldo en código que
captura lo que el modelo deja pasar. Cada uno define cómo detectar el problema, una
instrucción de corrección para **un único reintento** y un **texto fijo** para cuando el
reintento también falla.

| Guardrail | Qué evita | Archivo |
| --- | --- | --- |
| `sensitive_data_request` | Que el asistente pida o repita CVV, PAN, clave completa o PIN | `response_validators.py` |
| `phone_key_outside_block` | Pedir la clave telefónica fuera del bloqueo estándar (p. ej. en phishing) | `response_validators.py` |
| `auth_channel_disclosure` | Describir por qué canal llegan los datos de autenticación | `response_validators.py` |
| `block_status_mismatch` | Decir "bloqueada" cuando el backend devolvió pendiente, fallido, etc. | `action_validators.py` |
| `formal_case_while_pending` | Hablar de un caso formal cuando la transacción aún está pendiente | `action_validators.py` |
| `unsupported_claim` | Afirmaciones que no están en las políticas citadas ni en los hechos del sistema. Lo decide un **segundo LLM verificador** | `faithfulness.py` |
| `faithfulness_unverified` | Si el verificador falla, la respuesta se bloquea (*fail-closed*) | `faithfulness.py` |
| `state_dropped_after_retry` | Que un reintento "limpio" omita el estado real de la operación | `action_validators.py` |
| `transaction_pending_hold` | Marca informativa: la transacción reportada está en hold | `action_validators.py` |

Reglas de ejecución:

- **Revalidación cruzada:** la respuesta regenerada se valida contra *todos* los guardrails
  que aplican, no solo contra el que pidió el reintento, porque un reintento puede corregir un
  problema y crear otro.
- **Jerarquía de severidad:** si hay que usar un texto fijo, gana el del guardrail más severo
  (datos sensibles > clave telefónica > canal de autenticación > estado del bloqueo > caso
  formal pendiente > fidelidad).
- **El estado real manda:** si en el turno hubo una operación simulada, el texto fijo siempre
  informa el estado real devuelto por el backend.
- Todos los guardrails que se activaron se devuelven en `guardrail_triggered` y el frontend los
  muestra.

## Backend simulado y flujo de bloqueo en dos turnos

No hay integración con el core bancario. `app/api/mock_backend.py` simula:

- **Bloqueo:** `BLOCKED` 70 %, `BLOCK_PENDING` 10 %, `BLOCK_FAILED` 10 %, `ALREADY_BLOCKED` 10 %.
- **Estado de transacción:** `settled` 80 %, `pending` 20 %.

`force_result` permite fijar el resultado en las pruebas.

El bloqueo sigue la política en dos turnos:

1. **Recolección:** el cliente pide bloquear y el asistente solicita los datos de identidad.
   Esa respuesta queda marcada en el historial como pendiente de identidad.
2. **Ejecución:** si el siguiente mensaje de la sesión trae un número de documento (6 o más
   dígitos), se ejecuta el bloqueo simulado y se informa el estado real. Un `BLOCK_FAILED`
   deriva a un humano.

En un reporte de fraude se consulta primero el estado de la transacción: si está `pending`, el
asistente no habla de un caso formal (POL-FRD-2026-4).

## Historial de conversaciones

- Cada turno se guarda en SQLite (`DATABASE_PATH`, por defecto `data/chat.db`, fuera de git).
- El mensaje del cliente se guarda **enmascarado** (`mask_sensitive_data`): claves, CVV, PIN,
  números de tarjeta y documentos se reemplazan por `[dato omitido]`.
- El frontend lista las sesiones anteriores y permite retomarlas.

## Frontend

Está en `frontend/`: una interfaz de chat en Next.js 16 + Tailwind CSS v4.

- Diseño responsive: sidebar fijo en escritorio y drawer en móvil.
- **Tarjetas de estado** con el resultado real de la operación (bloqueo o transacción) en
  lenguaje claro.
- **Insignias de guardrail** y **tarjeta de derivación** cuando el caso requiere un humano.
- Indicador de "escribiendo…", errores con reintento, "Finalizar sesión" y continuación de
  sesiones anteriores.
- Proxy: el navegador pide `/api/v1/*` a Next y un rewrite lo reenvía al backend. No hace falta
  CORS y funciona desde un celular en la misma red Wi-Fi (`npm run dev:lan`).

Más detalles en [`frontend/README.md`](frontend/README.md).

## API

| Método | Ruta | Descripción |
| --- | --- | --- |
| `GET` | `/health` | Healthcheck |
| `POST` | `/api/v1/chat` | Procesa un mensaje y devuelve la respuesta del asistente |
| `GET` | `/api/v1/chats` | Lista las sesiones guardadas (la más reciente primero) |
| `GET` | `/api/v1/chats/{session_id}` | Historial completo de una sesión |

Ejemplo:

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "demo-1", "message": "Me robaron la tarjeta, quiero bloquearla"}'
```

```json
{
  "session_id": "demo-1",
  "answer": "…",
  "source": "kb",
  "citations": [{ "chunk_id": "POL-BLQ-2026-1", "source": "POL-BLQ-2026", "score": 0.61 }],
  "action": null,
  "guardrail_triggered": [],
  "requires_human": false
}
```

`action` informa la operación simulada (`block_card` o `check_transaction_status`) con su
`status`. La documentación interactiva queda en `http://localhost:8000/docs`.

## Instalación y ejecución

### Requisitos

- Python 3.10 o superior
- Node.js 20 o superior
- Una API key de [Groq](https://console.groq.com/)

### Backend

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env        # completar GROQ_API_KEY
uvicorn app.main:app --reload --port 8000
```

La primera vez, fastembed descarga el modelo de embeddings desde Hugging Face. El índice se
arma en memoria con el primer request.

Variables principales (`.env.example`):

| Variable | Default | Uso |
| --- | --- | --- |
| `GROQ_API_KEY` | — | Obligatoria para clasificar, generar y verificar |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Debe admitir structured outputs estrictos |
| `EMBEDDING_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Modelo local de embeddings |
| `KB_PATH` | `app/kb` | Carpeta de políticas |
| `RAG_TOP_K` / `RAG_MIN_SCORE` | `4` / `0.0` | Recuperación de chunks no obligatorios |
| `DATABASE_PATH` | `data/chat.db` | SQLite del historial |
| `HISTORY_CONTEXT_MESSAGES` | `6` | Mensajes previos que se pasan como contexto |
| `MOCK_API_BASE_URL` | — | Reservada para una integración futura; hoy no se usa |

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local   # NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev                        # http://localhost:3000
```

## Pruebas

### Backend

```bash
pytest
```

- **Offline** (sin red): usan dobles del LLM y del verificador y fijan el backend simulado.
  Cubren guardrails de datos sensibles, fidelidad, flujo de bloqueo en dos turnos,
  persistencia y consistencia de la KB.
- **Integración con Groq**: se saltan si no hay `GROQ_API_KEY`. Recorren escenarios reales:
  robo con violencia, pérdida simple, pedido de CVV, saludo fuera de alcance, bloqueo completo
  y fraude con transacción pendiente.
- **Pruebas manuales** del classifier y del retriever:

  ```bash
  python -m tests.test_classifier_manual "me pidieron el cvv por teléfono"
  python -m tests.test_retriever_manual
  ```

### Frontend

```bash
cd frontend
npm run lint && npm run typecheck
npx playwright install chromium   # solo la primera vez
npm run test:e2e
```

Las pruebas de Playwright simulan el backend en el navegador y revisan la interfaz en Pixel 7,
iPhone 14, 360×640, 320×568, tablet y escritorio.

## Estructura del repositorio

```
app/
├── main.py                  # App FastAPI y /health
├── api/
│   ├── chat.py              # Endpoints /chat y /chats
│   ├── mock_backend.py      # Backend transaccional simulado
│   └── router.py
├── core/config.py           # Configuración (pydantic-settings)
├── guardrails/
│   ├── response_validators.py  # Datos sensibles, clave telefónica, canal de autenticación
│   ├── action_validators.py    # Estado del bloqueo y de la transacción
│   └── faithfulness.py         # Verificador de fidelidad (segundo LLM)
├── kb/                      # Políticas del banco (POL-*.md)
├── models/schemas.py        # Contratos Pydantic (ChatRequest, ChatResponse…)
├── orchestrator/
│   ├── hard_triggers.py     # Capa determinista del classifier
│   ├── classifier.py        # Capa Groq multi-intent
│   ├── generator.py         # Generación con reglas de prompt por flujo
│   └── pipeline.py          # Orquestación de un turno
├── rag/                     # Carga de la KB y retriever
└── storage/chat_store.py    # Historial en SQLite
frontend/                    # Interfaz Next.js
tests/                       # pytest (offline + integración + manuales)
LIMITATIONS.md               # Limitaciones conocidas y riesgos observados
```

## Alcance y limitaciones

Las limitaciones están documentadas en detalle en [`LIMITATIONS.md`](LIMITATIONS.md). Las
principales:

- **Backend simulado:** los bloqueos y los estados de transacción son aleatorios y los datos de
  identidad no se validan. Cualquier número de documento avanza el flujo.
- **Robo con violencia vs. pérdida simple:** la entidad `hubo_agresion` todavía no se extrae.
  La distinción depende de una regla de prompt, no de código.
- **Riesgos de generación observados:** promesas de acciones que este canal no ejecuta,
  inversión de condiciones y verificación de línea delegada al cliente. Están mitigados en
  parte con el verificador de fidelidad y documentados con sus casos.
- **Sin autenticación:** cualquiera que conozca un `session_id` puede leer esa sesión. Sirve
  para el MVP local, no para producción.
- **Enmascarado heurístico:** un dato sensible escrito de una forma no prevista podría
  guardarse. El mensaje actual sí se envía completo a Groq.

## Historial del desarrollo

El proyecto se construyó por etapas (ver `git log`):

1. Scaffold del backend FastAPI y configuración centralizada.
2. Classifier de intents en dos capas (hard triggers + Groq).
3. Base de conocimiento y recuperación semántica en memoria (RAG), con prioridad por niveles.
4. Endpoint `POST /api/v1/chat` con generación RAG y el primer guardrail (canal de
   autenticación).
5. Guardrails de salida por severidad con revalidación cruzada.
6. Guardrail de fidelidad con un LLM verificador y textos fijos por flujo.
7. Backend simulado de bloqueo y transacciones, historial en SQLite y guardrails de estado.
8. Documentación de limitaciones y riesgos observados.
9. Frontend Next.js responsive con tarjetas de estado y proxy al backend.
10. Identidad del asistente: **Centinela**.
