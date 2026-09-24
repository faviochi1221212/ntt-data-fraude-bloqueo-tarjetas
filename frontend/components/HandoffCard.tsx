import { HeadsetIcon } from "./icons";

/**
 * requires_human: el caso necesita un asesor. Se presenta como una opción, no como un error.
 * No promete contacto ni plazos: el MVP solo marca la derivación (ver LIMITATIONS.md).
 */
export default function HandoffCard() {
  return (
    <div
      data-testid="handoff-card"
      className="flex min-w-0 items-start gap-3 rounded-xl border border-primary-border bg-surface px-3.5 py-3 md:min-w-56 md:flex-1"
    >
      <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary-soft text-primary">
        <HeadsetIcon className="size-4" />
      </span>
      <div className="min-w-0">
        <p className="text-sm font-semibold leading-6 text-ink">Este caso requiere atención de un asesor</p>
        <p className="text-sm leading-5 text-ink-2">
          Tu consulta quedó marcada para que un asesor te ayude a continuar. Estás en buenas manos.
        </p>
      </div>
    </div>
  );
}
