"use client";

import { useEffect, useRef } from "react";

interface Props {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
}

const FOCUSABLE = 'button:not([disabled]), [href], input, textarea, select, [tabindex]:not([tabindex="-1"])';

/**
 * Contenedor del sidebar por debajo de lg: panel fijo que se desliza desde la izquierda,
 * fuera del flujo del layout (nunca comprime ni se superpone al chat cuando está cerrado).
 * Cerrado queda `inert`: fuera de pantalla y fuera del orden de tabulación.
 */
export default function MobileDrawer({ open, onClose, children }: Props) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const { overflow } = document.body.style;
    document.body.style.overflow = "hidden";
    panelRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key !== "Tab" || !panelRef.current) return;
      const items = Array.from(panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = overflow;
      previouslyFocused?.focus();
    };
  }, [open, onClose]);

  return (
    <div className="lg:hidden" data-testid="mobile-drawer" data-open={open}>
      <div
        aria-hidden="true"
        onClick={onClose}
        className={`fixed inset-0 z-40 bg-ink/40 transition-opacity duration-200 ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Conversaciones"
        inert={!open}
        // Cerrado queda visibility:hidden (oculto también para lectores de pantalla). Solo al cerrar
        // se anima visibility, para que pase a hidden al final del deslizamiento; al abrir cambia
        // de inmediato y el panel acepta el foco desde el primer frame.
        className={`fixed inset-y-0 left-0 z-50 w-[min(85vw,20rem)] shadow-xl duration-200 ease-out ${
          open ? "visible translate-x-0 transition-[translate]" : "invisible -translate-x-full transition-[translate,visibility]"
        }`}
      >
        {children}
      </div>
    </div>
  );
}
