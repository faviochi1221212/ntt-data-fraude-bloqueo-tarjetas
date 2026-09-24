import { expect, test, type Page } from "@playwright/test";

// --- Backend simulado en el navegador --------------------------------------------------

const CHATS = [
  { session_id: "s-1", summary: "perdí mi tarjeta, ¿me pueden desactivar?", last_message_at: new Date().toISOString() },
  { session_id: "s-2", summary: "no reconozco un cargo de 300 soles en mi tarjeta", last_message_at: "2026-09-20T15:00:00+00:00" },
];

const BLOCK_PENDING_RESPONSE = {
  session_id: "x",
  answer: "Gracias. Tu solicitud de bloqueo está **en proceso** y te confirmaremos cuando sea efectivo.",
  source: "kb",
  citations: [{ chunk_id: "POL-BLQ-2026-1", source: "POL-BLQ-2026", score: 0.41 }],
  action: {
    name: "block_card",
    success: false,
    status: "BLOCK_PENDING",
    reference_id: null,
    detail: "Estado devuelto por el backend simulado (mock).",
  },
  guardrail_triggered: ["sensitive_data_request"],
  requires_human: true,
};

const HISTORY = {
  session_id: "s-2",
  messages: [
    { role: "user", content: "no reconozco un cargo de 300 soles en mi tarjeta", timestamp: "2026-09-20T15:00:00+00:00", metadata: {} },
    {
      role: "assistant",
      content: "Tu reporte quedó registrado como preliminar: la transacción sigue pendiente de liquidación.",
      timestamp: "2026-09-20T15:00:05+00:00",
      metadata: {
        action: { name: "check_transaction_status", success: true, status: "pending", reference_id: null, detail: null },
        guardrail_triggered: ["transaction_pending_hold"],
        requires_human: false,
      },
    },
  ],
};

interface MockOptions {
  chatResponse?: object;
  chatDelayMs?: number;
  failChat?: boolean;
}

async function mockBackend(page: Page, { chatResponse = BLOCK_PENDING_RESPONSE, chatDelayMs = 0, failChat = false }: MockOptions = {}) {
  await page.route("**/api/v1/chats", (route) => route.fulfill({ json: CHATS }));
  await page.route("**/api/v1/chats/*", (route) => route.fulfill({ json: HISTORY }));
  await page.route("**/api/v1/chat", async (route) => {
    if (failChat) return route.abort("connectionrefused");
    if (chatDelayMs) await new Promise((r) => setTimeout(r, chatDelayMs));
    return route.fulfill({ json: chatResponse });
  });
}

// --- Utilidades de layout ------------------------------------------------------------------

const isMobile = (page: Page) => page.viewportSize()!.width < 1024;

async function expectNoHorizontalOverflow(page: Page) {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(scrollWidth, "sin scroll horizontal").toBeLessThanOrEqual(innerWidth);
}

/** Ningún elemento visible del chat se sale por la derecha del viewport. */
async function expectInsideViewport(page: Page, selector: string) {
  const width = page.viewportSize()!.width;
  for (const box of await page.locator(selector).evaluateAll((els) =>
    els.map((el) => el.getBoundingClientRect()).map((r) => ({ left: r.left, right: r.right })),
  )) {
    expect(box.left, `${selector} dentro del viewport`).toBeGreaterThanOrEqual(-0.5);
    expect(box.right, `${selector} dentro del viewport`).toBeLessThanOrEqual(width + 0.5);
  }
}

async function sendMessage(page: Page, text: string) {
  await page.getByLabel("Escribe tu mensaje").fill(text);
  await page.getByTestId("send-button").click();
}

// --- Pruebas -----------------------------------------------------------------------------------

test("layout inicial: sin desborde y sidebar según el ancho", async ({ page }, testInfo) => {
  await mockBackend(page);
  await page.goto("/");
  await expect(page.getByTestId("empty-state")).toBeVisible();
  expect(await page.getByTestId("message-list").evaluate((el) => el.scrollTop), "el estado vacío arranca arriba").toBe(0);
  await expectNoHorizontalOverflow(page);
  await expectInsideViewport(page, "[data-testid=empty-state] button");

  const main = (await page.getByTestId("chat-main").boundingBox())!;
  const vw = page.viewportSize()!.width;

  if (isMobile(page)) {
    await expect(page.getByTestId("desktop-sidebar")).toBeHidden();
    await expect(page.getByTestId("menu-button")).toBeVisible();
    // Drawer cerrado: completamente fuera de pantalla, el chat ocupa todo el ancho.
    const panel = (await page.getByRole("dialog", { name: "Conversaciones", includeHidden: true }).boundingBox())!;
    expect(panel.x + panel.width).toBeLessThanOrEqual(0.5);
    expect(main.x).toBe(0);
    expect(main.width).toBeCloseTo(vw, 0);
  } else {
    const sidebar = (await page.getByTestId("desktop-sidebar").boundingBox())!;
    await expect(page.getByTestId("menu-button")).toBeHidden();
    expect(sidebar.x + sidebar.width, "sidebar y chat no se superponen").toBeLessThanOrEqual(main.x + 0.5);
    await expect(page.getByTestId("desktop-sidebar").getByText(CHATS[0].summary)).toBeVisible();
  }
  await page.screenshot({ path: testInfo.outputPath("01-inicio.png") });
});

