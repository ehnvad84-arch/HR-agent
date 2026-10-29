// «Воронка»: этапы, перевод кандидата, встречи и отказы (S24, S36)
import assert from "node:assert/strict";
import test from "node:test";

import {
  TERMINAL, buildMove, dayText, defaultTarget, funnelTotals, localIso, meetingStage, moveOptions, needsWindow, pickVacancy,
  reasonMode, stageSummary, statusLabel,
} from "../../web/js/screens/funnel-model.js";

const STAGES = [
  { status: "picked_by_agent", title: "Отобран агентом", people: [{ candidate_id: 1 }, { candidate_id: 2 }, { candidate_id: 3 }], avg_days: 4 },
  { status: "first_message_sent", title: "Написали", people: [{ candidate_id: 4 }], avg_days: 6 },
  { status: "replied", title: "Ответил", people: [], avg_days: null },
  { status: "phone_interview", title: "Телефонное интервью", people: [{ candidate_id: 5 }], avg_days: 1 },
  { status: "manager_interview", title: "Встреча с руководителем", people: [], avg_days: null },
  { status: "test_task", title: "Тестовое задание", people: [], avg_days: null },
  { status: "security_check", title: "Проверка СБ", people: [], avg_days: null },
  { status: "offer", title: "Оффер", people: [], avg_days: null },
  { status: "reserve", title: "Резерв", people: [{ candidate_id: 6 }], avg_days: 10 },
];

test("итоговые статусы — как в задаче, русскими словами", () => {
  assert.deepEqual(TERMINAL, [["hired", "Вышел на работу"], ["rejected_by_us", "Отказали мы"],
    ["rejected_by_candidate", "Отказался"], ["no_show", "Не пришёл"]]);
});

test("S24: этап — сколько человек, доля от всех в воронке, среднее время на этапе", () => {
  assert.equal(funnelTotals(STAGES), 6);
  assert.deepEqual(stageSummary(STAGES[0], 6), { count: 3, countText: "3 чел.", pct: 50, avgText: "в среднем 4 дн. на этапе", empty: false });
  assert.deepEqual(stageSummary(STAGES[2], 6), { count: 0, countText: "0 чел.", pct: 0, avgText: "никого", empty: true });
  assert.equal(stageSummary(STAGES[1], 6).pct, 17);
  assert.equal(stageSummary({ people: [{ candidate_id: 1 }], avg_days: 0 }, 1).avgText, "в среднем меньше суток на этапе");
  assert.equal(stageSummary({ people: [], avg_days: null }, 0).pct, 0);   // пустая воронка: без деления на ноль
});

test("сколько дней человек на этапе", () => {
  assert.equal(dayText(5), "5 дн. на этапе");
  assert.equal(dayText(0), "на этапе меньше суток");
  assert.equal(dayText(null), "");
});

test("встреча назначается на этапах интервью и тестового задания", () => {
  for (const s of ["phone_interview", "manager_interview", "test_task"]) assert.equal(meetingStage(s), true);
  for (const s of ["replied", "offer", "hired", "rejected_by_us"]) assert.equal(meetingStage(s), false);
});

test("причина: обязательна для отказов, по желанию — у «не пришёл», остальным не нужна", () => {
  assert.equal(reasonMode("rejected_by_us"), "required");
  assert.equal(reasonMode("rejected_by_candidate"), "required");
  assert.equal(reasonMode("no_show"), "optional");
  assert.equal(reasonMode("hired"), "none");
  assert.equal(reasonMode("replied"), "none");
});

test("перетаскивание: сразу переводим, если не нужны дата и причина; иначе открываем окно", () => {
  assert.equal(needsWindow("replied"), false);
  assert.equal(needsWindow("offer"), false);
  assert.equal(needsWindow("phone_interview"), true);
  assert.equal(needsWindow("rejected_by_candidate"), true);
  assert.equal(needsWindow("no_show"), true);
});

