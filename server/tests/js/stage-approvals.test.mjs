// Утверждение наборов этапов руководителем ОП (S4): что изменилось, счётчик на вкладке «Воронка», справочник по строкам отчёта
import assert from "node:assert/strict";
import test from "node:test";

import {
  APPROVE_HINT, approvalLine, decisionToast, diffSets, libraryGroups, pendingBadge, pendingCountText, usedText, zoneTitle,
} from "../../web/js/screens/stage-approvals-model.js";

const it = (pos, slot, title, report_row, extra = {}) => ({ pos, slot, stage_id: pos, label: null, title, report_row, is_standard: true, ...extra });
const CURRENT = [it(1, 1, "Отобранные резюме", "selected"), it(2, 2, "Интервью", "interview"), it(3, 3, "Тестовое задание", "interview", { is_standard: false }),
  it(4, 4, "Собеседование", "meeting"), it(5, 5, "Пришедшие в 1 день", "first_day")];

test("S4: счётчик на вкладке «Воронка» — число наборов, ждущих утверждения; ноль — пусто", () => {
  const lists = { open: [{ stages_pending: true }, { stages_pending: false }, { stages_pending: true }], closed: [{ stages_pending: false }] };
  assert.equal(pendingBadge(lists), 2);
  assert.equal(pendingBadge({ open: [{ stages_pending: false }], closed: [] }), "");
  assert.equal(pendingBadge({ open: [], closed: [] }), "");
  assert.equal(pendingBadge(null), "");
  assert.equal(pendingBadge({ open: [{ stages_pending: true }] }), 1);
});

test("S4: заголовок блока — со счётчиком", () => {
  assert.equal(zoneTitle(2), "Ждут утверждения — наборы этапов (2)");
  assert.equal(pendingCountText(1), "1 набор");
  assert.equal(pendingCountText(3), "3 набора");
  assert.equal(pendingCountText(5), "5 наборов");
});

test("S4: что изменил рекрутер — «+ этап», «− этап», «перенесён», подпись", () => {
  const proposed = [CURRENT[0], it(2, 2, "Интервью", "interview"), it(3, 6, "Собеседование с главным инженером", "meeting", { is_standard: false }),
    CURRENT[3], CURRENT[4]];
  assert.deepEqual(diffSets(CURRENT, proposed), [
    { kind: "add", text: "+ Собеседование с главным инженером", tone: "g" },
    { kind: "remove", text: "− Тестовое задание", tone: "r" }]);
  const moved = [CURRENT[0], CURRENT[2], CURRENT[1], CURRENT[3], CURRENT[4]];
  assert.deepEqual(diffSets(CURRENT, moved), [{ kind: "move", text: "перенесён: Интервью", tone: "" }]);
  const renamed = [CURRENT[0], CURRENT[1], { ...CURRENT[2], label: "Тест у главного" }, CURRENT[3], CURRENT[4]];
  assert.deepEqual(diffSets(CURRENT, renamed), [{ kind: "label", text: "подпись: Тест у главного", tone: "" }]);
  assert.deepEqual(diffSets(CURRENT, CURRENT), []);
  assert.deepEqual(diffSets(null, CURRENT).length, 0, "нет действующего набора — нечего сравнивать");
});

test("S4: подпись карточки — вакансия, рекрутер, дата отправки", () => {
  assert.equal(approvalLine({ recruiter: "Петрова А.", proposed_at: "2026-11-24T10:00:00+03:00" }), "Петрова А. · отправлен 24.11.2026");
  assert.equal(approvalLine({ recruiter: null, proposed_by_name: "Сидоров Б.", proposed_at: "2026-11-24T10:00:00+03:00" }), "Сидоров Б. · отправлен 24.11.2026");
  assert.equal(approvalLine({}), "");
});

test("S4: тексты решения", () => {
  assert.equal(decisionToast("approved"), "Набор утверждён");
  assert.equal(decisionToast("declined"), "Набор отклонён");
  assert.match(APPROVE_HINT, /сразу/);
});

test("S1: справочник по 8 строкам отчёта; «в наборах N вакансий»", () => {
  const library = {
    rows: [{ code: "interview", ord: 4, title: "Интервью" }, { code: "meeting", ord: 5, title: "Собеседование" }, { code: "security", ord: 6, title: "Проверка СБ" }],
    stages: [
      { id: 1, title: "Интервью", report_row: "interview", is_standard: true, is_active: true, vacancies: 9 },
      { id: 9, title: "Тестовое задание", report_row: "interview", is_standard: false, is_active: false, vacancies: 0 },
      { id: 5, title: "Собеседование", report_row: "meeting", is_standard: true, is_active: true, vacancies: 1 }],
  };
  const groups = libraryGroups(library);
  assert.deepEqual(groups.map((g) => [g.code, g.title, g.items.length]), [["interview", "Интервью", 2], ["meeting", "Собеседование", 1], ["security", "Проверка СБ", 0]]);
  assert.deepEqual(groups[0].items.map((i) => [i.title, i.standard, i.active, i.usedText]), [
    ["Интервью", true, true, "в наборах 9 вакансий"], ["Тестовое задание", false, false, "ни в одном наборе"]]);
  assert.equal(groups[0].items[0].canRetire, false, "стандартный не выводится");
  assert.equal(groups[0].items[1].canRetire, false, "уже выведен");
  assert.equal(usedText(1), "в наборах 1 вакансии");
  assert.equal(usedText(2), "в наборах 2 вакансий");
  assert.equal(usedText(21), "в наборах 21 вакансии");
  assert.equal(usedText(0), "ни в одном наборе");
});

test("S1: форма «Новый этап» — название и строка отчёта обязательны", async () => {
  const { newStageForm } = await import("../../web/js/screens/stage-approvals-model.js");
  assert.deepEqual(newStageForm({ title: "  ", report_row: "meeting" }).field, "title");
  assert.deepEqual(newStageForm({ title: "Тест", report_row: "" }).field, "report_row");
  assert.deepEqual(newStageForm({ title: " Тест ", report_row: "interview" }), { body: { title: "Тест", report_row: "interview" } });
});
