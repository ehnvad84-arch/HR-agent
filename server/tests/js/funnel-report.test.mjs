// Отчёт воронки без DOM (S11): строки таблицы, проценты, конверсия, даты и срок ведения
import assert from "node:assert/strict";
import test from "node:test";

import {
  captionText, conversionText, hintText, periodText, pctText, reportDate, rowView, tableRows,
} from "../../web/js/screens/funnel-report-model.js";

const row = (code, title, entered, passed, extra = {}) => ({
  code, title, entered, passed, pct: entered ? Math.round((passed / entered) * 100) : null, in_set: true, stages: [], ...extra,
});
const REPORT = {
  id: 12, title: "Инженер-электроник", state: "open", opened_at: "2026-11-03", closed_at: null, days: 14, paused_days: 6,
  since: "2026-11-23", carried: 0,
  rows: [
    row("selected", "Отобранные резюме", 24, 12),
    row("contact", "Первичный контакт", 12, 9),
    row("screening", "Пройденное первичное интервью", 9, 6),
    row("interview", "Интервью", 6, 4),
    row("meeting", "Собеседование", 4, 2, { stages: [
      { slot: 5, title: "Собеседование с руководителем отдела", in_set: true, entered: 4, passed: 3, pct: 75 },
      { slot: 6, title: "Собеседование с главным инженером", in_set: true, entered: 3, passed: 2, pct: 67 }] }),
    row("security", "Проверка СБ", 2, 2, { in_set: false }),
    row("offer", "Оффер", 0, 0),
    row("first_day", "Пришедшие в 1 день", 1, 1),
  ],
  conversion: { hired: 1, selected: 24, pct: 4 },
};

test("S11: проценты — целые со знаком, при нуле вошедших «—»", () => {
  assert.equal(pctText(50), "50 %");
  assert.equal(pctText(0), "0 %");
  assert.equal(pctText(null), "—");
  assert.equal(pctText(undefined), "—");
});

test("S11: даты — число.месяц.год", () => {
  assert.equal(reportDate("2026-11-03"), "03.11.2026");
  assert.equal(reportDate("2026-11-03T10:00:00+03:00"), "03.11.2026");
  assert.equal(reportDate(null), "");
  assert.equal(reportDate("мусор"), "");
});

test("S11: «Открыта 03.11.2026 · закрытия нет · срок ведения 14 дн. (пауза 6 дн. не считается)»", () => {
  assert.equal(periodText(REPORT), "Открыта 03.11.2026 · закрытия нет · срок ведения 14 дн. (пауза 6 дн. не считается)");
  assert.equal(periodText({ ...REPORT, paused_days: 0 }), "Открыта 03.11.2026 · закрытия нет · срок ведения 14 дн.");
  assert.equal(periodText({ ...REPORT, closed_at: "2026-11-20", days: 11, paused_days: 0 }),
    "Открыта 03.11.2026 · закрыта 20.11.2026 · срок ведения 11 дн.");
});

test("S11: «Конверсия воронки: 4 % — вышли 1 из 24 отобранных»; нет отобранных — прочерк", () => {
  assert.equal(conversionText(REPORT.conversion), "Конверсия воронки: 4 % — вышли 1 из 24 отобранных");
  assert.equal(conversionText({ hired: 0, selected: 0, pct: null }), "Конверсия воронки: — — вышли 0 из 0 отобранных");
  assert.equal(conversionText(null), "Конверсия воронки: —");
});

test("S11, S15: подсказка под таблицей и «Учёт с» — дата применения 012", () => {
  assert.equal(hintText("2026-11-23"), "Считает портал по вашим отметкам «прошёл / не прошёл». Учёт с 23.11.2026.");
  assert.equal(hintText(null), "Считает портал по вашим отметкам «прошёл / не прошёл».");
});

test("S11: строки таблицы — вошли, прошли, %; вне набора — отдельный признак; несколько этапов — подстроки", () => {
  const rows = tableRows(REPORT);
  assert.equal(rows.length, 8);
  assert.deepEqual([rows[0].title, rows[0].entered, rows[0].passed, rows[0].pct], ["Отобранные резюме", "24", "12", "50 %"]);
  assert.equal(rows[0].outOfSet, false);
  assert.equal(rows[0].subs.length, 0);
  assert.equal(rows[4].subs.length, 2);
  assert.deepEqual([rows[4].subs[0].title, rows[4].subs[0].entered, rows[4].subs[0].passed, rows[4].subs[0].pct], ["Собеседование с руководителем отдела", "4", "3", "75 %"]);
  assert.equal(rows[5].outOfSet, true);
  assert.equal(rows[5].note, "нет в наборе");
  assert.equal(rows[6].pct, "—");   // вошли = 0
  assert.equal(rowView(row("offer", "Оффер", 2, 1)).note, "");
});

test("S11: без раскрытия (сумма рекрутера и отдела) подстрок нет", () => {
  const rows = tableRows(REPORT, { expand: false });
  assert.ok(rows.every((r) => r.subs.length === 0));
});

test("подпись таблицы для скринридера", () => {
  assert.equal(captionText("Инженер-электроник"), "Воронка: Инженер-электроник");
  assert.equal(captionText(""), "Воронка");
});
