import { Fragment } from "react";

import { formatTime } from "@/lib/session";
import type { UIMessage } from "@/lib/types";
import GuardrailBadge from "./GuardrailBadge";
import HandoffCard from "./HandoffCard";
import { RetryIcon, ShieldIcon } from "./icons";
import StatusCard from "./StatusCard";

/** El modelo usa **negritas** de Markdown: se respetan sin interpretar ningún HTML. */
function RichText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*\n]+\*\*)/g);
  return (
    <>
      {parts.map((part, i) =>
        part.startsWith("**") && part.endsWith("**") && part.length > 4 ? (
          <strong key={i} className="font-semibold">
            {part.slice(2, -2)}
          </strong>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </>
  );
}

interface Props {
  message: UIMessage;
  onRetry?: (message: UIMessage) => void;
}

export default function ChatMessage({ message, onRetry }: Props) {
  const time = formatTime(message.timestamp);

  if (message.role === "user") {
    return (
      <div className="flex flex-col items-end gap-1 animate-message-in" data-testid="user-message">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-[15px] leading-6 text-white whitespace-pre-wrap break-words sm:max-w-[75%]">
          {message.content}
        </div>
        <DeliveryStatus message={message} time={time} onRetry={onRetry} />
      </div>
    );
  }

  const hasCards = Boolean(message.action) || Boolean(message.guardrailTriggered?.length) || message.requiresHuman;
  return (
    <div className="flex items-start gap-2.5 animate-message-in" data-testid="assistant-message">
      <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-primary-soft text-primary">
        <ShieldIcon className="size-4" />
      </span>
      <div className="flex min-w-0 max-w-[calc(100%-2.625rem)] flex-col gap-2 sm:max-w-[80%]">
        <div className="rounded-2xl rounded-tl-md border border-line bg-surface px-4 py-3 text-[15px] leading-6 text-ink whitespace-pre-wrap break-words shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
          <RichText text={message.content} />
        </div>
        {hasCards && (
          <div className="flex flex-col gap-2 md:flex-row md:flex-wrap" data-testid="message-cards">
            {message.action && <StatusCard action={message.action} />}
            {message.requiresHuman && <HandoffCard />}
            {Boolean(message.guardrailTriggered?.length) && <GuardrailBadge />}
          </div>
        )}
        {time && <span className="px-1 text-xs text-ink-3">{time}</span>}
      </div>
    </div>
  );
}

function DeliveryStatus({ message, time, onRetry }: { message: UIMessage; time: string; onRetry?: Props["onRetry"] }) {
  if (message.delivery === "failed") {
    return (
      <button
        type="button"
        onClick={() => onRetry?.(message)}
        className="flex items-center gap-1 rounded-md px-1 text-xs font-medium text-ink-2 underline-offset-2 hover:text-ink hover:underline focus-visible:outline-2 focus-visible:outline-primary"
      >
        <RetryIcon className="size-3.5" />
        No se envió · Reintentar
      </button>
    );
  }
  return (
    <span className="px-1 text-xs text-ink-3" data-testid="delivery">
      {message.delivery === "sending" ? "Enviando…" : `${time ? `${time} · ` : ""}Enviado ✓`}
    </span>
  );
}
