import { CloseIcon, CloudOffIcon } from "./icons";

/** Error de conexión en lenguaje humano. Neutro (sin rojo): no es una alerta de seguridad. */
export default function ErrorNotice({ message, onDismiss }: { message: string; onDismiss?: () => void }) {
  return (
    <div
      role="alert"
      data-testid="error-notice"
      className="flex items-start gap-2.5 rounded-xl border border-line bg-surface px-3.5 py-2.5 text-sm text-ink-2 shadow-sm"
    >
      <CloudOffIcon className="mt-0.5 size-4 shrink-0 text-ink-3" />
      <p className="min-w-0 flex-1 break-words leading-5">{message}</p>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Cerrar aviso"
          className="-m-1.5 shrink-0 rounded-lg p-1.5 text-ink-3 hover:bg-app hover:text-ink focus-visible:outline-2 focus-visible:outline-primary"
        >
          <CloseIcon className="size-4" />
        </button>
      )}
    </div>
  );
}
