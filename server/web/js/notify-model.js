// Сообщения о загрузках для отдела персонала без DOM (S20): колокольчик в шапке, список, красная полоса тревоги.
// Ответ GET /api/notifications: {items: [{id, kind: "load_ok" | "load_alarm", body: {title, text, job, severity}, created_at, read_at, ack: {by, at} | null}],
// unread, alarm: {id, created_at, title, text} | null}. «Разбираюсь» снимает полосу у всех, имя видно в списке.

export const POLL_MS = 60000;
export const ACK_LABEL = "Разбираюсь";
export const EMPTY_TEXT = "Сообщений о загрузках пока нет";

export const ackText = (by) => `Разбирается: ${by}`;

/** Значок: число новых (больше девяти — «9+»), подпись для скринридера словами, красный оттенок при тревоге. */
export function bellView(reply) {
  const count = reply && Number.isFinite(reply.unread) ? reply.unread : 0;
  const alarm = Boolean(reply && reply.alarm);
  const label = `Сообщения о загрузках: ${count > 0 ? `новых ${count}` : "новых нет"}${alarm ? ", есть тревога" : ""}`;
  return { count, badge: count > 9 ? "9+" : count > 0 ? String(count) : "", alarm, label };
}

/** Строка списка: итог словом (не только цветом), тон чипа, кто разбирает. */
export function itemView(item) {
  const alarm = item.kind === "load_alarm";
  const acked = Boolean(item.ack && item.ack.by);
  return {
    id: item.id,
    title: (item.body && item.body.title) || "Загрузка",
    text: (item.body && item.body.text) || "",
    at: item.created_at,
    tone: alarm ? "r" : "g",
    status: alarm ? "не прошла — требуется разбирательство" : "прошла",
    unread: !item.read_at,
    canAck: alarm && !acked,
    ackText: acked ? ackText(item.ack.by) : null,
  };
}

/**
 * Красная полоса под вкладками: у всех HR, пока кто-то не нажал «Разбираюсь». Нажавший сам ещё немного видит спокойную полосу
 * «Разбирается: …» (localAck = {by, title}); потом она уходит.
 */
export function alarmView(reply, localAck) {
  if (reply && reply.alarm) {
    return { kind: "alarm", id: reply.alarm.id, title: reply.alarm.title, text: reply.alarm.text, canAck: true };
  }
  if (localAck) return { kind: "taken", id: null, title: localAck.title, text: ackText(localAck.by), canAck: false };
  return null;
}

/** Какие сообщения отметить прочитанными, когда список открыт. */
export const unreadIds = (reply) => ((reply && reply.items) || []).filter((item) => !item.read_at).map((item) => item.id);
