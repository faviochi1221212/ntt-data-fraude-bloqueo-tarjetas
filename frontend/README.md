# Frontend: Asistente de seguridad

Interfaz de chat en Next.js 16 (App Router) + Tailwind CSS v4 para el backend FastAPI de este
repositorio.

## Requisitos

- Node.js 20 o superior (probado con Node 24).
- El backend corriendo en `http://localhost:8000` (ver el README de la raíz).

## Configuración

```bash
cd frontend
npm install
cp .env.local.example .env.local   # NEXT_PUBLIC_API_URL=http://localhost:8000
```

El navegador no llama al backend directo. Pide `/api/v1/*` al propio servidor de Next, y el
rewrite de `next.config.ts` lo reenvía a `NEXT_PUBLIC_API_URL`. Por eso el backend no necesita
CORS. Si cambias `NEXT_PUBLIC_API_URL`, reinicia `next dev` (los rewrites se leen al arrancar).

## Correr en local

```bash
npm run dev          # http://localhost:3000
```

### Desde un celular Android en la misma red Wi-Fi

1. Levanta el frontend expuesto en la red local:

   ```bash
   npm run dev:lan    # equivale a: next dev --hostname 0.0.0.0
   ```

2. Busca la IP de tu laptop en la red local (Windows: `ipconfig`, "Dirección IPv4";
   macOS/Linux: `ipconfig getifaddr en0` o `hostname -I`).
3. En Chrome del celular abre `http://<IP-de-tu-laptop>:3000`.

El backend puede seguir escuchando solo en `localhost:8000`: la llamada al backend la hace el
servidor de Next en tu laptop, no el navegador del celular. Si no carga:

- Permite Node.js en el firewall para redes privadas (Windows lo pregunta la primera vez).
- `next.config.ts` ya permite los orígenes de red privada (`allowedDevOrigins`: `192.168.*.*`,
  `10.*.*.*`, `172.*.*.*`). Si tu red usa otro rango, agrégalo ahí.

Revisión manual en el celular (lo que la emulación no cubre por completo):

- [ ] Al tocar el campo de mensaje, el teclado no tapa el input ni el botón de enviar.
- [ ] El menú (☰) abre el panel de conversaciones y el chat no se comprime detrás.
- [ ] Las tarjetas de estado se apilan en columna y nada se corta a los lados.
- [ ] Al girar el celular a horizontal no aparece scroll horizontal.

## Scripts

| Script | Qué hace |
| --- | --- |
| `npm run dev` / `npm run dev:lan` | Servidor de desarrollo (solo local / expuesto en la red). |
| `npm run build` y `npm start` | Build y servidor de producción. |
| `npm run lint` / `npm run typecheck` | ESLint y TypeScript. |
| `npm run test:e2e` | Pruebas responsive con Playwright (ver abajo). |

## Pruebas responsive

```bash
npx playwright install chromium   # solo la primera vez
npm run test:e2e
```

Hacen el build, levantan el servidor en el puerto 3100 y simulan el backend dentro del
navegador (`page.route`), así que no necesitan FastAPI ni gastan cuota de Groq. Corren en
Chromium con estos perfiles: Pixel 7, iPhone 14 (viewport y touch de iPhone, motor Chromium),
Android 360×640, 320×568, tablet 768×1024 y desktop 1440×900. Verifican:

- que no haya scroll horizontal y que nada se salga del viewport, incluido texto largo sin espacios;
- sidebar fijo desde `lg` (1024 px) sin superponerse al chat; por debajo, drawer fuera del
  flujo que no cambia el tamaño del chat, con foco atrapado y cierre con Esc;
- tarjetas en columna por debajo de `md` (768 px) y en fila desde `md`;
- input y botón de enviar visibles con el viewport reducido al 55 % de alto (aproximación del
  teclado virtual);
- confirmación de envío, indicador "escribiendo…", errores en lenguaje humano con reintento,
  "Finalizar sesión" y continuar una sesión anterior con su mismo `session_id`.

Las capturas de cada perfil quedan en `e2e/.results/`. La emulación no reemplaza a un celular
real: el teclado virtual solo se aproxima (ver la revisión manual de arriba).

## Límites conocidos y pendientes

- **`detail` de las tarjetas de estado:** hoy muestra el texto que devuelve el backend simulado
  ("Estado devuelto por el backend simulado (mock)."). Es intencional, para que en la demo se vea
  que el estado viene del mock. En una integración real ese campo debería ocultarse o
  reemplazarse por un texto pensado para el cliente final (`components/StatusCard.tsx`).
- **Validación en un celular real:** las pruebas emulan Android e iPhone en Chromium. El teclado
  virtual solo se aproxima y el iPhone no corre con WebKit. Falta la revisión manual en un
  dispositivo (ver la checklist de "Desde un celular Android").

## Estructura

```
app/          layout (fuente, viewport), página y estilos globales (tokens de color)
components/   ChatShell (estado), ChatSidebar, MobileDrawer, ChatHeader, MessageList,
              ChatMessage, ChatInput, StatusCard, GuardrailBadge, HandoffCard,
              TypingIndicator, ErrorNotice, icons
lib/          types (espejo de app/models/schemas.py), api (cliente fetch), status
              (estado técnico → texto humano), session (ids y fechas)
e2e/          pruebas Playwright
```

## Decisiones de diseño

- **Paleta:** primario índigo `#4F46E5` sobre fondo `#F8FAFC`. El ámbar se reserva para
  "Se aplicó una medida de seguridad" y para el bloqueo fallido; no hay rojo. Los errores de
  conexión usan tonos neutros.
- **Teclado en Android:** `interactive-widget=resizes-content` en el viewport, altura `100dvh`
  y el input fuera del área con scroll. En iPhone (WebKit ignora `interactive-widget`) se usa
  un listener de `visualViewport` como respaldo.
- **Textos de estado:** `lib/status.ts` traduce `BLOCKED`, `BLOCK_PENDING`, `pending`, etc. a
  lenguaje humano, alineado con los textos fijos del backend. Nunca se muestra el nombre técnico
  del estado ni del guardrail.
- **Derivación a asesor:** la tarjeta no promete contacto ni plazos, porque el backend solo marca
  `requires_human` (ver `LIMITATIONS.md`).
- **`session_id`:** usa `crypto.randomUUID()` cuando existe. Si no (fuera de HTTPS o
  localhost, como el celular que entra por IP), usa `crypto.getRandomValues`.
