"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, fromChatResponse, fromStoredMessage, getChat, listChats, sendMessage } from "@/lib/api";
import { newSessionId, uuid } from "@/lib/session";
import type { ChatSessionSummary, UIMessage } from "@/lib/types";
import ChatHeader from "./ChatHeader";
import ChatInput from "./ChatInput";
import ChatSidebar from "./ChatSidebar";
import ErrorNotice from "./ErrorNotice";
import { CloseIcon } from "./icons";
import MessageList from "./MessageList";
import MobileDrawer from "./MobileDrawer";

function userMessage(err: unknown): string {
  return err instanceof ApiError ? err.userMessage : "Ocurrió un problema inesperado. Intenta de nuevo.";
}

export default function ChatShell() {
  // El id no se pinta en el HTML, así que generarlo también en el render del servidor no
  // desalinea la hidratación; el cliente usa el suyo.
  const [sessionId, setSessionId] = useState(newSessionId);
  const [messages, setMessages] = useState<UIMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Sesión vigente al momento de resolver una petición: si el usuario cambió de chat mientras
  // esperaba, la respuesta tardía no se mezcla con la conversación nueva.
  const activeSession = useRef(sessionId);

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await listChats());
      setSessionsError(null);
    } catch (err) {
      setSessionsError(userMessage(err));
    } finally {
      setSessionsLoading(false);
    }
  }, []);

  const startNewChat = useCallback(() => {
    const id = newSessionId();
    activeSession.current = id;
    setSessionId(id);
    setMessages([]);
    setDraft("");
    setError(null);
    setIsSending(false);
    setLoadingHistory(false);
    setDrawerOpen(false);
  }, []);

  // Carga inicial del listado (suscripción a un sistema externo: setState solo en callbacks).
  useEffect(() => {
    let cancelled = false;
    listChats()
      .then((list) => {
        if (cancelled) return;
        setSessions(list);
        setSessionsError(null);
      })
      .catch((err) => {
        if (!cancelled) setSessionsError(userMessage(err));
      })
      .finally(() => {
        if (!cancelled) setSessionsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selectChat = useCallback(
    async (id: string) => {
      setDrawerOpen(false);
      if (id === activeSession.current) return;
      activeSession.current = id;
      setSessionId(id);
      setMessages([]);
      setError(null);
      setIsSending(false);
      setLoadingHistory(true);
      try {
        const detail = await getChat(id);
        if (activeSession.current !== id) return;
        setMessages(detail.messages.map(fromStoredMessage));
      } catch (err) {
        if (activeSession.current === id) setError(userMessage(err));
      } finally {
        if (activeSession.current === id) setLoadingHistory(false);
      }
    },
    [],
  );

  const deliver = useCallback(
    async (text: string, pending: UIMessage) => {
      const id = activeSession.current;
      setIsSending(true);
      setError(null);
      try {
        const response = await sendMessage(id, text);
        if (activeSession.current !== id) return;
        setMessages((prev) => [
          ...prev.map((m) => (m.id === pending.id ? { ...m, delivery: "sent" as const } : m)),
          fromChatResponse(response),
        ]);
        void refreshSessions();
      } catch (err) {
        if (activeSession.current !== id) return;
        setMessages((prev) => prev.map((m) => (m.id === pending.id ? { ...m, delivery: "failed" as const } : m)));
        setError(userMessage(err));
      } finally {
        if (activeSession.current === id) setIsSending(false);
      }
    },
    [refreshSessions],
  );

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || isSending || !activeSession.current) return;
      const pending: UIMessage = {
        id: uuid(),
        role: "user",
        content: trimmed,
        timestamp: new Date().toISOString(),
        delivery: "sending",
      };
      setMessages((prev) => [...prev, pending]);
      setDraft("");
      void deliver(trimmed, pending);
    },
    [isSending, deliver],
  );

  const retry = useCallback(
    (failed: UIMessage) => {
      if (isSending) return;
      setMessages((prev) => prev.map((m) => (m.id === failed.id ? { ...m, delivery: "sending" as const } : m)));
      void deliver(failed.content, failed);
    },
    [isSending, deliver],
  );

  const closeDrawer = useCallback(() => setDrawerOpen(false), []);

  // Si la ventana pasa a lg con el drawer abierto (rotar la tablet, redimensionar), se cierra:
  // si no, quedaría el scroll del body bloqueado con el drawer oculto por CSS.
  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 1024px)");
    const onChange = () => desktop.matches && setDrawerOpen(false);
    desktop.addEventListener("change", onChange);
    return () => desktop.removeEventListener("change", onChange);
  }, []);

  const sidebarProps = {
    sessions,
    activeSessionId: sessionId,
    loading: sessionsLoading,
    error: sessionsError,
    onNewChat: startNewChat,
    onSelect: selectChat,
  };

  return (
    <div className="flex h-dvh w-full overflow-hidden bg-app">
      {/* Desktop (lg+): columna fija. */}
      <aside className="hidden w-72 shrink-0 border-r border-line lg:block" data-testid="desktop-sidebar">
        <ChatSidebar {...sidebarProps} />
      </aside>

      {/* Móvil y tablet: drawer fuera del flujo del layout. */}
      <MobileDrawer open={drawerOpen} onClose={closeDrawer}>
        <ChatSidebar
          {...sidebarProps}
          headerAction={
            <button
              type="button"
              onClick={closeDrawer}
              aria-label="Cerrar conversaciones"
              className="-mr-1 flex size-10 shrink-0 items-center justify-center rounded-xl text-ink-2 hover:bg-app hover:text-ink focus-visible:outline-2 focus-visible:outline-primary"
            >
              <CloseIcon />
            </button>
          }
        />
      </MobileDrawer>

      <main className="flex min-w-0 flex-1 flex-col" data-testid="chat-main">
        <ChatHeader
          onOpenMenu={() => setDrawerOpen(true)}
          onEndSession={startNewChat}
          canEndSession={messages.length > 0 || loadingHistory}
        />
        <MessageList
          messages={messages}
          isTyping={isSending}
          loadingHistory={loadingHistory}
          onSuggestion={send}
          onRetry={retry}
          footer={error ? <ErrorNotice message={error} onDismiss={() => setError(null)} /> : null}
        />
        <ChatInput value={draft} onChange={setDraft} onSend={() => send(draft)} disabled={isSending || loadingHistory} />
      </main>
    </div>
  );
}
