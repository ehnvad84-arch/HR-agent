// Вкладка «Этапы» вакансии без DOM (S2–S5): действующий набор по строкам отчёта, плашки «ждёт утверждения» и «отклонён»
import assert from "node:assert/strict";
import test from "node:test";

import {
  PENDING_NOTE, declinedNote, groupItems, isStandardSet, peopleText, setHeading,
} from "../../web/js/screens/stages-model.js";

const item = (pos, slot, title, report_row, extra = {}) => ({ pos, slot, stage_id: pos, label: null, title, report_row, is_standard: true, ...extra });
const CURRENT = {
  id: 1, version: 1, state: "approved", note: "стандартный набор", proposed_by: null,
  items: [item(1, 1, "Отобранные резюме", "selected"), item(2, 2, "Первичный контакт", "contact"),
    item(5, 5, "Собеседование", "meeting"), item(6, 6, "Собеседование с руководителем", "meeting", { is_standard: false, label: "Руководитель 2" }),
    item(8, 8, "Пришедшие в 1 день", "first_day")],
  people: { 1: 12, 6: 3 },
};

test("S2: стандартный набор — версия 1 из стандартных этапов без автора", () => {
  assert.equal(isStandardSet(CURRENT), false);   // есть нестандартный этап
  assert.equal(isStandardSet({ ...CURRENT, items: CURRENT.items.filter((i) => i.is_standard) }), true);
  assert.equal(isStandardSet({ ...CURRENT, version: 2, items: CURRENT.items.filter((i) => i.is_standard) }), false);
  assert.equal(isStandardSet(null), false);
});

test("S2: заголовок «Действует»", () => {
  assert.equal(setHeading({ version: 1, items: [1, 2, 3, 4, 5, 6, 7, 8].map((n) => item(n, n, `Э${n}`, "selected")) }), "Действует (стандартный, 8 этапов)");
  assert.equal(setHeading(CURRENT), "Действует (версия 1, 5 этапов)");
  assert.equal(setHeading({ version: 3, items: [item(1, 1, "А", "selected")] }), "Действует (версия 3, 1 этап)");
  assert.equal(setHeading(null), "Действует");
});

test("S1, S5: этапы набора по строкам отчёта; у этапа — «N чел.» или «—»; подпись набора важнее названия", () => {
  const groups = groupItems(CURRENT.items, CURRENT.people);
  assert.deepEqual(groups.map((g) => [g.code, g.title, g.items.length]), [
    ["selected", "Отобранные резюме", 1], ["contact", "Первичный контакт", 1], ["meeting", "Собеседование", 2], ["first_day", "Пришедшие в 1 день", 1]]);
  assert.deepEqual(groups[2].items.map((i) => [i.slot, i.title, i.count, i.peopleText]), [[5, "Собеседование", 0, "—"], [6, "Руководитель 2", 3, "3 чел."]]);
  assert.equal(groups[0].items[0].peopleText, "12 чел.");
});

test("«N чел.» — как на доске", () => {
  assert.equal(peopleText(0), "—");
  assert.equal(peopleText(3), "3 чел.");
});

test("S3: плашка «набор ждёт утверждения» — рекрутер не простаивает", () => {
  assert.equal(PENDING_NOTE, "Новый набор ждёт утверждения у руководителя ОП. Пока вакансия идёт по действующему.");
});

test("S4: отклонённый набор — автор решения, дата, комментарий", () => {
  assert.equal(declinedNote({ decided_by_name: "Смирнова Е.", decided_at: "2026-11-24T10:00:00+03:00", decision_comment: "Лишнее собеседование" }),
    "Набор отклонён 24.11.2026, Смирнова Е.: «Лишнее собеседование»");
  assert.equal(declinedNote({ decided_by_name: null, decided_at: "2026-11-24T10:00:00+03:00", decision_comment: "" }), "Набор отклонён 24.11.2026");
});
