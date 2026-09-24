// Espejo de app/models/schemas.py (backend FastAPI). Mantener sincronizado.

export type ResponseSource = "kb" | "api" | "guardrail" | "static";

export interface Citation {
  chunk_id: string;
  source: string;
  score: number | null;
}

export interface ActionResult {
  name: string; // "block_card" | "check_transaction_status"
  success: boolean;
  status: string | null; // BLOCKED, BLOCK_PENDING, ..., "pending" | "settled"
  reference_id: string | null;
  detail: string | null;
}

export interface ChatResponse {
  session_id: string;
  answer: string;
  source: ResponseSource;
  citations: Citation[];
  action: ActionResult | null;
  guardrail_triggered: string[];
  requires_human: boolean;
}

export interface ChatSessionSummary {
  session_id: string;
  summary: string; // primer mensaje del usuario, truncado
  last_message_at: string; // ISO 8601 (UTC)
}

export interface StoredMessage {
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  // En los mensajes del asistente: source, guardrail_triggered, action, citations, requires_human, ...
  metadata: Record<string, unknown>;
}

export interface ChatSessionDetail {
  session_id: string;
  messages: StoredMessage[];
}

/** Mensaje tal como lo pinta la UI (en vivo o reconstruido desde el historial). */
export interface UIMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  action?: ActionResult | null;
  guardrailTriggered?: string[];
  requiresHuman?: boolean;
  /** Solo mensajes del usuario: "sending" mientras se espera, "failed" si no llegó al backend. */
  delivery?: "sending" | "sent" | "failed";
}
