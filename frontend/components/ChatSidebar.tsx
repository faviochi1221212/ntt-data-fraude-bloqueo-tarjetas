import { formatRelativeDay } from "@/lib/session";
import type { ChatSessionSummary } from "@/lib/types";
import { ChatIcon, PlusIcon, ShieldIcon } from "./icons";

interface Props {
  sessions: ChatSessionSummary[];
  activeSessionId: string;
  loading: boolean;
  error: string | null;
  onNewChat: () => void;
  onSelect: (sessionId: string) => void;
  /** Solo en el drawer móvil: botón de cierre en la cabecera. */
  headerAction?: React.ReactNode;
}

export default function ChatSidebar({
  sessions,
  activeSessionId,
  loading,
  error,
  onNewChat,
  onSelect,
  headerAction,
}: Props) {
  return (
    <nav aria-label="Conversaciones" className="flex h-full min-h-0 flex-col bg-surface">
      <div className="flex items-center gap-3 px-4 pt-4 pb-3">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary text-white">
          <ShieldIcon className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-ink">Centinela</p>
          <p className="truncate text-xs text-ink-3">Tarjetas, fraude y protección</p>
        </div>
        {headerAction}
      </div>

      <div className="px-3 pb-3">
        <button
          type="button"
          onClick={onNewChat}
          data-testid="new-chat"
          className="flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-primary px-4 text-sm font-medium text-white transition hover:bg-primary-hover active:scale-[0.98] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          <PlusIcon className="size-4" />
          Nueva conversación
        </button>
      </div>

      <p className="px-4 pt-2 pb-1.5 text-xs font-medium tracking-wide text-ink-3 uppercase">Conversaciones anteriores</p>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        {loading && sessions.length === 0 && (
          <ul className="space-y-1.5 px-1" aria-label="Cargando conversaciones">
            {[0, 1, 2].map((i) => (
              <li key={i} className="h-14 animate-pulse rounded-xl bg-app" />
            ))}
          </ul>
        )}
        {error && <p className="px-3 py-2 text-sm leading-5 text-ink-2">{error}</p>}
        {!loading && !error && sessions.length === 0 && (
          <p className="px-3 py-2 text-sm leading-5 text-ink-3">Aún no tienes conversaciones guardadas.</p>
        )}
        <ul className="space-y-0.5">
          {sessions.map((session) => {
            const active = session.session_id === activeSessionId;
            return (
              <li key={session.session_id}>
                <button
                  type="button"
                  onClick={() => onSelect(session.session_id)}
                  aria-current={active ? "true" : undefined}
                  className={`flex w-full items-start gap-2.5 rounded-xl px-3 py-2.5 text-left transition focus-visible:outline-2 focus-visible:outline-primary ${
                    active ? "bg-primary-soft text-primary-ink" : "text-ink hover:bg-app"
                  }`}
                >
                  <ChatIcon className={`mt-0.5 size-4 shrink-0 ${active ? "text-primary" : "text-ink-3"}`} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">{session.summary || "Conversación"}</span>
                    <span className="block text-xs text-ink-3">{formatRelativeDay(session.last_message_at)}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </nav>
  );
}
