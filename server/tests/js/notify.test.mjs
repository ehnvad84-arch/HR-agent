// Сообщения о загрузках для отдела персонала (S20): колокольчик, список, красная полоса тревоги
import assert from "node:assert/strict";
import test from "node:test";

import { ACK_LABEL, EMPTY_TEXT, POLL_MS, alarmView, ackText, bellView, itemView, unreadIds } from "../../web/js/notify-model.js";

const at = (d, h, m = 0) => new Date(2026, 9, d, h, m).toISOString();
const OK = { id: 5, kind: "load_ok", body: { title: "Загрузка hh: прошла", text: "просмотрено 412, новых 57, изменилось 12", job: "collect_hh", severity: "ok" },
  created_at: at(30, 18, 2), read_at: null, ack: null };
const ALARM = { id: 6, kind: "load_alarm", body: { title: "Загрузка hh: не прошла — требуется разбирательство", text: "лимит исчерпан", job: "collect_hh", severity: "alarm" },
  created_at: at(30, 18, 5), read_at: null, ack: null };
const REPLY = { items: [ALARM, OK], unread: 2, alarm: { id: 6, created_at: ALARM.created_at, title: ALARM.body.title, text: "лимит исчерпан" } };

test("опрос раз в минуту — как у полосы хода загрузки", () => {
  assert.equal(POLL_MS, 60000);
  assert.equal(ACK_LABEL, "Разбираюсь");
  assert.equal(EMPTY_TEXT, "Сообщений о загрузках пока нет");
});

test("колокольчик: число непрочитанных на значке, «9+» для большого; подпись для скринридера со словами", () => {
  assert.deepEqual(bellView(REPLY), { count: 2, badge: "2", alarm: true, label: "Сообщения о загрузках: новых 2, есть тревога" });
  assert.deepEqual(bellView({ items: [], unread: 0, alarm: null }), { count: 0, badge: "", alarm: false, label: "Сообщения о загрузках: новых нет" });
  assert.equal(bellView({ items: [], unread: 14, alarm: null }).badge, "9+");
  assert.deepEqual(bellView(null), { count: 0, badge: "", alarm: false, label: "Сообщения о загрузках: новых нет" });
});

test("строка списка: итог словом, тон, время, кнопка «Разбираюсь» только у тревоги без ответственного", () => {
  const ok = itemView(OK);
  assert.deepEqual([ok.tone, ok.status, ok.canAck, ok.unread, ok.ackText], ["g", "прошла", false, true, null]);
  assert.equal(ok.title, "Загрузка hh: прошла");
  assert.equal(ok.text, "просмотрено 412, новых 57, изменилось 12");
  const alarm = itemView(ALARM);
  assert.deepEqual([alarm.tone, alarm.status, alarm.canAck], ["r", "не прошла — требуется разбирательство", true]);
  const taken = itemView({ ...ALARM, read_at: at(30, 18, 9), ack: { by: "Петрова А.", at: at(30, 18, 9) } });
  assert.deepEqual([taken.canAck, taken.unread, taken.ackText], [false, false, "Разбирается: Петрова А."]);
  assert.equal(ackText("Петрова А."), "Разбирается: Петрова А.");
});

test("красная полоса: пока никто не нажал «Разбираюсь»; после — «Разбирается: …»", () => {
  assert.deepEqual(alarmView(REPLY, null), { kind: "alarm", id: 6, title: ALARM.body.title, text: "лимит исчерпан", canAck: true });
  assert.equal(alarmView({ items: [OK], unread: 1, alarm: null }, null), null);
  assert.equal(alarmView(null, null), null);
  const calm = alarmView({ items: [], unread: 0, alarm: null }, { by: "Петрова А.", title: "Загрузка hh: не прошла — требуется разбирательство" });
  assert.deepEqual(calm, { kind: "taken", id: null, title: "Загрузка hh: не прошла — требуется разбирательство", text: "Разбирается: Петрова А.", canAck: false });
});

test("при открытии списка отмечаются прочитанными только непрочитанные", () => {
  assert.deepEqual(unreadIds(REPLY), [6, 5]);
  assert.deepEqual(unreadIds({ items: [{ ...OK, read_at: at(30, 19) }, ALARM] }), [6]);
  assert.deepEqual(unreadIds(null), []);
});
