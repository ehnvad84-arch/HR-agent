// Кабинет руководителя: «Сегодня», «Мои вакансии», страница вакансии, «Мои должности» (S5–S8)
import assert from "node:assert/strict";
import test from "node:test";

import {
  EMPTY_SIGNALS, EMPTY_VACANCIES, NO_HANDOFFS, customerLine, hrLine, managerBadge, matchingLabel, myPositionsNote, normalizeManagerToday, positionStatus, saveLabel,
  vacancyMeta, zoneTitles,
} from "../../web/js/screens/manager-model.js";

test("S6: «Сегодня» руководителя — сигналы, вакансии, передачи; чего нет в ответе — пустые списки", () => {
  const data = normalizeManagerToday({ signals: [{ person_id: 1 }, { person_id: 2 }], vacancies: [{ id: 3 }], handoffs: [{ id: 4 }] });
  assert.equal(data.signals.length, 2);
  assert.equal(data.vacancies.length, 1);
  assert.equal(data.handoffs.length, 1);
  assert.equal(data.badge, 2);   // число на вкладке — новые люди
  const empty = normalizeManagerToday({});
  assert.deepEqual([empty.signals, empty.vacancies, empty.handoffs], [[], [], []]);
  assert.equal(empty.badge, 0);
  assert.equal(managerBadge(0), "");
  assert.equal(managerBadge(5), 5);
});

test("S6: заголовки блоков и пустые состояния — как в спеке", () => {
  assert.deepEqual(zoneTitles(), { signals: "Новые и изменившиеся — по вашим должностям", vacancies: "Открытые вакансии вашего подразделения", handoffs: "Кого вы передали" });
  assert.equal(EMPTY_SIGNALS, "Новых людей по вашим должностям нет");
  assert.equal(EMPTY_VACANCIES, "Открытых вакансий нет. Вакансию заводит отдел персонала");
  assert.equal(NO_HANDOFFS, "Вы пока никого не передавали");
});

test("S6, S7: вакансия — ставки, срок, ответственный HR, заказчик, число подходящих", () => {
  const now = new Date(2026, 9, 30);
  assert.equal(vacancyMeta({ headcount: 3, target_date: "2026-11-15" }, now), "Ставок: 3 · срок до 15 ноября");
  assert.equal(vacancyMeta({ headcount: 1, target_date: null }, now), "Ставок: 1");
  assert.equal(vacancyMeta({ headcount: 2, target_date: "2027-01-10" }, now), "Ставок: 2 · срок до 10 января 2027");
  assert.equal(vacancyMeta({}, now), "");
  assert.equal(hrLine({ recruiter: "Петрова А." }), "Ответственный: Петрова А.");
  assert.equal(hrLine({ recruiter: null }), "Ответственный не назначен");
  assert.equal(customerLine({ hiring_manager: "Сидоров С. П." }), "Заказчик: Сидоров С. П.");
  assert.equal(customerLine({}), "");
  assert.equal(matchingLabel(12), "Подходящих: 12");
  assert.equal(matchingLabel(0), "Подходящих: 0");
  assert.equal(matchingLabel(undefined), "Подходящих: 0");
});

test("S8: «Мои должности» — «Отправить на утверждение» вместо «Сохранить как новую версию»; «Утвердить» нет", () => {
  assert.equal(saveLabel(true), "Отправить на утверждение");
  assert.equal(saveLabel(false), "Сохранить как новую версию");
});

test("S8: статусы портрета в «Моих должностях»", () => {
  assert.deepEqual(positionStatus({ state: "approved" }), { text: "утверждён", tone: "g" });
  assert.deepEqual(positionStatus({ state: "pending", author: "Сидоров С. П.", at: new Date(2026, 9, 30).toISOString() }, new Date(2026, 9, 31)),
    { text: "ждёт утверждения — правка Сидорова С. П. от 30.10", tone: "o" });
  assert.deepEqual(positionStatus({ state: "pending" }), { text: "ждёт утверждения", tone: "o" });
  assert.deepEqual(positionStatus({ state: "empty" }), { text: "критерии не заполнены", tone: "o" });
  assert.deepEqual(positionStatus({ state: "draft" }), { text: "черновик", tone: "" });
});

test("S8: подсказки у формы руководителя", () => {
  assert.equal(myPositionsNote(true), "Должность есть и в других подразделениях: правка меняет общий портрет");
  assert.equal(myPositionsNote(false), "");
});
