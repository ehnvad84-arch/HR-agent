// Отчёт воронки без DOM (S11–S13): строки таблицы в форме отчёта ОП, проценты, конверсия, даты и срок ведения.
// Таблицу рисует ../funnel-table.js — она же нужна отчётам рекрутера и отдела (T14).

export const pctText = (pct) => (typeof pct === "number" ? `${pct} %` : "—");

/** «2026-11-03» или ISO с временем → «03.11.2026»; не дата — пусто. */
export function reportDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value ?? ""));
  return match ? `${match[3]}.${match[2]}.${match[1]}` : "";
}

/** «Открыта 03.11.2026 · закрытия нет · срок ведения 14 дн. (пауза 6 дн. не считается)». */
export function periodText(report) {
  const parts = [`Открыта ${reportDate(report.opened_at)}`,
    report.closed_at ? `закрыта ${reportDate(report.closed_at)}` : "закрытия нет",
    `срок ведения ${report.days} дн.${report.paused_days ? ` (пауза ${report.paused_days} дн. не считается)` : ""}`];
  return parts.join(" · ");
}

/** «Конверсия воронки: 4 % — вышли 1 из 24 отобранных». */
export function conversionText(conversion) {
  if (!conversion) return "Конверсия воронки: —";
  return `Конверсия воронки: ${pctText(conversion.pct)} — вышли ${conversion.hired} из ${conversion.selected} отобранных`;
}

/** Подсказка под таблицей; «Учёт с» — дата применения миграции 012 (поле since), не константа. */
export function hintText(since) {
  const base = "Считает портал по вашим отметкам «прошёл / не прошёл».";
  return since && reportDate(since) ? `${base} Учёт с ${reportDate(since)}.` : base;
}

export const captionText = (title) => (title ? `Воронка: ${title}` : "Воронка");

const cells = (r) => ({ entered: String(r.entered), passed: String(r.passed), pct: r.entered ? pctText(r.pct) : "—" });

/** Строка таблицы: «Вошли», «Прошли», «%» текстом; строка вне набора — серая с пометкой. */
export function rowView(row, { expand = true } = {}) {
  const outOfSet = row.in_set === false;
  return {
    code: row.code, title: row.title, ...cells(row), outOfSet, note: outOfSet ? "нет в наборе" : "",
    subs: expand ? (row.stages || []).map((s) => ({ slot: s.slot, title: s.title, ...cells(s), outOfSet: s.in_set === false })) : [],
  };
}

export const tableRows = (report, options = {}) => (report.rows || []).map((r) => rowView(r, options));
