import { LogoutIcon, MenuIcon } from "./icons";

interface Props {
  onOpenMenu: () => void;
  onEndSession: () => void;
  canEndSession: boolean;
  menuButtonRef?: React.Ref<HTMLButtonElement>;
}

export default function ChatHeader({ onOpenMenu, onEndSession, canEndSession, menuButtonRef }: Props) {
  return (
    <header className="flex min-h-16 shrink-0 items-center gap-2 border-b border-line bg-surface/95 px-3 pt-[env(safe-area-inset-top)] backdrop-blur sm:px-6">
      <button
        ref={menuButtonRef}
        type="button"
        onClick={onOpenMenu}
        aria-label="Abrir conversaciones"
        data-testid="menu-button"
        className="-ml-1 flex size-11 shrink-0 items-center justify-center rounded-xl text-ink-2 hover:bg-app hover:text-ink focus-visible:outline-2 focus-visible:outline-primary lg:hidden"
      >
        <MenuIcon />
      </button>

      <div className="min-w-0 flex-1">
        <h1 className="truncate text-[15px] font-semibold text-ink">Asistente de seguridad</h1>
        <p className="flex items-center gap-1.5 text-xs text-ink-3">
          <span className="size-1.5 shrink-0 rounded-full bg-success-ink" aria-hidden="true" />
          <span className="truncate">
            Disponible<span className="hidden sm:inline"> · respuestas basadas en políticas del banco</span>
          </span>
        </p>
      </div>

      <button
        type="button"
        onClick={onEndSession}
        disabled={!canEndSession}
        data-testid="end-session"
        aria-label="Finalizar sesión"
        className="flex h-10 min-w-10 shrink-0 items-center justify-center gap-1.5 rounded-xl border border-line px-2.5 text-sm xs:px-3 font-medium text-ink-2 transition hover:bg-app hover:text-ink focus-visible:outline-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-50"
      >
        <LogoutIcon className="size-4" />
        <span className="hidden sm:inline">Finalizar sesión</span>
        <span className="hidden xs:inline sm:hidden">Finalizar</span>
      </button>
    </header>
  );
}
