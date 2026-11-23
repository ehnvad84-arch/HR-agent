// «Воронка» вакансии без DOM (S6, S10, S16): сводка по этапам набора, следующий этап, отмена последней отметки.
// Этап — колонка по slot действующего набора (GET /api/vacancy/{id}/funnel); перевод — «Итог этапа» (outcome-model.js).
import { dayLabel, timeLabel } from "../format.js";

export const funnelTotals = (stages) => (stages || []).reduce((sum, s) => sum + s.people.length, 0);

/** Строка этапа: «3 чел.», доля от всех в воронке (полоса), «в среднем 4 дн. на этапе» или «никого». */
export function stageSummary(stage, total) {
  const count = stage.people.length;
  return {
    count,
    countText: `${count} чел.`,
    pct: total > 0 ? Math.round((count / total) * 100) : 0,
    avgText: !count || stage.avg_days === null || stage.avg_days === undefined ? "никого"
      : stage.avg_days === 0 ? "в среднем меньше суток на этапе" : `в среднем ${stage.avg_days} дн. на этапе`,
    empty: count === 0,
  };
}

export function dayText(days) {
  if (typeof days !== "number") return "";
  return days === 0 ? "на этапе меньше суток" : `${days} дн. на этапе`;
}

// ------------------------------------------------------------------ порядок этапов (S6: только вперёд)

export const stageIndex = (stages, slot) => (stages || []).findIndex((s) => s.slot === slot);

/** Следующий этап набора по порядку колонок; у последнего и у неизвестного slot — null. */
export function nextStage(stages, slot) {
  const index = stageIndex(stages, slot);
  return index < 0 ? null : (stages[index + 1] || null);
}

export const isLastStage = (stages, slot) => stageIndex(stages, slot) >= 0 && nextStage(stages, slot) === null;

/** Перетащить карточку можно только на следующий этап: назад и через этап — нельзя (S6). */
export const canDropOn = (stages, fromSlot, toSlot) => {
  const next = nextStage(stages, fromSlot);
  return Boolean(next) && next.slot === toSlot;
};

/** Закрытая или отменённая вакансия: «прошёл» и отмена отметки сервер отвергает — кнопок нет (S9, S16). */
export const isClosedVacancy = (vacancy) => Boolean(vacancy) && (vacancy.state === "closed" || vacancy.state === "cancelled");

// ------------------------------------------------------------------ отмена последней отметки (S16)

/** День по Москве «2026-11-23»: отметка «в тот же день» считается по Москве, а не по часам браузера. */
export function moscowDay(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("sv-SE", { timeZone: "Europe/Moscow" });
}

/**
 * Показывать ли «Отменить последнюю отметку»: автору — в тот же день, руководителю ОП (stage.approve) — всегда.
 * Решает сервер (403 undo_expired); здесь только прячем лишнюю кнопку.
 */
export function canUndo({ last, userId, canApprove, now = new Date(), closed = false }) {
  if (!last || closed) return false;   // закрытая вакансия: отметки не отменяются (409 vacancy_closed)
  if (canApprove) return true;
  return last.by === userId && moscowDay(last.at) !== "" && moscowDay(last.at) === moscowDay(now);
}

const OUTCOME_WORD = { passed: "прошёл", failed: "не прошёл", refused: "отказался" };

/** «Последняя отметка: прошёл · Иванова А. · сегодня». */
export function lastMarkText(last, now = new Date()) {
  if (!last) return "";
  const day = dayLabel(last.at, now);
  const when = day ? `${day}, ${timeLabel(last.at)}` : "";
  return ["Последняя отметка: " + (OUTCOME_WORD[last.outcome] || String(last.outcome ?? "")), last.by_name, when].filter(Boolean).join(" · ");
}

const LEFT_WORD = { passed: "вышел на работу", failed: "не прошёл", refused: "отказался" };

/**
 * Блок «Выбыли (N)» под доской (S16): люди без открытого этапа — отказ или «вышел»; их последнюю отметку отменяют
 * по тем же правилам, что на доске. left — GET /api/vacancy/{id}/funnel → left; пусто — null (блока нет).
 */
export function leftBlock(left, { userId, canApprove, canMove, now = new Date(), closed = false }) {
  if (!left || !left.length) return null;
  return {
    title: `Выбыли (${left.length})`,
    rows: left.map((person) => {
      const last = person.last_mark || null;
      const word = last ? (LEFT_WORD[last.outcome] || String(last.outcome ?? "")) : "";
      return { person, text: [word, person.stage ? `этап «${person.stage}»` : ""].filter(Boolean).join(" · "),
        undo: Boolean(canMove) && canUndo({ last, userId, canApprove, now, closed }) };
    }),
  };
}

export const undoToast = (name, stageTitle) =>
  `Отметка отменена: ${name}${stageTitle ? ` — снова на этапе «${stageTitle}»` : ""}`;

// ------------------------------------------------------------------ прочее

/** Заметка человека в кабинете руководителя: последний комментарий отметки (контактов в нём нет — чистит сервер). */
export function personNote(person) {
  const notes = person.notes || [];
  return notes.length ? String(notes[0].comment || "") : String(person.note || "");
}

/** «2026-10-30T11:00» (поле datetime-local) → «2026-10-30T11:00:00+03:00»: время встречи с часовым поясом браузера. */
export function localIso(value, offsetMinutes) {
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::(\d{2}))?$/.exec(String(value ?? ""));
  if (!match || Number.isNaN(new Date(value).getTime())) return "";
  const offset = offsetMinutes ?? new Date(value).getTimezoneOffset();   // минуты «позади» UTC: для +03:00 это −180
  const sign = offset <= 0 ? "+" : "-";
  const abs = Math.abs(offset);
  const pad = (n) => String(n).padStart(2, "0");
  return `${match[1]}T${match[2]}:${match[3] || "00"}${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

/** Без номера вакансии открываем первую открытую (на паузе — только если других нет). */
export function pickVacancy(open) {
  const list = open || [];
  return list.find((v) => v.state === "open") || list[0] || null;
}

// ------------------------------------------------------------------ вкладки экрана вакансии (#/vacancy/<id>/funnel?view=)

export const VIEWS = [["candidates", "Кандидаты"], ["report", "Отчёт"], ["stages", "Этапы"]];

/** Вкладка из ?view=: «report», «stages»; всё остальное — «Кандидаты» (прежняя доска). */
export function viewOf(query) {
  const value = query && query.view;
  return VIEWS.some(([name]) => name === value) ? value : "candidates";
}
