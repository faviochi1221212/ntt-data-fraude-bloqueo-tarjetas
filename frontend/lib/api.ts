import type {
  ActionResult,
  ChatResponse,
  ChatSessionDetail,
  ChatSessionSummary,
  StoredMessage,
  UIMessage,
} from "./types";
import { uuid } from "./session";

// Rutas relativas: el rewrite de next.config.ts las reenvía al backend (NEXT_PUBLIC_API_URL).
const BASE = "/api/v1";
const REQUEST_TIMEOUT_MS = 120_000;

/** Error con un mensaje apto para mostrar al usuario (nunca un stack trace). */
export class ApiError extends Error {
  constructor(
    public readonly userMessage: string,
    public readonly status?: number,
  ) {
    super(userMessage);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      signal: init?.signal ?? AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") {
      throw new ApiError("El asistente está tardando más de lo normal. Intenta de nuevo en unos segundos.");
    }
    throw new ApiError("No pudimos conectar con el asistente. Revisa tu conexión e intenta de nuevo.");
  }

  if (!response.ok) {
    if (response.status === 404) throw new ApiError("No encontramos esta conversación.", 404);
    if (response.status >= 500) {
      // 500/502/504 del proxy de Next suelen significar que el backend no está disponible.
      throw new ApiError("El servicio no está disponible en este momento. Intenta de nuevo en unos minutos.", response.status);
    }
    throw new ApiError("No pudimos procesar tu mensaje. Intenta reformularlo.", response.status);
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError("Recibimos una respuesta inesperada del servicio. Intenta de nuevo.");
  }
}

export function sendMessage(sessionId: string, message: string): Promise<ChatResponse> {
  return request<ChatResponse>("/chat", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, message }),
  });
}

export function listChats(): Promise<ChatSessionSummary[]> {
  return request<ChatSessionSummary[]>("/chats", { cache: "no-store" });
}

export function getChat(sessionId: string): Promise<ChatSessionDetail> {
  return request<ChatSessionDetail>(`/chats/${encodeURIComponent(sessionId)}`, { cache: "no-store" });
}

// --- Adaptadores a UIMessage -------------------------------------------------------------

export function fromChatResponse(response: ChatResponse): UIMessage {
  return {
    id: uuid(),
    role: "assistant",
    content: response.answer,
    timestamp: new Date().toISOString(),
    action: response.action,
    guardrailTriggered: response.guardrail_triggered,
    requiresHuman: response.requires_human,
  };
}

function isActionResult(value: unknown): value is ActionResult {
  return typeof value === "object" && value !== null && typeof (value as ActionResult).name === "string";
}

/** El historial guarda action/guardrails/requires_human en metadata: se reconstruyen las tarjetas. */
export function fromStoredMessage(message: StoredMessage, index: number): UIMessage {
  const meta = message.metadata ?? {};
  const guardrails = Array.isArray(meta.guardrail_triggered)
    ? meta.guardrail_triggered.filter((g): g is string => typeof g === "string")
    : [];
  return {
    id: `${message.timestamp}-${index}`,
    role: message.role,
    content: message.content,
    timestamp: message.timestamp,
    action: isActionResult(meta.action) ? meta.action : null,
    guardrailTriggered: guardrails,
    requiresHuman: meta.requires_human === true,
    delivery: message.role === "user" ? "sent" : undefined,
  };
}