test("drawer móvil: abre sobre el chat sin comprimirlo, cierra y carga el historial", async ({ page }, testInfo) => {
  test.skip(!isMobile(page), "solo por debajo de lg");
  await mockBackend(page);
  await page.goto("/");

  const mainBefore = (await page.getByTestId("chat-main").boundingBox())!;
  await page.getByTestId("menu-button").click();
  const dialog = page.getByRole("dialog", { name: "Conversaciones" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Cerrar conversaciones" })).toBeFocused();
  await page.waitForTimeout(260); // fin de la transición

  const panel = (await dialog.boundingBox())!;
  const vw = page.viewportSize()!.width;
  expect(panel.x).toBeGreaterThanOrEqual(0);
  expect(panel.x + panel.width).toBeLessThanOrEqual(vw * 0.86);
  const mainOpen = (await page.getByTestId("chat-main").boundingBox())!;
  expect(mainOpen, "el chat no cambia de tamaño con el drawer abierto").toEqual(mainBefore);
  await expectNoHorizontalOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("02-drawer.png") });

  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Conversaciones" })).toBeHidden();
  await expect(page.getByTestId("menu-button")).toBeFocused();

  // Elegir un chat carga el historial (con sus tarjetas) y cierra el drawer.
  await page.getByTestId("menu-button").click();
  await page.getByRole("dialog", { name: "Conversaciones" }).getByText(CHATS[1].summary).click();
  await expect(page.getByRole("dialog", { name: "Conversaciones" })).toBeHidden();
  await expect(page.getByTestId("status-card")).toContainText("Transacción pendiente de liquidación");
  await expect(page.getByTestId("guardrail-badge")).toBeVisible();
});

test("envío: confirmación, escribiendo… y tarjetas apiladas en móvil / en fila en desktop", async ({ page }, testInfo) => {
  await mockBackend(page, { chatDelayMs: 900 });
  await page.goto("/");
  await sendMessage(page, "mi documento es 45871236");

  await expect(page.getByTestId("user-message")).toContainText("mi documento es 45871236");
  await expect(page.getByTestId("delivery")).toHaveText("Enviando…");
  await expect(page.getByTestId("typing")).toBeVisible();
  await expect(page.getByLabel("Escribe tu mensaje")).toHaveValue("");

  await expect(page.getByTestId("assistant-message")).toBeVisible();
  await expect(page.getByTestId("typing")).toBeHidden();
  await expect(page.getByTestId("delivery")).toContainText("Enviado ✓");

  const status = page.getByTestId("status-card");
  await expect(status).toContainText("Bloqueo en proceso");
  await expect(status).not.toContainText("BLOCK_PENDING");
  await expect(page.getByTestId("guardrail-badge")).toContainText("Se aplicó una medida de seguridad");
  await expect(page.getByTestId("guardrail-badge")).not.toContainText("sensitive_data_request");
  await expect(page.getByTestId("handoff-card")).toContainText("Este caso requiere atención de un asesor");
  await expect(page.locator("strong", { hasText: "en proceso" })).toBeVisible();

  const cards = page.getByTestId("message-cards").locator(":scope > div");
  await expect(cards).toHaveCount(3);
  const boxes = await cards.evaluateAll((els) => els.map((el) => el.getBoundingClientRect().toJSON()));
  if (page.viewportSize()!.width < 768) {
    for (let i = 1; i < boxes.length; i++) {
      expect(boxes[i].x, "misma columna").toBeCloseTo(boxes[0].x, 0);
      expect(boxes[i].y, "apiladas").toBeGreaterThanOrEqual(boxes[i - 1].y + boxes[i - 1].height - 0.5);
    }
  } else {
    expect(boxes[1].y, "en fila").toBeCloseTo(boxes[0].y, 0);
    expect(boxes[1].x).toBeGreaterThan(boxes[0].x);
  }
  await expectNoHorizontalOverflow(page);
  await expectInsideViewport(page, "[data-testid=message-cards] > div");
  await expectInsideViewport(page, "[data-testid=user-message] > div, [data-testid=assistant-message]");
  await page.screenshot({ path: testInfo.outputPath("03-tarjetas.png"), fullPage: true });
});

