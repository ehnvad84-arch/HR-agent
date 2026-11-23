// «Воронка» вакансии: доска по этапам набора, перетаскивание вперёд, отмена последней отметки (S6, S10, S16)
import assert from "node:assert/strict";
import test from "node:test";

import {
  canDropOn, canUndo, dayText, funnelTotals, isClosedVacancy, isLastStage, lastMarkText, leftBlock, localIso, moscowDay, nextStage, personNote,
  pickVacancy, stageIndex, stageSummary, undoToast, VIEWS, viewOf,
} from "../../web/js/screens/funnel-model.js";

const person = (id, extra = {}) => ({ candidate_id: id, person_id: id + 100, name: `Человек ${id}`, days: 2, ...extra });
const STAGES = [
  { slot: 1, pos: 1, title: "Отобранные резюме", report_row: "selected", people: [person(1), person(2), person(3)], avg_days: 4 },
  { slot: 2, pos: 2, title: "Первичный контакт", report_row: "contact", people: [person(4)], avg_days: 6 },
  { slot: 5, pos: 3, title: "Собеседование с руководителем отдела", report_row: "meeting", people: [], avg_days: null },
  { slot: 8, pos: 4, title: "Пришедшие в 1 день", report_row: "first_day", people: [person(5)], avg_days: 1 },
];

test("S24: этап — сколько человек, доля от всех в воронке, среднее время на этапе", () => {
  assert.equal(funnelTotals(STAGES), 5);
  assert.deepEqual(stageSummary(STAGES[0], 5), { count: 3, countText: "3 чел.", pct: 60, avgText: "в среднем 4 дн. на этапе", empty: false });
  assert.deepEqual(stageSummary(STAGES[2], 5), { count: 0, countText: "0 чел.", pct: 0, avgText: "никого", empty: true });
  assert.equal(stageSummary({ people: [{ candidate_id: 1 }], avg_days: 0 }, 1).avgText, "в среднем меньше суток на этапе");
  assert.equal(stageSummary({ people: [], avg_days: null }, 0).pct, 0);   // пустая воронка: без деления на ноль
});

test("сколько дней человек на этапе", () => {
  assert.equal(dayText(5), "5 дн. на этапе");
  assert.equal(dayText(0), "на этапе меньше суток");
  assert.equal(dayText(null), "");
});

test("S6: следующий этап набора — по порядку колонок, у последнего следующего нет", () => {
  assert.equal(stageIndex(STAGES, 5), 2);
  assert.equal(stageIndex(STAGES, 99), -1);
  assert.equal(nextStage(STAGES, 1).slot, 2);
  assert.equal(nextStage(STAGES, 2).slot, 5);   // slot не равен порядку: ориентир — pos колонки
  assert.equal(nextStage(STAGES, 8), null);
  assert.equal(nextStage(STAGES, 99), null);
  assert.equal(isLastStage(STAGES, 8), true);
  assert.equal(isLastStage(STAGES, 5), false);
});

test("S6: перетащить можно только на следующий этап — не назад, не через этап, не на тот же", () => {
  assert.equal(canDropOn(STAGES, 1, 2), true);
  assert.equal(canDropOn(STAGES, 1, 5), false);
  assert.equal(canDropOn(STAGES, 5, 2), false);
  assert.equal(canDropOn(STAGES, 2, 2), false);
  assert.equal(canDropOn(STAGES, 8, 1), false);
});

test("S16: день отметки — по Москве, а не по часовому поясу браузера", () => {
  assert.equal(moscowDay("2026-11-23T21:30:00+00:00"), "2026-11-24");   // 00:30 в Москве
  assert.equal(moscowDay("2026-11-23T20:59:00+00:00"), "2026-11-23");
  assert.equal(moscowDay("не дата"), "");
});

test("S16: «Отменить последнюю отметку» — автору в тот же день, руководителю ОП всегда", () => {
  const now = new Date("2026-11-23T12:00:00+03:00");
  const mark = (by, at) => ({ by, at, outcome: "passed", slot: 1, by_name: "Иванова А." });
  assert.equal(canUndo({ last: mark(7, "2026-11-23T09:00:00+03:00"), userId: 7, canApprove: false, now }), true);
  assert.equal(canUndo({ last: mark(7, "2026-11-22T18:00:00+03:00"), userId: 7, canApprove: false, now }), false);   // вчерашняя
  assert.equal(canUndo({ last: mark(8, "2026-11-23T09:00:00+03:00"), userId: 7, canApprove: false, now }), false);   // чужая
  assert.equal(canUndo({ last: mark(8, "2026-11-20T09:00:00+03:00"), userId: 7, canApprove: true, now }), true);
  assert.equal(canUndo({ last: null, userId: 7, canApprove: true, now }), false);   // отметок ещё не было
});

test("S16: подпись последней отметки — исход, кто, когда", () => {
  const now = new Date("2026-11-23T12:00:00+03:00");
  assert.match(lastMarkText({ by_name: "Иванова А.", at: "2026-11-23T09:05:00+03:00", outcome: "passed" }, now),
    /^Последняя отметка: прошёл · Иванова А. · (сегодня|вчера), \d{2}:\d{2}$/);   // «сегодня» или «вчера» — по часовому поясу машины
  assert.equal(lastMarkText({ by_name: null, at: "2026-11-20T09:05:00+03:00", outcome: "failed" }, now).startsWith("Последняя отметка: не прошёл"), true);
  assert.equal(lastMarkText(null, now), "");
});

