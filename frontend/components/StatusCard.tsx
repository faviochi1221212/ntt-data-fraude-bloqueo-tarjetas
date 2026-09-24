import { describeAction, type Severity } from "@/lib/status";
import type { ActionResult } from "@/lib/types";

const TONE: Record<Severity, string> = {
  success: "border-success-border bg-success-soft text-success-ink",
  progress: "border-primary-border bg-primary-soft text-primary-ink",
  attention: "border-caution-border bg-caution-soft text-caution-ink",
};

/** Estado real de la operación del turno (bloqueo o transacción), en lenguaje humano. */
export default function StatusCard({ action }: { action: ActionResult }) {
  const status = describeAction(action);
  return (
    <div
      role="status"
      data-testid="status-card"
      className={`flex min-w-0 items-start gap-3 rounded-xl border px-3.5 py-3 md:min-w-56 md:flex-1 ${TONE[status.severity]}`}
    >
      <span className="text-base leading-6" aria-hidden="true">
        {status.icon}
      </span>
      <div className="min-w-0">
        <p className="text-sm font-semibold leading-6">{status.title}</p>
        <p className="text-sm leading-5 text-ink-2 break-words">{status.description}</p>
        {action.detail && <p className="mt-1 text-xs leading-4 text-ink-3 break-words">{action.detail}</p>}
      </div>
    </div>
  );
}