test("teclado virtual: con el viewport reducido, el input y enviar siguen visibles", async ({ page }, testInfo) => {
  test.skip(!isMobile(page), "solo móvil/tablet");
  await mockBackend(page);
  await page.goto("/");
  await sendMessage(page, "perdí mi tarjeta");
  await expect(page.getByTestId("assistant-message")).toBeVisible();

  const { width, height } = page.viewportSize()!;
  const input = page.getByLabel("Escribe tu mensaje");
  await input.focus();
  // Aproximación del teclado de Android con interactive-widget=resizes-content: el layout
  // viewport pierde ~45% de alto.
  const keyboardHeight = Math.round(height * 0.55);
  await page.setViewportSize({ width, height: keyboardHeight });
  await page.waitForTimeout(150);

  for (const target of [input, page.getByTestId("send-button")]) {
    const box = (await target.boundingBox())!;
    expect(box.y, "no queda por encima del viewport").toBeGreaterThanOrEqual(0);
    expect(box.y + box.height, "no lo tapa el teclado").toBeLessThanOrEqual(keyboardHeight + 0.5);
  }
  await expect(page.getByTestId("menu-button")).toBeInViewport();
  await expectNoHorizontalOverflow(page);
  await input.fill("sigo aquí");
  await page.getByTestId("send-button").click();
  await expect(page.getByTestId("user-message").last()).toContainText("sigo aquí");
  await page.screenshot({ path: testInfo.outputPath("04-teclado.png") });
});

test("error de conexión: mensaje humano, sin detalles técnicos, con reintento", async ({ page }, testInfo) => {
  await mockBackend(page, { failChat: true });
  await page.goto("/");
  await sendMessage(page, "hola");

  const notice = page.getByTestId("error-notice");
  await expect(notice).toContainText("No pudimos conectar con el asistente");
  await expect(notice).not.toContainText(/TypeError|fetch|stack|Error:/i);
  await expect(page.getByRole("button", { name: /Reintentar/ })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("05-error.png") });

  // El reintento funciona cuando el backend vuelve.
  await page.unroute("**/api/v1/chat");
  await page.route("**/api/v1/chat", (route) => route.fulfill({ json: BLOCK_PENDING_RESPONSE }));
  await page.getByRole("button", { name: /Reintentar/ }).click();
  await expect(page.getByTestId("assistant-message")).toBeVisible();
  await expect(page.getByTestId("delivery")).toContainText("Enviado ✓");
});

test("texto largo sin espacios no desborda", async ({ page }) => {
  const long = "https://banco.example/reportes/" + "a".repeat(220);
  await mockBackend(page, { chatResponse: { ...BLOCK_PENDING_RESPONSE, answer: `Revisa ${long}` } });
  await page.goto("/");
  await sendMessage(page, "x".repeat(180));
  await expect(page.getByTestId("assistant-message")).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectInsideViewport(page, "[data-testid=user-message] > div, [data-testid=assistant-message]");
});

test("finalizar sesión limpia la vista y vuelve a una conversación nueva", async ({ page }) => {
  await mockBackend(page);
  await page.goto("/");
  await expect(page.getByTestId("end-session")).toBeDisabled();
  await sendMessage(page, "perdí mi tarjeta");
  await expect(page.getByTestId("assistant-message")).toBeVisible();
  await page.getByTestId("end-session").click();
  await expect(page.getByTestId("empty-state")).toBeVisible();
  await expect(page.getByTestId("assistant-message")).toHaveCount(0);
});

test("al elegir un chat anterior, los mensajes siguen en esa misma sesión", async ({ page }) => {
  await mockBackend(page);
  await page.goto("/");
  if (isMobile(page)) await page.getByTestId("menu-button").click();
  const nav = isMobile(page) ? page.getByRole("dialog", { name: "Conversaciones" }) : page.getByTestId("desktop-sidebar");
  await nav.getByText(CHATS[1].summary).click();
  await expect(page.getByTestId("assistant-message")).toHaveCount(1);

  const request = page.waitForRequest("**/api/v1/chat");
  await sendMessage(page, "¿cuándo se liquida?");
  expect((await request).postDataJSON()).toEqual({ session_id: "s-2", message: "¿cuándo se liquida?" });
  await expect(page.getByTestId("assistant-message")).toHaveCount(2);
});
