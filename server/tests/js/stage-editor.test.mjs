// Редактор набора этапов вакансии без DOM (S3, S5): черновик, ↑/↓ внутри строки отчёта, «убрать», что можно убрать, что уходит на сервер
import assert from "node:assert/strict";
import test from "node:test";

import {
  addItem, addOptions, busyText, groupDraft, isChanged, moveItem, movePlace, proposalPayload, rebaseDraft, removeItem, removedRows, toDraft,
} from "../../web/js/screens/stage-editor-model.js";

const it = (pos, slot, title, report_row, extra = {}) => ({ pos, slot, stage_id: pos, label: null, title, report_row, is_standard: true, is_active: true, ...extra });
const CURRENT = [
  it(1, 1, "Отобранные резюме", "selected"), it(2, 2, "Первичный контакт", "contact"), it(3, 3, "Пройденное первичное интервью", "screening"),
  it(4, 4, "Интервью", "interview"), it(5, 5, "Собеседование", "meeting"), it(6, 6, "Проверка СБ", "security"),
  it(7, 7, "Оффер", "offer"), it(8, 8, "Пришедшие в 1 день", "first_day"),
];
const LIB = {
  rows: [["selected", "Отобранные резюме"], ["contact", "Первичный контакт"], ["screening", "Пройденное первичное интервью"], ["interview", "Интервью"],
    ["meeting", "Собеседование"], ["security", "Проверка СБ"], ["offer", "Оффер"], ["first_day", "Пришедшие в 1 день"]].map(([code, title], i) => ({ code, ord: i + 1, title })),
  stages: [
    ...CURRENT.map((i) => ({ id: i.stage_id, title: i.title, report_row: i.report_row, is_standard: true, is_active: true })),
    { id: 9, title: "Тестовое задание", report_row: "interview", is_standard: false, is_active: true },
    { id: 10, title: "Собеседование с руководителем", report_row: "meeting", is_standard: false, is_active: true },
    { id: 11, title: "Старый этап", report_row: "meeting", is_standard: false, is_active: false },
  ],
};
const TEST = LIB.stages[8];
const BOSS = LIB.stages[9];
const keys = (draft) => draft.map((d) => d.key);
const titles = (draft) => draft.map((d) => d.label || d.title);