test("S16: выбывшие с доски — блок «Выбыли (N)», исход и этап, отмена по тем же правилам", () => {
  const now = new Date("2026-11-24T12:00:00+03:00");
  const left = [
    person(11, { stage: "Первичный контакт", last_mark: { by: 7, at: "2026-11-24T09:00:00+03:00", outcome: "refused", slot: 2, by_name: "Иванова А." } }),
    person(12, { stage: "Пришедшие в 1 день", last_mark: { by: 7, at: "2026-11-23T09:00:00+03:00", outcome: "passed", slot: 8, by_name: "Иванова А." } }),
  ];
  const own = leftBlock(left, { userId: 7, canApprove: false, canMove: true, now });
  assert.equal(own.title, "Выбыли (2)");
  assert.deepEqual(own.rows.map((r) => [r.person.candidate_id, r.text, r.undo]),
    [[11, "отказался · этап «Первичный контакт»", true], [12, "вышел на работу · этап «Пришедшие в 1 день»", false]]);   // вчерашняя — не своя
  const head = leftBlock(left, { userId: 99, canApprove: true, canMove: true, now });
  assert.deepEqual(head.rows.map((r) => r.undo), [true, true]);   // руководитель ОП — всегда
  assert.deepEqual(leftBlock(left, { userId: 7, canApprove: true, canMove: false, now }).rows.map((r) => r.undo), [false, false]);   // без права отмечать — только чтение
  assert.equal(leftBlock([], { userId: 7, canApprove: true, canMove: true, now }), null);
  assert.equal(leftBlock(undefined, { userId: 7, canApprove: true, canMove: true, now }), null);
});

test("S16: после отмены — уведомление с названием этапа", () => {
  assert.equal(undoToast("Анна С.", "Интервью"), "Отметка отменена: Анна С. — снова на этапе «Интервью»");
  assert.equal(undoToast("Анна С.", ""), "Отметка отменена: Анна С.");
});

test("кабинет руководителя: заметка человека — последний комментарий отметки, без него пусто", () => {
  assert.equal(personNote({ notes: [{ comment: "Хорошо ответил" }, { comment: "Старый" }] }), "Хорошо ответил");
  assert.equal(personNote({ notes: [] }), "");
  assert.equal(personNote({}), "");
  assert.equal(personNote({ note: "прежняя форма" }), "прежняя форма");
});

test("время встречи — ISO с часовым поясом браузера", () => {
  assert.equal(localIso("2026-10-30T11:00", -180), "2026-10-30T11:00:00+03:00");
  assert.equal(localIso("2026-10-30T11:00", 300), "2026-10-30T11:00:00-05:00");
  assert.equal(localIso("2026-10-30T11:00", 0), "2026-10-30T11:00:00+00:00");
  assert.equal(localIso("2026-10-30T11:00", -330), "2026-10-30T11:00:00+05:30");
  assert.equal(localIso("", -180), "");
  assert.equal(localIso("не дата", -180), "");
});

test("без номера вакансии — первая открытая (на паузе — только если других нет)", () => {
  const open = [{ id: 3, state: "on_hold" }, { id: 2, state: "open" }, { id: 1, state: "open" }];
  assert.equal(pickVacancy(open).id, 2);
  assert.equal(pickVacancy([{ id: 9, state: "on_hold" }]).id, 9);
  assert.equal(pickVacancy([]), null);
  assert.equal(pickVacancy(null), null);
});

test("вкладки вакансии: «Кандидаты / Отчёт / Этапы», адрес ?view=; неизвестное значение — «Кандидаты»", () => {
  assert.deepEqual(VIEWS.map(([, title]) => title), ["Кандидаты", "Отчёт", "Этапы"]);
  assert.equal(viewOf({ view: "report" }), "report");
  assert.equal(viewOf({ view: "stages" }), "stages");
  assert.equal(viewOf({ view: "candidates" }), "candidates");
  assert.equal(viewOf({ view: "<script>" }), "candidates");
  assert.equal(viewOf({}), "candidates");
  assert.equal(viewOf(undefined), "candidates");
});

test("S16: на закрытой вакансии «Отменить последнюю отметку» нет ни у кого (сервер отвечает vacancy_closed)", () => {
  const now = new Date("2026-11-23T12:00:00+03:00");
  const last = { by: 7, at: "2026-11-23T09:00:00+03:00", outcome: "refused", slot: 1, by_name: "Иванова А." };
  assert.equal(canUndo({ last, userId: 7, canApprove: true, now, closed: true }), false);
  assert.equal(canUndo({ last, userId: 7, canApprove: false, now, closed: false }), true);
  const left = [person(11, { stage: "Отобранные резюме", last_mark: last })];
  assert.deepEqual(leftBlock(left, { userId: 7, canApprove: true, canMove: true, now, closed: true }).rows.map((r) => r.undo), [false]);
  assert.equal(isClosedVacancy({ state: "closed" }), true);
  assert.equal(isClosedVacancy({ state: "cancelled" }), true);
  assert.equal(isClosedVacancy({ state: "on_hold" }), false);
  assert.equal(isClosedVacancy(null), false);
});
