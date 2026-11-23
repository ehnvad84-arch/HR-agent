// «Итог этапа» без DOM (S6–S9): варианты окна, причины по исходу, тело запроса, уведомления
import assert from "node:assert/strict";
import test from "node:test";

import {
  OTHER_MIN, buildMark, closePrompt, markToast, meetingNext, otherCounter, otherMin, outcomeForm, reasonGroups,
} from "../../web/js/screens/outcome-model.js";

const STAGES = [
  { slot: 1, title: "Отобранные резюме", report_row: "selected" },
  { slot: 4, title: "Интервью", report_row: "interview" },
  { slot: 5, title: "Собеседование с руководителем отдела", report_row: "meeting" },
  { slot: 6, title: "Собеседование с главным инженером", report_row: "meeting" },
  { slot: 7, title: "Оффер", report_row: "offer" },
  { slot: 8, title: "Пришедшие в 1 день", report_row: "first_day" },
];
const REASONS = [
  { code: "skills", title: "Опыт или навыки", outcome: "failed", only_row: null },
  { code: "test_task", title: "Тестовое задание", outcome: "failed", only_row: null },
  { code: "security", title: "Проверка СБ", outcome: "failed", only_row: null },
  { code: "conditions", title: "Не подходят условия труда", outcome: "refused", only_row: null },
  { code: "offer_declined", title: "Отказ от оффера", outcome: "refused", only_row: "offer" },
  { code: "no_contact", title: "Не вышел на связь", outcome: "refused", only_row: null },
  { code: "no_show", title: "Не пришёл на встречу", outcome: "refused", only_row: null },
  { code: "no_first_day", title: "Не вышел в первый день", outcome: "refused", only_row: null },
  { code: "other", title: "Другое", outcome: null, only_row: null, needs_comment: true },
];

test("S6: окно — «Прошёл» на следующий этап набора, «Не прошёл», «Отказался»", () => {
  const form = outcomeForm({ stages: STAGES, slot: 4 });
  assert.equal(form.stage.title, "Интервью");
  assert.equal(form.last, false);
  assert.deepEqual(form.options.map((o) => [o.value, o.label]), [
    ["passed", "Прошёл — на этап «Собеседование с руководителем отдела»"],
    ["failed", "Не прошёл — отказали мы"],
    ["refused", "Отказался"]]);
  assert.equal(form.next.slot, 5);
});

test("S8: на «Собеседовании» — резолюция руководителя-заказчика; на других строках нет", () => {
  assert.equal(outcomeForm({ stages: STAGES, slot: 5, hiringManager: "Иванов И." }).resolution, "резолюция руководителя (Иванов И.)");
  assert.equal(outcomeForm({ stages: STAGES, slot: 5 }).resolution, "резолюция руководителя");
  assert.equal(outcomeForm({ stages: STAGES, slot: 4, hiringManager: "Иванов И." }).resolution, "");
});

test("S9: последний этап — «Вышел в первый день» / «Не вышел», причина подставлена", () => {
  const form = outcomeForm({ stages: STAGES, slot: 8 });
  assert.equal(form.last, true);
  assert.equal(form.next, null);
  assert.deepEqual(form.options.map((o) => [o.value, o.label]), [["passed", "Вышел в первый день"], ["refused", "Не вышел"]]);
  assert.equal(form.fixedReason, "no_first_day");
  assert.equal(outcomeForm({ stages: STAGES, slot: 7 }).fixedReason, "");
});

test("S36: дата встречи спрашивается, когда следующий этап — встреча или интервью", () => {
  assert.equal(outcomeForm({ stages: STAGES, slot: 1 }).askWhen, true);   // дальше «Интервью»
  assert.equal(outcomeForm({ stages: STAGES, slot: 4 }).askWhen, true);   // дальше «Собеседование»
  assert.equal(outcomeForm({ stages: STAGES, slot: 6 }).askWhen, false);  // дальше «Оффер»
  assert.equal(outcomeForm({ stages: STAGES, slot: 8 }).askWhen, false);
  assert.equal(meetingNext({ report_row: "screening" }), true);
  assert.equal(meetingNext({ report_row: "offer" }), false);
  assert.equal(meetingNext(null), false);
});

test("S7: причины по исходу — «Не прошёл»: отказали мы; «Отказался»: сам и пропал; «Другое» в конце", () => {
  const failed = reasonGroups(REASONS, "failed", "interview");
  assert.deepEqual(failed.groups.map((g) => [g.label, g.items.map((i) => i.code)]), [["Отказали мы", ["skills", "test_task", "security"]]]);
  assert.equal(failed.other.code, "other");
  const refused = reasonGroups(REASONS, "refused", "interview");
  assert.deepEqual(refused.groups.map((g) => [g.label, g.items.map((i) => i.code)]), [
    ["Отказался сам", ["conditions"]], ["Пропал", ["no_contact", "no_show", "no_first_day"]]]);
});

test("S7: «Отказ от оффера» — только на строке «Оффер»", () => {
  const onOffer = reasonGroups(REASONS, "refused", "offer");
  assert.deepEqual(onOffer.groups[0].items.map((i) => i.code), ["conditions", "offer_declined"]);
  assert.equal(reasonGroups(REASONS, "refused", "security").groups[0].items.some((i) => i.code === "offer_declined"), false);
});

