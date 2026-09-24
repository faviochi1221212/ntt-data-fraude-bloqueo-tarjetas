import { ShieldIcon } from "./icons";

export default function TypingIndicator() {
  return (
    <div className="flex items-end gap-2.5 animate-message-in" role="status" aria-live="polite" data-testid="typing">
      <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary-soft text-primary">
        <ShieldIcon className="size-4" />
      </span>
      <div className="flex items-center gap-1 rounded-2xl rounded-bl-md border border-line bg-surface px-4 py-3.5">
        {[0, 150, 300].map((delay) => (
          <span
            key={delay}
            className="size-1.5 rounded-full bg-ink-3 animate-typing"
            style={{ animationDelay: `${delay}ms` }}
          />
        ))}
        <span className="sr-only">El asistente está escribiendo…</span>
      </div>
    </div>
  );
}
