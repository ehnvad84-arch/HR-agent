// Отчёт рекрутера и отдела на «Воронке» без вакансии (S12, S13) без DOM: год, чей отчёт, блоки вакансий, сравнение рекрутеров
import assert from "node:assert/strict";
import test from "node:test";

import {
  ALL, compareView, emptyYearText, overviewQuery, overviewTitle, parseView, recruiterOptions, recruiterTitle, shortDate,
  vacancyLine, yearOptions,
} from "../../web/js/screens/funnel-overview-model.js";

const CODES = ["selected", "contact", "screening", "interview", "meeting", "security", "offer", "first_day"];
const TITLES = ["Отобранные резюме", "Первичный контакт", "Пройденное первичное интервью", "Интервью", "Собеседование", "Проверка СБ", "Оффер",
  "Пришедшие в 1 день"];
const table = (pairs, extra = {}) => ({
  rows: CODES.map((code, i) => ({ code, title: TITLES[i], entered: pairs[i][0], passed: pairs[i][1],
    pct: pairs[i][0] ? Math.round((pairs[i][1] / pairs[i][0]) * 100) : null, in_set: true, ...(extra[code] || {}) })),
  conversion: { hired: pairs[7][1], selected: pairs[0][0], pct: pairs[0][0] ? Math.round((pairs[7][1] / pairs[0][0]) * 100) : null },
});
const DEPARTMENT = {
  year: 2026, years: [2026, 2025], since: "2026-11-23",
  total: table([[17, 14], [14, 12], [13, 10], [10, 8], [8, 6], [6, 5], [6, 3], [3, 2]]),
  recruiters: [
    { id: 3, name: "Белов Игорь (демо)", vacancies: 1, total: table([[7, 6], [6, 5], [5, 4], [4, 3], [3, 3], [3, 2], [2, 1], [1, 1]]) },
    { id: 2, name: "Кравцова Анна (демо)", vacancies: 2, total: table([[10, 8], [8, 7], [8, 6], [6, 5], [5, 3], [3, 3], [4, 2], [2, 1]], { security: { in_set: false } }) },
  ],
};

test("S12: адрес #/funnel?year=&recruiter= — год числом, рекрутер «весь отдел» или номер", () => {
  assert.deepEqual(parseView({ year: "2025", recruiter: "7" }, { department: true, userId: 2 }), { year: 2025, recruiter: 7 });
  assert.deepEqual(parseView({}, { department: true, userId: 2 }), { year: null, recruiter: ALL });
  assert.deepEqual(parseView({ recruiter: "all" }, { department: true, userId: 2 }), { year: null, recruiter: ALL });
  assert.deepEqual(parseView({ year: "abc", recruiter: "x" }, { department: true, userId: 2 }), { year: null, recruiter: ALL });
  assert.deepEqual(parseView({ year: "1999" }, { department: false, userId: 2 }).year, null);   // вне разумных лет — как будто года нет
});

test("S12: рекрутер видит только себя — ?recruiter= чужого игнорируется", () => {
  assert.deepEqual(parseView({ year: "2026", recruiter: "7" }, { department: false, userId: 2 }), { year: 2026, recruiter: 2 });
  assert.deepEqual(parseView({}, { department: false, userId: 2 }), { year: null, recruiter: 2 });
});

test("S12: адрес для выбора — «весь отдел» без параметра recruiter", () => {
  assert.deepEqual(overviewQuery({ year: 2026, recruiter: ALL }), { year: 2026 });
  assert.deepEqual(overviewQuery({ year: 2025, recruiter: 7 }), { year: 2025, recruiter: 7 });
  assert.deepEqual(overviewQuery({ year: null, recruiter: ALL }), {});
});

test("S12: переключатель года — годы из ответа, выбранный год есть всегда, новые сверху", () => {
  assert.deepEqual(yearOptions([2025, 2026], 2026), [2026, 2025]);
  assert.deepEqual(yearOptions([2026], 2024), [2026, 2024]);
  assert.deepEqual(yearOptions(undefined, 2026), [2026]);
  assert.deepEqual(yearOptions([2026, 2026], 2026), [2026]);
});

