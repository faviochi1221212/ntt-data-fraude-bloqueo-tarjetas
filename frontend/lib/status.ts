import type { ActionResult } from "./types";

export type Severity = "success" | "progress" | "attention";

export interface HumanStatus {
  severity: Severity;
  icon: string;
  title: string;
  description: string;
}

// Textos al cliente: sin nombres técnicos de estado. Coinciden con los textos fijos del backend
// (app/guardrails/action_validators.py) para no contradecir lo que dice el asistente.
const BLOCK_STATUS: Record<string, HumanStatus> = {
  BLOCKED: {
    severity: "success",
    icon: "✅",
    title: "Tarjeta bloqueada",
    description: "El bloqueo de tu tarjeta fue confirmado.",
  },
  ALREADY_BLOCKED: {
    severity: "success",
    icon: "✅",
    title: "Tu tarjeta ya estaba bloqueada",
    description: "No fue necesario realizar una nueva operación.",
  },
  BLOCK_PENDING: {
    severity: "progress",
    icon: "⏳",
    title: "Bloqueo en proceso",
    description: "Te confirmaremos cuando el bloqueo sea efectivo.",
  },
  BLOCK_REQUESTED: {
    severity: "progress",
    icon: "⏳",
    title: "Solicitud de bloqueo recibida",
    description: "Te confirmaremos cuando el bloqueo sea efectivo.",
  },
  BLOCK_FAILED: {
    severity: "attention",
    icon: "⚠️",
    title: "No pudimos completar el bloqueo",
    description: "Hubo un inconveniente al procesar el bloqueo de tu tarjeta.",
  },
};

const TRANSACTION_STATUS: Record<string, HumanStatus> = {
  pending: {
    severity: "progress",
    icon: "⏳",
    title: "Transacción pendiente de liquidación",
    description: "Tu reporte quedó registrado como preliminar hasta que la transacción se liquide.",
  },
  settled: {
    severity: "success",
    icon: "✅",
    title: "Transacción verificada",
    description: "La transacción reportada ya está liquidada.",
  },
};

const UNKNOWN: HumanStatus = {
  severity: "progress",
  icon: "⏳",
  title: "Operación registrada",
  description: "Estamos procesando tu solicitud.",
};

export function describeAction(action: ActionResult): HumanStatus {
  const status = action.status ?? "";
  if (action.name === "block_card") return BLOCK_STATUS[status] ?? UNKNOWN;
  if (action.name === "check_transaction_status") return TRANSACTION_STATUS[status] ?? UNKNOWN;
  return UNKNOWN;
}
