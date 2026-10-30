// Передача руководителем в отдел персонала (S9–S11): окно, бейджи статуса, карточка у отдела персонала
import assert from "node:assert/strict";
import test from "node:test";

import {
  NO_OPEN_TEXT, NO_INBOX, REJECT_HINT, REJECT_REASONS, canSend, commentCounter, defaultVacancy, handoffBadge, handoffMeta, handoffsOfPerson, inboxLines, optionList, pickMode, recipientText, sentToast, takenToast, zoneTitle,
} from "../../web/js/handoff-model.js";

const at = (d, h, m = 0) => new Date(2026, 9, d, h, m).toISOString();
const H = (over = {}) => ({ id: 1, person_id: 5, name: "Орлова Мария Сергеевна", vacancy_id: 3, vacancy_title: "Ведущий инженер-электронщик", comment: "Сильный схемотехник",
  state: "sent", created_at: at(30, 9, 12), recruiter: "Петрова А.", stage: null, stage_title: null, reject_reason: null, decided_at: null, ...over });

test("S10: «передан» → «в работе у …» → этап воронки → «вышел на работу» или «отказ»", () => {
  assert.deepEqual(handoffBadge(H()), { text: "передан 30.10", tone: "b", reason: "" });
  assert.deepEqual(handoffBadge(H({ state: "taken", stage: "picked_by_agent", stage_title: "Отобран агентом" })), { text: "в работе у Петровой А.", tone: "g", reason: "" });
  assert.deepEqual(handoffBadge(H({ state: "taken", stage: null })), { text: "в работе у Петровой А.", tone: "g", reason: "" });
  assert.deepEqual(handoffBadge(H({ state: "taken", stage: "manager_interview", stage_title: "Собеседование" })), { text: "этап: собеседование", tone: "v", reason: "" });
  assert.deepEqual(handoffBadge(H({ state: "taken", stage: "hired", stage_title: "Вышел на работу" })), { text: "вышел на работу", tone: "g", reason: "" });
  assert.deepEqual(handoffBadge(H({ state: "taken", stage: "rejected_by_us", stage_title: "Отказали мы", reject_reason: "Не подошёл по опыту" })),
    { text: "отказ", tone: "r", reason: "Не подошёл по опыту" });
  assert.deepEqual(handoffBadge(H({ state: "taken", stage: "rejected_by_candidate", reject_reason: "Ушёл к другому работодателю" })).reason, "Ушёл к другому работодателю");
  assert.deepEqual(handoffBadge(H({ state: "taken", stage: "no_show", stage_title: "Не пришёл" })), { text: "отказ", tone: "r", reason: "" });
});

test("S11: отклонено отделом персонала — причина строкой под бейджем", () => {
  assert.deepEqual(handoffBadge(H({ state: "rejected", reject_reason: "Нет опыта в нашей отрасли", recruiter: "Петрова А." })),
    { text: "отклонён отделом персонала", tone: "r", reason: "Нет опыта в нашей отрасли" });
  assert.equal(handoffBadge(H({ state: "rejected", reject_reason: null })).reason, "");
});

test("имя ответственного склоняется: «Петрова А.» → «Петровой А.»; незнакомая форма остаётся как есть", () => {
  assert.equal(handoffBadge(H({ state: "taken", recruiter: "Иванов И." })).text, "в работе у Иванова И.");
  assert.equal(handoffBadge(H({ state: "taken", recruiter: "Сидоренко Е." })).text, "в работе у Сидоренко Е.");
  assert.equal(handoffBadge(H({ state: "taken", recruiter: "Волкова К." })).text, "в работе у Волковой К.");
  assert.equal(handoffBadge(H({ state: "taken", recruiter: "Кузнец А." })).text, "в работе у Кузнеца А.");
  assert.equal(handoffBadge(H({ state: "taken", recruiter: "" })).text, "в работе у отдела персонала");
});

test("S9: «Передано Петровой А.» — уведомление", () => {
  assert.equal(sentToast({ recruiter: "Петрова А." }), "Передано Петровой А.");
  assert.equal(sentToast({}), "Передано в отдел персонала");
});

test("S9: вакансии для выбора — с баллом сверху по убыванию, остальные по названию; уже переданные видны и неактивны", () => {
  const options = { comment_min: 50, vacancies: [
    { id: 1, title: "Токарь", score: null, recruiter: "Иванов И.", already: false },
    { id: 2, title: "Менеджер продаж", score: 77, recruiter: "Петрова А.", already: false },
    { id: 3, title: "Ведущий инженер", score: 91, recruiter: "Петрова А.", already: true },
    { id: 4, title: "Кладовщик", score: null, recruiter: "Иванов И.", already: false }] };
  assert.deepEqual(optionList(options).map((v) => [v.id, v.label, v.disabled]), [
    [3, "Ведущий инженер — балл 91 · уже передан", true], [2, "Менеджер продаж — балл 77", false], [4, "Кладовщик", false], [1, "Токарь", false]]);
  assert.deepEqual(optionList(null), []);
  assert.deepEqual(optionList({ vacancies: [] }), []);
});