test("S13: «Чей отчёт» — «Весь отдел» и рекрутеры; неизвестный выбранный остаётся в списке", () => {
  const opts = recruiterOptions(DEPARTMENT.recruiters, 2);
  assert.deepEqual(opts.map((o) => [o.value, o.label]), [["all", "Весь отдел"], ["3", "Белов Игорь (демо)"], ["2", "Кравцова Анна (демо)"]]);
  assert.deepEqual(recruiterOptions([], 9, new Map([[9, "Иванов Пётр"]])).map((o) => [o.value, o.label]), [["all", "Весь отдел"], ["9", "Иванов Пётр"]]);
  assert.equal(recruiterOptions([], 9).at(-1).label, "Рекрутер № 9");
  assert.equal(recruiterOptions([], ALL).length, 1);
});

test("S12, S13: заголовки — год и чей отчёт", () => {
  assert.equal(overviewTitle(2026), "Воронка 2026");
  assert.equal(overviewTitle(null), "Воронка");
  assert.equal(recruiterTitle({ recruiter: ALL }, []), "Весь отдел");
  assert.equal(recruiterTitle({ recruiter: 2 }, DEPARTMENT.recruiters), "Кравцова Анна (демо)");
  assert.equal(recruiterTitle({ recruiter: 2 }, []), "Мои вакансии");
});

test("S12: пусто — «За 2026 год вакансий нет»", () => {
  assert.equal(emptyYearText(2026), "За 2026 год вакансий нет");
});

test("S12: даты блока — число.месяц, у закрытой — период", () => {
  assert.equal(shortDate("2026-11-03"), "03.11");
  assert.equal(shortDate(null), "");
});

test("S12: строка вакансии — «Название · открыта 03.11 · 14 дн. · вышли 1 из 24»; закрытая — «01.09–30.10»", () => {
  const open = { title: "Инженер-электронщик", opened_at: "2026-11-03", closed_at: null, days: 14, conversion: { hired: 1, selected: 24, pct: 4 } };
  assert.equal(vacancyLine(open), "Инженер-электронщик · открыта 03.11 · 14 дн. · вышли 1 из 24");
  const closed = { title: "Монтажник РЭА", opened_at: "2026-09-01", closed_at: "2026-10-30", days: 52, conversion: { hired: 2, selected: 40, pct: 5 } };
  assert.equal(vacancyLine(closed), "Монтажник РЭА · 01.09–30.10 · 52 дн. · вышли 2 из 40");
});

test("S13: сравнение — столбцы «Отдел» и рекрутеры, в ячейке % и мелко «прошли из вошли»", () => {
  const view = compareView(DEPARTMENT);
  assert.deepEqual(view.heads.map((c) => c.title), ["Отдел", "Белов Игорь (демо)", "Кравцова Анна (демо)"]);
  assert.deepEqual(view.heads.map((c) => c.recruiterId), [null, 3, 2]);
  assert.equal(view.rows.length, 8);
  const first = view.rows[0];
  assert.equal(first.title, "Отобранные резюме");
  assert.deepEqual(first.cells.map((c) => [c.pct, c.of]), [["82 %", "14 из 17"], ["86 %", "6 из 7"], ["80 %", "8 из 10"]]);
  assert.deepEqual(view.conversion.map((c) => [c.pct, c.of]), [["12 %", "2 из 17"], ["14 %", "1 из 7"], ["10 %", "1 из 10"]]);
});

test("S13: строка вне набора у рекрутера — пометка, «вошли = 0» — прочерк без «0 из 0»", () => {
  const view = compareView(DEPARTMENT);
  const security = view.rows[5];
  assert.equal(security.cells[2].outOfSet, true);
  assert.equal(security.cells[1].outOfSet, false);
  const empty = compareView({ ...DEPARTMENT, recruiters: [{ id: 5, name: "Новый", vacancies: 0, total: table(CODES.map(() => [0, 0])) }] });
  assert.deepEqual([empty.rows[0].cells[1].pct, empty.rows[0].cells[1].of], ["—", ""]);
  assert.deepEqual([empty.conversion[1].pct, empty.conversion[1].of], ["—", ""]);
});
