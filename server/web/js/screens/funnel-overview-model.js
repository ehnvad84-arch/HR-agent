// Отчёт рекрутера и отдела на «Воронке» без вакансии (S12, S13) без DOM: год, чей отчёт, блоки вакансий, сравнение рекрутеров.
// Цифры считает сервер (GET /api/funnel/recruiter/{uid}?year=, /api/funnel/department?year=); здесь только подписи и раскладка.
import { pctText, reportDate } from "./funnel-report-model.js";

export const ALL = "all";   // «Весь отдел»

const MIN_YEAR = 2000;
const MAX_YEAR = 2100;

/**
 * Адрес #/funnel?year=&recruiter= → {year: число|null, recruiter: "all"|номер}.
 * Рекрутер видит только себя: чужой ?recruiter= игнорируется. Нет года — сервер берёт текущий.
 */
export function parseView(query, { department, userId }) {
  const q = query || {};
  const year = /^\d{4}$/.test(String(q.year ?? "")) && Number(q.year) >= MIN_YEAR && Number(q.year) <= MAX_YEAR ? Number(q.year) : null;
  if (!department) return { year, recruiter: userId };
  const recruiter = /^\d+$/.test(String(q.recruiter ?? "")) ? Number(q.recruiter) : ALL;
  return { year, recruiter };
}

/** Что дописать к адресу: «Весь отдел» и неизвестный год — без параметра. */
export function overviewQuery({ year, recruiter }) {
  const query = {};
  if (year) query.year = year;
  if (recruiter !== ALL && recruiter !== null && recruiter !== undefined) query.recruiter = recruiter;
  return query;
}

/** Годы для переключателя: из ответа, выбранный есть всегда, новые сверху. */
export function yearOptions(years, year) {
  const set = new Set((years || []).map(Number));
  if (year) set.add(Number(year));
  return [...set].sort((a, b) => b - a);
}

/** «Чей отчёт»: «Весь отдел» и рекрутеры; выбранный, которого в ответе нет, остаётся в списке (имя — из names или «Рекрутер № N»). */
export function recruiterOptions(recruiters, selected, names = new Map()) {
  const options = [{ value: ALL, label: "Весь отдел" }, ...(recruiters || []).map((r) => ({ value: String(r.id), label: r.name || `Рекрутер № ${r.id}` }))];
  if (selected !== ALL && selected !== null && selected !== undefined && !options.some((o) => o.value === String(selected))) {
    options.push({ value: String(selected), label: names.get(Number(selected)) || `Рекрутер № ${selected}` });
  }
  return options;
}

export const overviewTitle = (year) => (year ? `Воронка ${year}` : "Воронка");

/** Чей отчёт показан: «Весь отдел», имя рекрутера или (рекрутер смотрит себя) «Мои вакансии». */
export function recruiterTitle(view, recruiters) {
  if (view.recruiter === ALL) return "Весь отдел";
  const found = (recruiters || []).find((r) => r.id === view.recruiter);
  return found && found.name ? found.name : "Мои вакансии";
}

export const emptyYearText = (year) => (year ? `За ${year} год вакансий нет` : "Вакансий нет");

/** «2026-11-03» → «03.11»; не дата — пусто. */
export function shortDate(value) {
  const full = reportDate(value);
  return full ? full.slice(0, 5) : "";
}

/** «Название · открыта 03.11 · 14 дн. · вышли 1 из 24»; у закрытой вместо «открыта» — период «01.09–30.10». */
export function vacancyLine(v) {
  const when = v.closed_at ? `${shortDate(v.opened_at)}–${shortDate(v.closed_at)}` : `открыта ${shortDate(v.opened_at)}`;
  const c = v.conversion || { hired: 0, selected: 0 };
  return [v.title, when, `${v.days} дн.`, `вышли ${c.hired} из ${c.selected}`].join(" · ");
}

const cell = (entered, passed, pct, outOfSet = false) => (entered
  ? { pct: pctText(pct), of: `${passed} из ${entered}`, outOfSet } : { pct: "—", of: "", outOfSet });

/**
 * Сравнение рекрутеров (S13): столбцы «Отдел» и рекрутеры, строки — 8 строк отчёта и «Конверсия воронки».
 * В ячейке % этапа (прошли / вошли) и мелко «прошли из вошли»; recruiterId у «Отдел» — null (имя без ссылки).
 */
export function compareView(department) {
  const columns = [{ title: "Отдел", recruiterId: null, total: department.total },
    ...(department.recruiters || []).map((r) => ({ title: r.name || `Рекрутер № ${r.id}`, recruiterId: r.id, total: r.total }))];
  const rows = (department.total.rows || []).map((row, i) => ({
    code: row.code, title: row.title,
    cells: columns.map((col) => {
      const r = col.total.rows[i];
      return cell(r.entered, r.passed, r.pct, r.in_set === false);
    }),
  }));
  const conversion = columns.map((col) => {
    const c = col.total.conversion || { hired: 0, selected: 0, pct: null };
    return cell(c.selected, c.hired, c.pct);
  });
  return { heads: columns.map((c) => ({ title: c.title, recruiterId: c.recruiterId })), rows, conversion };
}