test("S3: черновик — копия действующего набора, ключ этапа — его slot", () => {
  const draft = toDraft(CURRENT);
  assert.equal(draft.length, 8);
  assert.deepEqual(keys(draft), ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8"]);
  assert.equal(draft[4].slot, 5);
  draft[0].label = "x";
  assert.equal(CURRENT[0].label, null, "исходный набор не меняется");
});

test("S3: до правки набор не изменён — «Отправить» неактивна; правка включает её", () => {
  assert.equal(isChanged(toDraft(CURRENT), CURRENT), false);
  assert.equal(isChanged(moveItem(addItem(toDraft(CURRENT), TEST, "n1"), "n1", -1), CURRENT), true);
  assert.equal(isChanged(removeItem(toDraft(CURRENT), "s6"), CURRENT), true);
  const named = toDraft(CURRENT);
  named[4].label = "С главным инженером";
  assert.equal(isChanged(named, CURRENT), true);
  named[4].label = "  ";
  assert.equal(isChanged(named, CURRENT), false, "пустая подпись — как её отсутствие");
});

test("S3: добавить этап — в конец своей строки отчёта; нового этапа ещё нет на сервере, slot пуст", () => {
  let draft = addItem(toDraft(CURRENT), TEST, "n1");       // «Тестовое задание» → «Интервью»
  assert.deepEqual(titles(draft).slice(3, 6), ["Интервью", "Тестовое задание", "Собеседование"]);
  draft = addItem(draft, BOSS, "n2");                   // «Собеседование с руководителем» → после «Собеседование»
  draft = addItem(draft, BOSS, "n3");                   // тот же этап ещё раз — собеседования с разными руководителями
  assert.deepEqual(titles(draft).slice(5, 9), ["Собеседование", "Собеседование с руководителем", "Собеседование с руководителем", "Проверка СБ"]);
  assert.equal(draft.find((d) => d.key === "n2").slot, null);
});

test("S5: убрана целая строка — этап добавляется на её место по порядку воронки", () => {
  let draft = removeItem(toDraft(CURRENT), "s6");
  assert.deepEqual(removedRows(draft, LIB.rows).map((r) => r.title), ["Проверка СБ"]);
  draft = addItem(draft, LIB.stages[5], "n1");                       // «Проверка СБ» вернулась
  assert.deepEqual(titles(draft), CURRENT.map((i) => i.title));
  assert.deepEqual(removedRows(draft, LIB.rows), []);
});

test("S3: ↑/↓ двигают этап только внутри своей строки; на границе — неактивны", () => {
  let draft = addItem(addItem(toDraft(CURRENT), BOSS, "n1"), BOSS, "n2");
  const group = (d) => groupDraft(d, {}).find((g) => g.code === "meeting");
  assert.deepEqual(group(draft).items.map((i) => [i.title, i.up, i.down]), [
    ["Собеседование", false, true], ["Собеседование с руководителем", true, true], ["Собеседование с руководителем", true, false]]);
  draft = moveItem(draft, "n2", -1);
  assert.deepEqual(group(draft).items.map((i) => i.key), ["s5", "n2", "n1"]);
  assert.deepEqual(moveItem(draft, "s5", -1), draft, "вверх с первого в строке — ничего");
  const single = groupDraft(toDraft(CURRENT), {}).find((g) => g.code === "offer").items[0];
  assert.deepEqual([single.up, single.down], [false, false], "единственный этап строки не двигается");
  assert.equal(draft.map((d) => d.report_row).join(), "selected,contact,screening,interview,meeting,meeting,meeting,security,offer,first_day");
});

test("S3: объявление для скринридера после перестановки — «N-й в строке»", () => {
  const draft = moveItem(addItem(addItem(toDraft(CURRENT), TEST, "n1"), TEST, "n2"), "n2", -1);
  assert.equal(movePlace(draft, "n2", LIB.rows), "«Тестовое задание» — 2-й в строке «Интервью»");
  assert.equal(movePlace(draft, "s4", LIB.rows), "«Интервью» — 1-й в строке «Интервью»");
});

test("S5: этап с людьми не убрать — причина по-русски, как на сервере", () => {
  assert.equal(busyText(1), "На этапе 1 человек — сначала переведите их");
  assert.equal(busyText(3), "На этапе 3 человека — сначала переведите их");
  assert.equal(busyText(5), "На этапе 5 человек — сначала переведите их");
  assert.equal(busyText(12), "На этапе 12 человек — сначала переведите их");
  assert.equal(busyText(22), "На этапе 22 человека — сначала переведите их");
  const groups = groupDraft(toDraft(CURRENT), { 4: 3, 5: 1 });
  const find = (code) => groups.find((g) => g.code === code).items[0];
  assert.deepEqual([find("interview").count, find("interview").peopleText, find("interview").remove.disabled, find("interview").remove.reason],
    [3, "3 чел.", true, "На этапе 3 человека — сначала переведите их"]);
  assert.equal(find("meeting").remove.reason, "На этапе 1 человек — сначала переведите их");
  assert.equal(find("security").remove.disabled, false);
  assert.equal(find("security").peopleText, "—");
});

test("S5: «Отобранные резюме» и «Пришедшие в 1 день» не убираются; последний этап другой строки — можно, с пометкой «нет в наборе»", () => {
  const groups = groupDraft(toDraft(CURRENT), {});
  const find = (code) => groups.find((g) => g.code === code).items[0];
  assert.equal(find("selected").remove.disabled, true);
  assert.equal(find("first_day").remove.disabled, true);
  assert.match(find("selected").remove.reason, /нельзя убрать/);
  assert.equal(find("security").remove.disabled, false);
  assert.equal(find("security").remove.lastInRow, true);
  assert.equal(find("security").remove.note, "в отчёте — «нет в наборе»");
  // вторую «Отобранные резюме» добавить можно — тогда любую из двух убрать можно
  const two = groupDraft(addItem(toDraft(CURRENT), { id: 12, title: "Резюме с сайта", report_row: "selected", is_active: true }, "n1"), {});
  assert.deepEqual(two.find((g) => g.code === "selected").items.map((i) => i.remove.disabled), [false, false]);
});

test("S3: что уходит на сервер — slot у прежних, без slot у новых, подпись и причина", () => {
  const draft = addItem(toDraft(CURRENT), BOSS, "n1");
  draft.find((d) => d.key === "n1").label = "  С главным инженером ";
  draft.find((d) => d.key === "s6").label = "";
  const payload = proposalPayload(removeItem(draft, "s7"), "  Два собеседования ");
  assert.equal(payload.note, "Два собеседования");
  assert.deepEqual(payload.items.slice(4, 8), [
    { slot: 5, stage_id: 5 }, { stage_id: 10, label: "С главным инженером" }, { slot: 6, stage_id: 6 }, { slot: 8, stage_id: 8 }]);
  assert.equal(proposalPayload(draft, "").note, undefined, "пустая причина не отправляется");
});

test("S3: «Добавить этап» — действующие этапы справочника по строкам отчёта (optgroup), выведенных из работы нет", () => {
  const groups = addOptions(LIB);
  assert.deepEqual(groups.map((g) => g.title).slice(0, 5), ["Отобранные резюме", "Первичный контакт", "Пройденное первичное интервью", "Интервью", "Собеседование"]);
  assert.deepEqual(groups.find((g) => g.code === "interview").items.map((i) => i.title), ["Интервью", "Тестовое задание"]);
  assert.deepEqual(groups.find((g) => g.code === "meeting").items.map((i) => i.title), ["Собеседование", "Собеседование с руководителем"]);
  assert.equal(JSON.stringify(groups).includes("Старый этап"), false);
});

test("S3: отозванный или отклонённый набор — снова в редактор: номера, выданные при предложении, не уходят обратно", () => {
  const withdrawn = [...CURRENT.slice(0, 5), it(9, 9, "Собеседование с руководителем", "meeting", { stage_id: 10, label: "С директором" }), ...CURRENT.slice(6)];
  const draft = rebaseDraft(withdrawn, CURRENT);
  assert.deepEqual(draft.map((d) => d.slot), [1, 2, 3, 4, 5, null, 7, 8]);
  assert.equal(draft[5].label, "С директором");
  assert.deepEqual(proposalPayload(draft, "").items[5], { stage_id: 10, label: "С директором" });
  assert.equal(new Set(keys(draft)).size, 8, "ключи разные");
  assert.equal(isChanged(draft, CURRENT), true);
  assert.equal(isChanged(rebaseDraft(CURRENT, CURRENT), CURRENT), false);
});
