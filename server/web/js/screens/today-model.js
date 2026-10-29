// Ответ /api/today → модель экрана «Сегодня» (без DOM).
import { badgeCount, isEmptyDay } from "../format.js";

export function normalizeToday(raw) {
  const data = {
    signals: raw.signals || [],
    actions: raw.actions || [],
    stalled: raw.stalled || [],
    meetings: raw.meetings || [],
  };
  return {
    ...data,
    badge: badgeCount(data),
    empty: isEmptyDay(data),
    // кандидаты, по которым напоминание уже ждёт подтверждения: второй черновик подряд не нужен
    followUpQueued: new Set(data.actions.filter((a) => a.kind === "follow_up" && a.candidate_id).map((a) => a.candidate_id)),
  };
}
