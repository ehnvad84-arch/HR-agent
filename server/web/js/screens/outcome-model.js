// «Итог этапа» без DOM (S6–S9, S36): варианты окна, причины по исходу, тело POST …/mark, уведомления.
// Тексты — из спеки funnel-report, раздел «UI» (окно C).
import { countChars } from "../decisions-model.js";
import { plural } from "../format.js";
import { localIso, nextStage } from "./funnel-model.js";

/** Порог комментария «Другое», пока справочник не загружен; рабочий — otherMin(GET /api/stages) из settings.stage_fail_other_min. */
export const OTHER_MIN = 100;

/** Порог «Другое» из справочника сервера (GET /api/stages → other_min); нет числа — по умолчанию. */
export function otherMin(library) {
  const value = library ? library.other_min : undefined;
  return Number.isInteger(value) && value >= 0 ? value : OTHER_MIN;
}

/** Строки отчёта, после которых человека ждёт встреча: спрашиваем дату и время (необязательно). */
const MEETING_ROWS = new Set(["screening", "interview", "meeting"]);
export const meetingNext = (stage) => Boolean(stage) && MEETING_ROWS.has(stage.report_row);

/**
 * Что показать в окне по этапу slot: заголовок, варианты исхода, следующий этап, резолюция руководителя (строка «Собеседование»),
 * подставленная причина последнего этапа. closed — вакансия закрыта: «Прошёл» не предлагается.
 */
export function outcomeForm({ stages, slot, hiringManager = "", closed = false }) {
  const stage = (stages || []).find((s) => s.slot === slot) || null;
  const next = nextStage(stages, slot);
  const last = Boolean(stage) && next === null;
  const all = last
    ? [{ value: "passed", label: "Вышел в первый день" }, { value: "refused", label: "Не вышел" }]
    : [{ value: "passed", label: `Прошёл — на этап «${next ? next.title : ""}»` },
      { value: "failed", label: "Не прошёл — отказали мы" }, { value: "refused", label: "Отказался" }];
  const options = closed ? all.filter((o) => o.value !== "passed") : all;   // S9: на закрытой — только отказ с причиной
  return {
    stage, next, last, options,
    askWhen: !closed && !last && meetingNext(next),
    fixedReason: last ? "no_first_day" : "",
    resolution: stage && stage.report_row === "meeting" ? `резолюция руководителя${hiringManager ? ` (${hiringManager})` : ""}` : "",
  };
}

const REFUSED_SELF = new Set(["conditions", "offer_declined"]);

/**
 * Причины своего исхода (S7): «Не прошёл» — «Отказали мы»; «Отказался» — «Отказался сам» и «Пропал»; «Другое» — отдельно, в конце.
 * «Отказ от оффера» (only_row) — только на своей строке отчёта.
 */
export function reasonGroups(reasons, outcome, rowCode) {
  const list = (reasons || []).filter((r) => !r.only_row || r.only_row === rowCode);
  const own = list.filter((r) => r.outcome === outcome).map((r) => ({ code: r.code, title: r.title }));
  const other = list.find((r) => r.outcome === null || r.outcome === undefined) || null;
  const groups = outcome === "failed" ? [{ label: "Отказали мы", items: own }]
    : [{ label: "Отказался сам", items: own.filter((r) => REFUSED_SELF.has(r.code)) },
      { label: "Пропал", items: own.filter((r) => !REFUSED_SELF.has(r.code)) }];
  return { groups: groups.filter((g) => g.items.length), other };
}

/**
 * Поля окна → тело POST /api/vacancy/{v}/candidate/{c}/mark; ошибка заполнения — текстом по-русски.
 * «Прошёл»: без причины, комментарий (резолюция) — по желанию, дата встречи — по желанию.
 * «Не прошёл» / «Отказался»: причина обязательна, «Другое» — комментарий не короче min знаков.
 */
export function buildMark({ slot, outcome, reason = "", comment = "", when = "", fixedReason = "", min = OTHER_MIN, offsetMinutes }) {
  if (!["passed", "failed", "refused"].includes(outcome)) return { ok: false, error: "Выберите итог" };
  const text = String(comment ?? "").trim();
  const payload = { slot, outcome };
  if (outcome === "passed") {
    if (text) payload.comment = text;
    const at = when ? localIso(when, offsetMinutes) : "";
    if (at) payload.scheduled_at = at;
    return { ok: true, payload };
  }
  const code = fixedReason && outcome === "refused" ? fixedReason : reason;
  if (!code) return { ok: false, error: "Выберите причину" };
  if (code === "other" && countChars(text) < min) return { ok: false, error: `Опишите причину: комментарий не короче ${min} знаков` };
  payload.reason = code;
  if (text) payload.comment = text;
  return { ok: true, payload };
}

/** Счётчик под комментарием «Другое»: «0 из 100 знаков». */
export const otherCounter = (text, min = OTHER_MIN) => `${countChars(text)} из ${min} знаков`;

/** «Итог сохранён: Анна С. — на этапе „Интервью“». */
export function markToast({ name, outcome, nextTitle = "" }) {
  if (outcome === "passed") return `Итог сохранён: ${name} — ${nextTitle ? `на этапе «${nextTitle}»` : "вышел в первый день"}`;
  return `Итог сохранён: ${name} — ${outcome === "failed" ? "не прошёл" : "отказался"}, причина записана`;
}

/** S9: вышли все ставки — предложение закрыть вакансию. */
export const closePrompt = (headcount) => `Вышли все ${headcount} ${plural(headcount, "ставка", "ставки", "ставок")} — закрыть вакансию?`;
