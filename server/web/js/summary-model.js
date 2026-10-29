// Краткая сводка резюме (summary из /api/today) без DOM: что показать справа на карточке сигнала.
import { moneyText, plural, yearsText } from "./format.js";

const MAX_SKILLS = 5;

const startOfDay = (date) => new Date(date.getFullYear(), date.getMonth(), date.getDate());

/** «сегодня», «вчера», «5 дней назад»; не дата — пустая строка. */
export function daysAgoText(value, now = new Date()) {
  if (value === null || value === undefined || value === "") return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const days = Math.round((startOfDay(now) - startOfDay(date)) / 86400000);
  if (days <= 0) return "сегодня";
  if (days === 1) return "вчера";
  return `${days} ${plural(days, "день", "дня", "дней")} назад`;
}

/**
 * Строки для блока справа: [подпись, значение] и навыки (до пяти). Пустые поля пропускаются; «ожидания» и «переезд»
 * показываются словами «не указаны» и «не указано». Нет ни одного данного — null, блока не будет.
 */
export function summaryView(summary, now = new Date()) {
  if (!summary) return null;
  const skills = (summary.skills || []).map((s) => String(s).trim()).filter(Boolean).slice(0, MAX_SKILLS);
  const employer = String(summary.employer_current ?? "").trim();
  const updated = daysAgoText(summary.updated_at_src, now);
  const hasAny = summary.exp_years !== null && summary.exp_years !== undefined
    || summary.salary_expect || employer || (summary.relocation_ready !== null && summary.relocation_ready !== undefined)
    || summary.open_for_search === true || updated || skills.length;
  if (!hasAny) return null;

  const rows = [];
  if (typeof summary.exp_years === "number") rows.push(["Опыт", yearsText(summary.exp_years)]);
  rows.push(["Ожидания", summary.salary_expect ? moneyText(summary.salary_expect) : "не указаны"]);
  if (employer) rows.push(["Сейчас", employer]);
  rows.push(["Переезд", summary.relocation_ready === true ? "готов" : summary.relocation_ready === false ? "нет" : "не указано"]);
  if (summary.open_for_search === true) rows.push(["Ищет работу", "да"]);
  if (updated) rows.push(["Резюме обновлено", updated]);
  return { rows, skills };
}
