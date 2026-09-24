/**
 * UUID v4. crypto.randomUUID() solo existe en contextos seguros (HTTPS o localhost): desde un
 * celular que entra por http://<IP-de-la-laptop>:3000 no está disponible, getRandomValues sí.
 */
export function uuid(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function newSessionId(): string {
  return `web-${uuid()}`;
}

const TIME = new Intl.DateTimeFormat("es-PE", { hour: "2-digit", minute: "2-digit" });
const DAY = new Intl.DateTimeFormat("es-PE", { day: "numeric", month: "short" });

export function formatTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : TIME.format(date);
}

/** "14:05" si es de hoy; "23 sept" si es de otro día. */
export function formatRelativeDay(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  return date.toDateString() === today.toDateString() ? TIME.format(date) : DAY.format(date);
}