test("S9: по умолчанию выбрана первая доступная вакансия с баллом; нет доступных — ничего", () => {
  const list = optionList({ vacancies: [{ id: 3, title: "А", score: 91, already: true }, { id: 2, title: "Б", score: 77, already: false }, { id: 1, title: "В", score: null, already: false }] });
  assert.equal(defaultVacancy(list), 2);
  assert.equal(defaultVacancy(optionList({ vacancies: [{ id: 3, title: "А", score: 91, already: true }] })), null);
  assert.equal(defaultVacancy([]), null);
});

test("S9: до пяти вакансий — радиокнопки, больше — список", () => {
  assert.equal(pickMode(5), "radios");
  assert.equal(pickMode(6), "select");
  assert.equal(pickMode(0), "radios");
});

test("S9: «Получит: Петрова А., ответственный HR»", () => {
  assert.equal(recipientText({ recruiter: "Петрова А." }), "Получит: Петрова А., ответственный HR");
  assert.equal(recipientText({ recruiter: "" }), "");
  assert.equal(recipientText(null), "");
});

test("S9: счётчик комментария — «Ещё 54 знака» → «✓ достаточно», порог из ответа API", () => {
  assert.deepEqual(commentCounter("", 50), { text: "Ещё 50 знаков", ok: false });
  assert.deepEqual(commentCounter("а".repeat(49), 50), { text: "Ещё 1 знак", ok: false });
  assert.deepEqual(commentCounter("а".repeat(46), 50), { text: "Ещё 4 знака", ok: false });
  assert.deepEqual(commentCounter("а".repeat(50), 50), { text: "✓ достаточно", ok: true });
  assert.deepEqual(commentCounter(`  ${"а".repeat(49)}  `, 50), { text: "Ещё 1 знак", ok: false });   // пробелы по краям не считаются
  assert.deepEqual(commentCounter("а".repeat(10), undefined), { text: "Ещё 40 знаков", ok: false });   // порог по умолчанию 50
});

test("S9: «Передать» активна, только когда выбрана доступная вакансия и комментарий достаточный", () => {
  const list = optionList({ vacancies: [{ id: 2, title: "Б", score: 77, already: false }, { id: 3, title: "А", score: 91, already: true }] });
  const long = "а".repeat(50);
  assert.equal(canSend({ vacancyId: 2, comment: long, min: 50, list }), true);
  assert.equal(canSend({ vacancyId: 2, comment: "коротко", min: 50, list }), false);
  assert.equal(canSend({ vacancyId: null, comment: long, min: 50, list }), false);
  assert.equal(canSend({ vacancyId: 3, comment: long, min: 50, list }), false);   // «уже передан»
  assert.equal(canSend({ vacancyId: 99, comment: long, min: 50, list }), false);
});

test("S9: открытых вакансий нет — кнопка с объяснением", () => {
  assert.equal(NO_OPEN_TEXT, "Нет открытых вакансий — передать некуда");
});

test("S11: карточка передачи у отдела персонала — кто и когда передал, заголовок зоны", () => {
  assert.equal(handoffMeta({ from_user: "Иванов И. И.", from_unit: "начальник ОГМ", created_at: at(30, 9, 12) }), "Передал: Иванов И. И., начальник ОГМ · 30.10 09:12");
  assert.equal(handoffMeta({ from_user: "Иванов И. И.", created_at: at(30, 9, 5) }), "Передал: Иванов И. И. · 30.10 09:05");
  assert.equal(zoneTitle(), "Передали руководители — решите, брать ли в работу");
});

test("S10: в досье — передачи этого человека, свежие сверху; чужих нет", () => {
  const items = [H({ id: 1, person_id: 5, created_at: at(28, 10) }), H({ id: 2, person_id: 6 }), H({ id: 3, person_id: 5, created_at: at(30, 9) })];
  assert.deepEqual(handoffsOfPerson(items, 5).map((x) => x.id), [3, 1]);
  assert.deepEqual(handoffsOfPerson(items, "5").map((x) => x.id), [3, 1]);
  assert.deepEqual(handoffsOfPerson(items, 99), []);
  assert.deepEqual(handoffsOfPerson(null, 5), []);
});

test("S11: строки карточки передачи: вакансия, кто передал, комментарий; «Ответственный» — только у руководителя отдела", () => {
  const item = H({ from_user: "Иванов И. И.", from_unit: "начальник ОГМ", score: 88 });
  assert.deepEqual(inboxLines(item, { seeAll: false }), ["Вакансия: Ведущий инженер-электронщик", "Передал: Иванов И. И., начальник ОГМ · 30.10 09:12"]);
  assert.deepEqual(inboxLines(item, { seeAll: true }), ["Вакансия: Ведущий инженер-электронщик", "Передал: Иванов И. И., начальник ОГМ · 30.10 09:12", "Ответственный: Петрова А."]);
  assert.equal(inboxLines(H({ recruiter: null }), { seeAll: true }).length, 2);
});

test("S11: «Взять в работу» и «Отклонить»: подсказки и итог", () => {
  assert.equal(REJECT_HINT, "Причину увидит руководитель");
  assert.ok(REJECT_REASONS.length >= 3);
  assert.equal(NO_INBOX, "Передач от руководителей нет");
  assert.equal(takenToast(H()), "Взят в работу по вакансии «Ведущий инженер-электронщик»");
});
