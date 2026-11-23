// Ответ /api/today → модель экрана «Сегодня» (без DOM).
import { badgeCount, isEmptyDay } from "../format.js";
import { buildMark } from "./outcome-model.js";

export function normalizeToday(raw) {
  const data = {
    signals: raw.signals || [],
    actions: raw.actions || [],
    stalled: raw.stalled || [],
    meetings: raw.meetings || [],
    handoffs: raw.handoffs || [],   // S11: передали руководители (только у тех, кому они адресованы)
  };
  return {
    ...data,
    badge: badgeCount(data),
    empty: isEmptyDay(data),
    // кандидаты, по которым напоминание уже ждёт подтверждения: второй черновик подряд не нужен
    followUpQueued: new Set(data.actions.filter((a) => a.kind === "follow_up" && a.candidate_id).map((a) => a.candidate_id)),
  };
}

// ------------------------------------------------------------------ «Закрыть» зависшего (funnel-report S7)

/** Куда отметить отказ: открытые этапы человека (`stages` из /api/today) — «Вакансия — „Этап“»; value — «вакансия:slot». */
export function closeTargets(row) {
  return (row.stages || []).map((s) => ({
    value: `${s.vacancy_id}:${s.slot}`, vacancyId: s.vacancy_id, slot: s.slot, reportRow: s.report_row,
    label: `${s.vacancy_title} — «${s.stage}»`,
  }));
}

/**
 * «В резерв» у зависшего: сервер переводит в резерв только человека без открытого этапа (иначе 409 use_stages).
 * С открытым этапом кнопки нет — подсказка ведёт к «Закрыть».
 */
export function reserveOption(row) {
  return closeTargets(row).length ? { show: false, hint: "В резерв — после итога этапа: сначала «Закрыть» и отметьте причину" }
    : { show: true, hint: "" };
}

/** Поля окна → вакансия и тело POST …/mark: «Отказался» с причиной (по умолчанию «Не вышел на связь» подбирает окно). */
export function buildStalledClose({ target, reason, comment = "", min }) {
  if (!target) return { ok: false, error: "У человека нет открытого этапа" };
  const built = buildMark({ slot: target.slot, outcome: "refused", reason, comment, min });
  return built.ok ? { ok: true, vacancyId: target.vacancyId, payload: built.payload } : built;
}
