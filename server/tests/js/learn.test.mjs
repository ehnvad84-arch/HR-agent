// «Обучение агента»: чат, недельная сводка, решения руководителя (S29–S31, S41)
import assert from "node:assert/strict";
import test from "node:test";

import {
  MIN_MESSAGE, TAGS, canDecide, canSend, chatItems, counterText, decisionInfo, digestIntro, proposalCounts, tagLabel, targetText,
  weekTitle,
} from "../../web/js/screens/learn-model.js";

const NOW = new Date(2026, 9, 29, 12, 0);

test("теги чата — шесть, русскими словами", () => {
  assert.deepEqual(TAGS.map(([code]) => code), ["remark", "recommendation", "criticism", "question", "reject_reason", "postpone_reason"]);
  assert.deepEqual(TAGS.map(([, label]) => label), ["замечание", "рекомендация", "критика", "вопрос", "причина отказа", "причина «отложить»"]);
  assert.equal(tagLabel("criticism"), "критика");
  assert.equal(tagLabel("новый"), "новый");
});

test("S29: короче 40 знаков не отправляется; счётчик как в прототипе", () => {
  assert.equal(MIN_MESSAGE, 40);
  assert.equal(canSend("а".repeat(39)), false);
  assert.equal(canSend("а".repeat(40)), true);
  assert.equal(canSend(`  ${"а".repeat(39)}  `), false);   // пробелы по краям не считаются
  assert.equal(counterText("абв"), "3 символов, нужно не меньше 40");
  assert.equal(counterText(""), "0 символов, нужно не меньше 40");
});

const FEED = [   // сервер отдаёт новые сверху
  { id: 3, tag: "criticism", body: "Письма для ОТК звучат как для продажников, нужен другой тон.", created_at: "2026-10-29T09:22:00+00:00", author: "Волкова К.",
    reply: "Записал. Замечание войдёт в недельную сводку.", reply_by: "template", digest_id: null },
  { id: 2, tag: "recommendation", body: "Для инженерных ролей стоит поднять вес открытого кода.", created_at: "2026-10-28T18:40:00+00:00", author: "Приходько А.",
    reply: "Записал, учту в сводке.", reply_by: "model", digest_id: 1 },
  { id: 1, tag: "remark", body: "Сообщение без ответа агента, пока ответ не записан.", created_at: "2026-10-27T10:00:00+00:00", author: "Скворцова М.", reply: null, reply_by: null, digest_id: null },
];

test("S29: лента — по порядку времени, ответ агента сразу под сообщением, по шаблону помечен", () => {
  const items = chatItems(FEED);
  assert.deepEqual(items.map((i) => `${i.kind}:${i.id}`), ["user:1", "user:2", "agent:2", "user:3", "agent:3"]);
  const message = items[3];
  assert.equal(message.author, "Волкова К.");
  assert.equal(message.initials, "ВК");
  assert.equal(message.tagLabel, "критика");
  assert.equal(message.text.startsWith("Письма для ОТК"), true);
  assert.equal(items[4].byTemplate, true);
  assert.equal(items[2].byTemplate, false);
  assert.equal(items[4].author, "Агент");
  assert.deepEqual(chatItems([]), []);
});

test("неделя сводки: «22–26 октября» или с двумя месяцами", () => {
  assert.equal(weekTitle("2026-10-22", "2026-10-26", NOW), "Неделя 22–26 октября");
  assert.equal(weekTitle("2026-10-28", "2026-11-01", NOW), "Неделя 28 октября — 1 ноября");
  assert.equal(weekTitle("2026-10-26", "2026-10-26", NOW), "Неделя 26 октября");
  assert.equal(weekTitle("2025-12-29", "2026-01-02", NOW), "Неделя 29 декабря 2025 — 2 января");
});

test("что изменится у предложения: порог или вес — понятным текстом", () => {
  assert.equal(targetText({ setting: "watch_threshold", value: 65 }), "порог наблюдения → 65");
  assert.equal(targetText({ setting: "daily_cards_limit", value: 20 }), "число карточек в день → 20");
  assert.equal(targetText({ profile_code: "hw_lead", feature: "open_code", value: 12 }, { hw_lead: "Ведущий инженер-электронщик" }),
    "вес «Открытый код» в профиле «Ведущий инженер-электронщик» → 12");
  assert.equal(targetText({ profile_code: "hw_lead", feature: "geo", value: 10 }), "вес «География» в профиле «hw_lead» → 10");
  assert.equal(targetText({}), "правка настроек не нужна: решение запишется в журнал");
  assert.equal(targetText(null), "правка настроек не нужна: решение запишется в журнал");
  assert.equal(targetText({ setting: "неведомое", value: 3 }), "неведомое → 3");
});

test("S31: решение руководителя — что видно на карточке", () => {
  const local = (d, h, m) => new Date(2026, 9, d, h, m).toISOString();   // время в часовом поясе, где идёт проверка
  const base = { decided_at: local(29, 10, 30), decision_note: null, applied_at: null };
  assert.deepEqual(decisionInfo({ ...base, decision: "pending", decided_at: null }, NOW),
    { state: "pending", text: "Решение не принято", tone: "o", note: "" });
  const accepted = decisionInfo({ ...base, decision: "accepted", decision_note: "Согласовано с директором" }, NOW);
  assert.equal(accepted.state, "accepted");
  assert.equal(accepted.text, "Принято сегодня, 10:30. Применится ночью (задача apply_digest)");
  assert.equal(accepted.note, "Согласовано с директором");
  const applied = decisionInfo({ ...base, decision: "accepted", applied_at: local(30, 1, 5) }, new Date(2026, 9, 31));
  assert.equal(applied.text, "Принято 29 октября и применено 30 октября");
  assert.equal(decisionInfo({ ...base, decision: "declined" }, NOW).text, "Отклонено сегодня, 10:30. Изменений не будет");
  assert.equal(decisionInfo({ ...base, decision: "discuss" }, NOW).text, "Помечено «обсудить» сегодня, 10:30. Решение пока не принято");
});

test("решить можно, пока нет окончательного решения: «ждёт» и «обсудить»", () => {
  assert.equal(canDecide("pending"), true);
  assert.equal(canDecide("discuss"), true);
  assert.equal(canDecide("accepted"), false);
  assert.equal(canDecide("declined"), false);
});

test("сводка: сколько предложений и сколько ждёт решения; пустая — «собирается по пятницам»", () => {
  const proposals = [{ decision: "pending" }, { decision: "accepted" }, { decision: "discuss" }, { decision: "declined" }, { decision: "pending" }];
  assert.deepEqual(proposalCounts(proposals), { total: 5, pending: 3, accepted: 1, declined: 1 });
  assert.equal(digestIntro({ messages: 14, proposals: new Array(3) }),
    "Собрано 14 сообщений. Агент сгруппировал их и предлагает 3 изменения. По каждому — вопрос анкеты и решение руководителя.");
  assert.equal(digestIntro({ messages: 1, proposals: new Array(1) }),
    "Собрано 1 сообщение. Агент сгруппировал их и предлагает 1 изменение. По каждому — вопрос анкеты и решение руководителя.");
  assert.equal(digestIntro({ messages: 5, proposals: [] }), null);
});
