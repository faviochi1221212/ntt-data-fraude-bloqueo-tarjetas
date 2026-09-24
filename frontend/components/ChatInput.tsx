"use client";

import { useEffect, useRef } from "react";

import { SendIcon } from "./icons";

interface Props {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  disabled?: boolean;
}

const MAX_HEIGHT_PX = 144; // ~6 líneas; después, scroll interno.

export default function ChatInput({ value, onChange, onSend, disabled }: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const canSend = value.trim().length > 0 && !disabled;

  // Autoexpansión del textarea.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [value]);

  // Respaldo para navegadores que ignoran interactive-widget (WebKit en iPhone): al abrirse el
  // teclado, el visual viewport se achica y se vuelve a traer el input a la vista.
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    const onResize = () => {
      if (document.activeElement === ref.current) ref.current?.scrollIntoView({ block: "nearest" });
    };
    viewport.addEventListener("resize", onResize);
    return () => viewport.removeEventListener("resize", onResize);
  }, []);

  function submit() {
    if (canSend) onSend();
  }

  return (
    <form
      className="shrink-0 border-t border-line bg-surface/95 px-3 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur sm:px-6"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="mx-auto flex w-full max-w-3xl items-end gap-2">
        <label htmlFor="chat-input" className="sr-only">
          Escribe tu mensaje
        </label>
        <textarea
          id="chat-input"
          ref={ref}
          rows={1}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            // Enter envía; Shift+Enter hace salto de línea. Se ignora durante la composición (IME).
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="Escribe tu consulta…"
          enterKeyHint="send"
          autoComplete="off"
          className="min-h-11 min-w-0 flex-1 resize-none rounded-2xl border border-line bg-app px-4 py-2.5 text-base leading-6 text-ink placeholder:text-ink-3 focus:border-primary focus:bg-surface focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
        <button
          type="submit"
          disabled={!canSend}
          aria-label="Enviar mensaje"
          data-testid="send-button"
          className="flex size-11 shrink-0 items-center justify-center rounded-full bg-primary text-white transition hover:bg-primary-hover active:scale-95 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:bg-line disabled:text-ink-3"
        >
          <SendIcon className="size-5" />
        </button>
      </div>
      <p className="mx-auto mt-2 max-w-3xl px-1 text-center text-xs leading-4 text-ink-3">
        Nunca compartas tu CVV, clave completa ni el número completo de tu tarjeta.
      </p>
    </form>
  );
}
