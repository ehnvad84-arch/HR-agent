// «Сегодня»: подписи, даты и разбор ответа /api/today без DOM (S4, S5, S36, S37)
import assert from "node:assert/strict";
import test from "node:test";

import {
  actionTitle, alertReason, badgeCount, dayLabel, interviewTitle, isEmptyDay, plural, scoreBand, silentText,
  statusTitle, timeLabel, whenLabel,
} from "../../web/js/format.js";
import { normalizeToday } from "../../web/js/screens/today-model.js";

const NOW = new Date(2026, 9, 29, 14, 30);   // 29 октября 2026, 14:30 по часовому поясу процесса
const at = (d, h, m = 0) => new Date(2026, 9, d, h, m).toISOString();

test("plural: день, дня, дней", () => {
  assert.deepEqual([1, 2, 5, 11, 12, 21, 22, 25, 101, 111].map((n) => plural(n, "день", "дня", "дней")),
    ["день", "дня", "дней", "дней", "дней", "день", "дня", "дней", "день", "дней"]);
});

test("день встречи: сегодня, завтра, вчера, иначе число и месяц", () => {
  assert.equal(dayLabel(new Date(2026, 9, 29, 8), NOW), "сегодня");
  assert.equal(dayLabel(new Date(2026, 9, 30, 23, 59), NOW), "завтра");
  assert.equal(dayLabel(new Date(2026, 9, 28, 0, 1), NOW), "вчера");
  assert.equal(dayLabel(new Date(2026, 10, 3, 10), NOW), "3 ноября");
  assert.equal(dayLabel(new Date(2025, 11, 31, 10), NOW), "31 декабря 2025");   // другой год — с годом
});

test("время — часы и минуты в часовом поясе браузера", () => {
  assert.equal(timeLabel(new Date(2026, 9, 29, 9, 5)), "09:05");
  assert.equal(timeLabel(at(29, 15, 0)), "15:00");
});

test("whenLabel: «сегодня, 15:00», «завтра, 10:30»", () => {
  assert.equal(whenLabel(at(29, 15), NOW), "сегодня, 15:00");
  assert.equal(whenLabel(at(30, 10, 30), NOW), "завтра, 10:30");
  assert.equal(whenLabel("не дата", NOW), "");
  assert.equal(whenLabel(null, NOW), "");
});

test("названия этапов, действий, встреч и причин сигналов — по-русски, незнакомое не ломает экран", () => {
  assert.equal(statusTitle("first_message_sent"), "Написали");
  assert.equal(statusTitle("rejected_by_candidate"), "Отказ кандидата");
  assert.equal(statusTitle("что-то новое"), "что-то новое");
  assert.equal(actionTitle("follow_up"), "Напоминание");
  assert.equal(actionTitle("open_contact"), "Открыть контакт");
  assert.equal(actionTitle("xyz"), "Действие");
  assert.equal(interviewTitle("phone"), "Телефонное интервью");
  assert.equal(interviewTitle("zzz"), "Встреча");
  assert.deepEqual(alertReason("new_high_score"), { title: "новый, высокий балл", tone: "g" });
  assert.deepEqual(alertReason("nope"), { title: "сигнал", tone: "" });
});

test("зависший: «Написали · последний контакт 6 дней назад»", () => {
  assert.equal(silentText("first_message_sent", 6), "Написали · последний контакт 6 дней назад");
  assert.equal(silentText("replied", 21), "Ответил · последний контакт 21 день назад");
  assert.equal(silentText("offer", null), "Оффер");
});

test("полоса балла: ниже 75 — оранжевая", () => {
  assert.equal(scoreBand(88), "");
  assert.equal(scoreBand(75), "");
  assert.equal(scoreBand(74), "mid");
  assert.equal(scoreBand(null), "mid");
});

test("S5: пустой день — все четыре списка пусты", () => {
  assert.equal(isEmptyDay({ signals: [], actions: [], stalled: [], meetings: [] }), true);
  assert.equal(isEmptyDay({ signals: [], actions: [], stalled: [{ candidate_id: 1 }], meetings: [] }), false);
  assert.equal(isEmptyDay({ signals: [], actions: [], stalled: [], meetings: [{}] }), false);
});

test("бейдж вкладки: сигналы плюс подтверждения", () => {
  assert.equal(badgeCount({ signals: [1, 2, 3], actions: [1], stalled: [1, 2], meetings: [1] }), 4);
  assert.equal(badgeCount({ signals: [], actions: [], stalled: [], meetings: [] }), 0);
});

test("normalizeToday: недостающие списки пусты, напоминания в очереди помнятся по кандидату", () => {
  const data = normalizeToday({
    signals: [{ alert_id: 1, person_id: 5, score: 80 }],
    actions: [{ action_id: 9, kind: "follow_up", candidate_id: 3 }, { action_id: 10, kind: "first_message", candidate_id: 4 },
      { action_id: 11, kind: "merge_persons", candidate_id: null }],
  });
  assert.deepEqual(data.stalled, []);
  assert.deepEqual(data.meetings, []);
  assert.equal(data.badge, 4);
  assert.equal(data.empty, false);
  assert.deepEqual([...data.followUpQueued], [3]);
});

test("normalizeToday: пустой ответ — пустой день", () => {
  const data = normalizeToday({});
  assert.equal(data.empty, true);
  assert.equal(data.badge, 0);
});

test("S11: передачи руководителей — на бейдже вкладки и не дают дню быть пустым", () => {
  assert.equal(isEmptyDay({ signals: [], actions: [], stalled: [], meetings: [], handoffs: [{ id: 1 }] }), false);
  assert.equal(isEmptyDay({ signals: [], actions: [], stalled: [], meetings: [], handoffs: [] }), true);
  assert.equal(badgeCount({ signals: [1], actions: [1], stalled: [1], meetings: [1], handoffs: [1, 2, 3] }), 5);
  const data = normalizeToday({ handoffs: [{ id: 7, person_id: 3 }] });
  assert.equal(data.handoffs.length, 1);
  assert.equal(data.badge, 1);
  assert.equal(data.empty, false);
  assert.deepEqual(normalizeToday({}).handoffs, []);
});