test("S7: «Другое» — комментарий от порога, счётчик знаков берёт порог 100", () => {
  assert.equal(OTHER_MIN, 100);
  const other = (text) => buildMark({ slot: 4, outcome: "failed", reason: "other", comment: text });
  assert.equal(other("коротко").ok, false);
  assert.match(other("коротко").error, /не короче 100 знаков/);
  const long = "а".repeat(100);
  assert.deepEqual(other(long), { ok: true, payload: { slot: 4, outcome: "failed", reason: "other", comment: long } });
  assert.equal(buildMark({ slot: 4, outcome: "failed", reason: "other", comment: "а".repeat(60), min: 50 }).ok, true);   // порог сервера строже/мягче
});

test("S6–S7: тело запроса — «прошёл» без причины, отказ с причиной; причина обязательна", () => {
  assert.deepEqual(buildMark({ slot: 1, outcome: "passed" }), { ok: true, payload: { slot: 1, outcome: "passed" } });
  assert.deepEqual(buildMark({ slot: 5, outcome: "passed", comment: " Руководитель доволен " }),
    { ok: true, payload: { slot: 5, outcome: "passed", comment: "Руководитель доволен" } });
  assert.deepEqual(buildMark({ slot: 4, outcome: "failed", reason: "skills" }), { ok: true, payload: { slot: 4, outcome: "failed", reason: "skills" } });
  assert.equal(buildMark({ slot: 4, outcome: "refused", reason: "" }).error, "Выберите причину");
  assert.equal(buildMark({ slot: 4, outcome: "" }).error, "Выберите итог");
  // у «прошёл» причина не уходит, даже если выбрана раньше
  assert.deepEqual(buildMark({ slot: 4, outcome: "passed", reason: "skills" }), { ok: true, payload: { slot: 4, outcome: "passed" } });
});

test("S9: «Не вышел» на последнем этапе — причина подставлена сама", () => {
  assert.deepEqual(buildMark({ slot: 8, outcome: "refused", reason: "", fixedReason: "no_first_day" }),
    { ok: true, payload: { slot: 8, outcome: "refused", reason: "no_first_day" } });
});

test("S36: встреча уходит с часовым поясом браузера и только с «прошёл»", () => {
  assert.deepEqual(buildMark({ slot: 1, outcome: "passed", when: "2026-11-25T11:00", offsetMinutes: -180 }),
    { ok: true, payload: { slot: 1, outcome: "passed", scheduled_at: "2026-11-25T11:00:00+03:00" } });
  assert.deepEqual(buildMark({ slot: 4, outcome: "failed", reason: "skills", when: "2026-11-25T11:00", offsetMinutes: -180 }),
    { ok: true, payload: { slot: 4, outcome: "failed", reason: "skills" } });
});

test("уведомления после сохранения итога", () => {
  assert.equal(markToast({ name: "Анна С.", outcome: "passed", nextTitle: "Интервью" }), "Итог сохранён: Анна С. — на этапе «Интервью»");
  assert.equal(markToast({ name: "Анна С.", outcome: "passed", nextTitle: "" }), "Итог сохранён: Анна С. — вышел в первый день");
  assert.equal(markToast({ name: "Анна С.", outcome: "failed" }), "Итог сохранён: Анна С. — не прошёл, причина записана");
  assert.equal(markToast({ name: "Анна С.", outcome: "refused" }), "Итог сохранён: Анна С. — отказался, причина записана");
});

test("S9: вышли все ставки — предложение закрыть вакансию", () => {
  assert.equal(closePrompt(1), "Вышли все 1 ставка — закрыть вакансию?");
  assert.equal(closePrompt(2), "Вышли все 2 ставки — закрыть вакансию?");
  assert.equal(closePrompt(5), "Вышли все 5 ставок — закрыть вакансию?");
});

test("S7: счётчик «Другое» — «0 из 100 знаков», по символам без пробелов по краям", () => {
  assert.equal(otherCounter(""), "0 из 100 знаков");
  assert.equal(otherCounter("  привет  "), "6 из 100 знаков");
  assert.equal(otherCounter("а".repeat(120), 150), "120 из 150 знаков");
});

test("S7: порог «Другое» — из справочника сервера (settings.stage_fail_other_min), без него — по умолчанию", () => {
  assert.equal(otherMin({ other_min: 150, reasons: [] }), 150);
  assert.equal(otherMin({ other_min: 0 }), 0);
  assert.equal(otherMin({ reasons: [] }), OTHER_MIN);
  assert.equal(otherMin({ other_min: "много" }), OTHER_MIN);
  assert.equal(otherMin(null), OTHER_MIN);
});

test("S9: закрытая вакансия — без «Прошёл»: только отказ с причиной (сервер «прошёл» отвергает)", () => {
  const form = outcomeForm({ stages: STAGES, slot: 4, closed: true });
  assert.deepEqual(form.options.map((o) => o.value), ["failed", "refused"]);
  assert.equal(form.askWhen, false);
  assert.deepEqual(outcomeForm({ stages: STAGES, slot: 8, closed: true }).options.map((o) => [o.value, o.label]), [["refused", "Не вышел"]]);
  assert.deepEqual(outcomeForm({ stages: STAGES, slot: 4, closed: false }).options.map((o) => o.value), ["passed", "failed", "refused"]);
});