test("S24: куда можно перевести — этапы воронки и итоги, без текущего этапа", () => {
  const options = moveOptions(STAGES, "first_message_sent");
  assert.equal(options.stages.length, 8);
  assert.ok(!options.stages.some((o) => o.value === "first_message_sent"));
  assert.deepEqual(options.stages[0], { value: "picked_by_agent", label: "Отобран агентом" });
  assert.deepEqual(options.final.map((o) => o.value), ["hired", "rejected_by_us", "rejected_by_candidate", "no_show"]);
  assert.deepEqual(options.final[0], { value: "hired", label: "Вышел на работу" });
});

test("выбор по умолчанию — следующий этап", () => {
  assert.equal(defaultTarget(STAGES, "first_message_sent"), "replied");
  assert.equal(defaultTarget(STAGES, "picked_by_agent"), "first_message_sent");
  assert.equal(defaultTarget(STAGES, "reserve"), "hired");   // после последнего этапа — итог
  assert.equal(defaultTarget(STAGES, "mystery"), "picked_by_agent");
});

test("время встречи — ISO с часовым поясом браузера", () => {
  assert.equal(localIso("2026-10-30T11:00", -180), "2026-10-30T11:00:00+03:00");
  assert.equal(localIso("2026-10-30T11:00", 300), "2026-10-30T11:00:00-05:00");
  assert.equal(localIso("2026-10-30T11:00", 0), "2026-10-30T11:00:00+00:00");
  assert.equal(localIso("2026-10-30T11:00", -330), "2026-10-30T11:00:00+05:30");
  assert.equal(localIso("", -180), "");
  assert.equal(localIso("не дата", -180), "");
});

test("S36: перевод с назначенной встречей уходит с scheduled_at", () => {
  assert.deepEqual(buildMove({ vacancyId: 7, toStatus: "phone_interview", when: "2026-10-30T11:00", offsetMinutes: -180 }),
    { ok: true, payload: { vacancy_id: 7, to_status: "phone_interview", scheduled_at: "2026-10-30T11:00:00+03:00" } });
  // время для этапа без встречи не отправляется, даже если поле осталось заполненным
  assert.deepEqual(buildMove({ vacancyId: 7, toStatus: "replied", when: "2026-10-30T11:00", offsetMinutes: -180 }),
    { ok: true, payload: { vacancy_id: 7, to_status: "replied" } });
  assert.deepEqual(buildMove({ vacancyId: 7, toStatus: "test_task", when: "", offsetMinutes: -180 }),
    { ok: true, payload: { vacancy_id: 7, to_status: "test_task" } });
});

test("отказ: причина обязательна и уходит комментарием", () => {
  assert.equal(buildMove({ vacancyId: 7, toStatus: "rejected_by_us", comment: "   " }).error, "Опишите причину отказа");
  assert.deepEqual(buildMove({ vacancyId: 7, toStatus: "rejected_by_candidate", comment: " Принял другое предложение " }),
    { ok: true, payload: { vacancy_id: 7, to_status: "rejected_by_candidate", comment: "Принял другое предложение" } });
  assert.deepEqual(buildMove({ vacancyId: 7, toStatus: "no_show", comment: "" }),
    { ok: true, payload: { vacancy_id: 7, to_status: "no_show" } });
  assert.deepEqual(buildMove({ vacancyId: 7, toStatus: "no_show", comment: "Не пришёл на звонок" }),
    { ok: true, payload: { vacancy_id: 7, to_status: "no_show", comment: "Не пришёл на звонок" } });
  assert.equal(buildMove({ vacancyId: 7, toStatus: "" }).error, "Выберите этап");
});

test("без номера вакансии — первая открытая (на паузе — только если других нет)", () => {
  const open = [{ id: 3, state: "on_hold" }, { id: 2, state: "open" }, { id: 1, state: "open" }];
  assert.equal(pickVacancy(open).id, 2);
  assert.equal(pickVacancy([{ id: 9, state: "on_hold" }]).id, 9);
  assert.equal(pickVacancy([]), null);
  assert.equal(pickVacancy(null), null);
});

test("название этапа или итога по коду", () => {
  assert.equal(statusLabel(STAGES, "phone_interview"), "Телефонное интервью");
  assert.equal(statusLabel(STAGES, "hired"), "Вышел на работу");
  assert.equal(statusLabel(STAGES, "rejected_by_candidate"), "Отказался");
  assert.equal(statusLabel(STAGES, "mystery"), "mystery");
});
