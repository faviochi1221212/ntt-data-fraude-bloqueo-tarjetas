import { ShieldIcon } from "./icons";

/**
 * Aviso discreto de que un guardrail ajustó la respuesta. No expone el nombre técnico del
 * guardrail: se nota en la demo sin sonar alarmante para el cliente real.
 */
export default function GuardrailBadge() {
  return (
    <div
      data-testid="guardrail-badge"
      className="flex min-w-0 items-start gap-2.5 rounded-xl border border-caution-border bg-caution-soft px-3.5 py-2.5 text-caution-ink md:min-w-56 md:flex-1"
    >
      <ShieldIcon className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0">
        <p className="text-sm font-medium leading-5">Se aplicó una medida de seguridad</p>
        <p className="text-xs leading-4 text-ink-2">Ajustamos esta respuesta para proteger tus datos.</p>
      </div>
    </div>
  );
}
