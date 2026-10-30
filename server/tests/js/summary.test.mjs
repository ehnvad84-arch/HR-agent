// Краткая сводка резюме на карточке сигнала «Сегодня» (summary из /api/today)
import assert from "node:assert/strict";
import test from "node:test";

import { daysAgoText, summaryView } from "../../web/js/summary-model.js";

const NOW = new Date(2026, 9, 29, 12, 0);
const ago = (days) => new Date(2026, 9, 29 - days, 8, 30).toISOString();   // местное время, как у браузера

const FULL = { exp_years: 9.4, salary_expect: 180000, employer_current: "АО «Северный прибор»", relocation_ready: true,
  open_for_search: true, updated_at_src: ago(12), skills: ["Altium Designer", "OrCAD", "ЭМС", "STM32", "KiCad"] };

test("полная сводка: опыт, ожидания, место работы, переезд, поиск работы, дата обновления, навыки", () => {
  const view = summaryView(FULL, NOW);
  assert.deepEqual(view.rows.map(([label, value]) => `${label}: ${value.replace(/[  ]/g, " ")}`), [
    "Опыт: 9,4 года", "Ожидания: 180 000 ₽", "Сейчас: АО «Северный прибор»", "Переезд: готов · ищет работу",
    "Резюме обновлено: 12 дней назад"]);
  assert.ok(view.rows.length <= 5, "справка не длиннее пяти строк");
  assert.deepEqual(view.skills, ["Altium Designer", "OrCAD", "ЭМС"]);
});

test("ожидания не указаны — так и пишем; переезд: готов / нет / не указано", () => {
  const rows = (patch) => Object.fromEntries(summaryView({ ...FULL, open_for_search: null, ...patch }, NOW).rows);
  assert.equal(rows({ salary_expect: null }).Ожидания, "не указаны");
  assert.equal(rows({ salary_expect: 0 }).Ожидания, "не указаны");
  assert.equal(rows({ relocation_ready: false }).Переезд, "нет");
  assert.equal(rows({ relocation_ready: null }).Переезд, "не указано");
});

test("пустые поля не показываются: нет места работы, дат, навыков и поиска работы", () => {
  const view = summaryView({ exp_years: 3, salary_expect: null, employer_current: "  ", relocation_ready: null, open_for_search: false,
    updated_at_src: null, skills: [] }, NOW);
  const labels = view.rows.map(([label]) => label);
  assert.deepEqual(labels, ["Опыт", "Ожидания", "Переезд"]);
  assert.deepEqual(view.skills, []);
  assert.ok(!labels.includes("Ищет работу") && !labels.includes("Сейчас") && !labels.includes("Резюме обновлено"));
});

test("сводки нет совсем — блока нет", () => {
  assert.equal(summaryView(null, NOW), null);
  assert.equal(summaryView(undefined, NOW), null);
  assert.equal(summaryView({ exp_years: null, salary_expect: null, employer_current: null, relocation_ready: null, open_for_search: null,
    updated_at_src: null, skills: [] }, NOW), null);
});

test("навыков не больше трёх, длинные обрезаются, лишние пробелы и пустое убираются", () => {
  const view = summaryView({ ...FULL, skills: [" A ", "", "Управление дистрибьюторской сетью Развитие дилерской сети", "C", "D"] }, NOW);
  assert.deepEqual(view.skills, ["A", "Управление дистрибьюторской…", "C"]);
  assert.ok(view.skills.every((s) => s.length <= 28));
});

test("когда обновлено резюме: сегодня, вчера, N дней назад", () => {
  assert.equal(daysAgoText(ago(0), NOW), "сегодня");
  assert.equal(daysAgoText(ago(1), NOW), "вчера");
  assert.equal(daysAgoText(ago(2), NOW), "2 дня назад");
  assert.equal(daysAgoText(ago(5), NOW), "5 дней назад");
  assert.equal(daysAgoText(ago(21), NOW), "21 день назад");
  assert.equal(daysAgoText(ago(-3), NOW), "сегодня");   // дата из будущего (часы источника) — не «минус три дня»
  assert.equal(daysAgoText("не дата", NOW), "");
  assert.equal(daysAgoText(null, NOW), "");
});
