"use client";

import { useEffect, useRef } from "react";

import type { UIMessage } from "@/lib/types";
import ChatMessage from "./ChatMessage";
import { ShieldIcon } from "./icons";
import TypingIndicator from "./TypingIndicator";

const SUGGESTIONS = [
  "Perdí mi tarjeta, ¿pueden bloquearla?",
  "No reconozco un cargo en mi tarjeta",
  "Me llegó un correo pidiendo mi CVV",
];

interface Props {
  messages: UIMessage[];
  isTyping: boolean;
  loadingHistory: boolean;
  onSuggestion: (text: string) => void;
  onRetry: (message: UIMessage) => void;
  footer?: React.ReactNode;
}

export default function MessageList({ messages, isTyping, loadingHistory, onSuggestion, onRetry, footer }: Props) {
  const endRef = useRef<HTMLDivElement>(null);

  // Al llegar un mensaje (o el indicador) se baja al final. Sin mensajes no: en pantallas bajas
  // recortaría el inicio del estado vacío.
  useEffect(() => {
    if (messages.length === 0 && !isTyping) return;
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, isTyping]);

  // Cuando el teclado achica el viewport, el último mensaje sigue visible. Sin mensajes no hay
  // listener: el resize inicial del viewport recortaría el estado vacío.
  const hasMessages = messages.length > 0;
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport || !hasMessages) return;
    const onResize = () => endRef.current?.scrollIntoView({ block: "end" });
    viewport.addEventListener("resize", onResize);
    return () => viewport.removeEventListener("resize", onResize);
  }, [hasMessages]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain" data-testid="message-list">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-3 py-6 sm:px-6">
        {loadingHistory ? (
          <HistorySkeleton />
        ) : messages.length === 0 ? (
          <EmptyState onSuggestion={onSuggestion} />
        ) : (
          <div aria-live="polite" aria-relevant="additions" className="flex flex-col gap-6">
            {messages.map((m) => (
              <ChatMessage key={m.id} message={m} onRetry={onRetry} />
            ))}
          </div>
        )}
        {isTyping && <TypingIndicator />}
        {footer}
        <div ref={endRef} />
      </div>
    </div>
  );
}

function EmptyState({ onSuggestion }: { onSuggestion: (text: string) => void }) {
  return (
    <div className="flex flex-col items-center px-2 pt-6 text-center sm:pt-14" data-testid="empty-state">
      <span className="flex size-14 items-center justify-center rounded-2xl bg-primary-soft text-primary">
        <ShieldIcon className="size-7" />
      </span>
      <h2 className="mt-5 text-xl font-semibold text-ink text-balance sm:text-2xl">Soy Centinela. ¿En qué puedo ayudarte hoy?</h2>
      <p className="mt-2 max-w-md text-[15px] leading-6 text-ink-2 text-pretty">
        Bloquea tu tarjeta, reporta un cargo que no reconoces o consulta cómo proteger tus datos.
      </p>
      <div className="mt-7 flex w-full max-w-md flex-col gap-2 sm:max-w-xl sm:flex-row sm:flex-wrap sm:justify-center">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onSuggestion(s)}
            className="rounded-xl border border-line bg-surface px-4 py-2.5 text-left text-sm text-ink-2 transition hover:border-primary-border hover:bg-primary-soft hover:text-primary-ink focus-visible:outline-2 focus-visible:outline-primary sm:text-center"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

function HistorySkeleton() {
  return (
    <div className="flex flex-col gap-6" aria-label="Cargando conversación" role="status">
      {[60, 80, 45].map((w, i) => (
        <div key={i} className={`flex ${i % 2 === 0 ? "justify-end" : "justify-start"}`}>
          <div className="h-12 animate-pulse rounded-2xl bg-line/60" style={{ width: `${w}%` }} />
        </div>
      ))}
    </div>
  );
}
