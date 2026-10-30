// «Загрузить резюме» (S44): окно «сколько» — остаток просмотров hh и пакета, число от 1 до остатка
import assert from "node:assert/strict";
import test from "node:test";

import {
  DAILY_LIMIT, EXPECT_MS, EXPLAIN, STARTED_TOAST, canStart, countError, defaultCount, exhaustedText, expectCollect, limitsText, normalizeLimits,
  parseCount, takeExpectation,
} from "../../web/js/hh-load-model.js";

test("остаток из ответа /api/hh/limits: число просмотров и пакет", () => {
  assert.deepEqual(normalizeLimits({ views_left: 311, package_left: 23000 }), { left: 311, packageLeft: 23000 });
  assert.deepEqual(normalizeLimits({ views_left: 0, package_left: null }), { left: 0, packageLeft: null });
  assert.deepEqual(normalizeLimits({ views_left: -4, package_left: "много" }), { left: 0, packageLeft: null });
  assert.deepEqual(normalizeLimits({ views_left: 12.9 }), { left: 12, packageLeft: null });
  assert.deepEqual(normalizeLimits(null), { left: 0, packageLeft: null });
  assert.deepEqual(normalizeLimits({}), { left: 0, packageLeft: null });
});

test("S44: текст остатка — просмотры и пакет, с правильным числом слов", () => {
  assert.equal(limitsText({ left: 311, packageLeft: 23000 }),
    "Сегодня можно загрузить ещё 311 полных резюме (дневной лимит просмотров hh). Пакет API: 23 000");
  assert.equal(limitsText({ left: 1, packageLeft: 5 }), "Сегодня можно загрузить ещё 1 полное резюме (дневной лимит просмотров hh). Пакет API: 5");
  assert.equal(limitsText({ left: 21, packageLeft: 5 }).includes("21 полное резюме"), true);
  assert.equal(limitsText({ left: 3, packageLeft: 5 }).includes("3 полных резюме"), true);
  assert.equal(limitsText({ left: 500, packageLeft: null }),
    "Сегодня можно загрузить ещё 500 полных резюме (дневной лимит просмотров hh). Пакет API: нет данных");
});

test("S44: лимит исчерпан — текст без поля", () => {
  assert.equal(DAILY_LIMIT, 500);
  assert.equal(exhaustedText(), "Сегодня лимит исчерпан, завтра снова 500");
});

test("пояснение в окне — как просил заказчик", () => {
  assert.equal(EXPLAIN, "Поиск бесплатный; полное резюме тратит один просмотр из дневного лимита hh — тот же лимит, что на сайте. "
    + "Контакты не открываются. Каждый день в 18:00 по Москве остаток выбирается автоматически.");
  assert.equal(STARTED_TOAST, "Загрузка запущена");
});

test("число из поля: только целое без знака и дроби", () => {
  assert.equal(parseCount("5"), 5);
  assert.equal(parseCount("  12 "), 12);
  assert.equal(parseCount("007"), 7);
  assert.equal(parseCount("0"), 0);
  for (const bad of ["", "   ", "1.5", "1,5", "-3", "+3", "1e3", "абв", "12 штук", "1 000", null, undefined]) assert.equal(parseCount(bad), null, String(bad));
});

test("S44: число вне 1…остаток не отправляется", () => {
  assert.equal(countError("1", 311), "");
  assert.equal(countError("311", 311), "");
  assert.equal(countError("150", 311), "");
  const message = "Введите целое число от 1 до 311";
  assert.equal(countError("0", 311), message);
  assert.equal(countError("312", 311), message);
  assert.equal(countError("", 311), message);
  assert.equal(countError("1.5", 311), message);
  assert.equal(countError("-1", 311), message);
  assert.equal(countError("пять", 311), message);
  assert.equal(canStart("311", 311), true);
  assert.equal(canStart("312", 311), false);
  assert.equal(canStart("0", 311), false);
  assert.equal(canStart("5", 0), false);   // остатка нет — отправлять нечего
});

test("по умолчанию в поле весь остаток", () => {
  assert.equal(defaultCount(311), "311");
  assert.equal(defaultCount(0), "");
});

test("после запуска «Схема» ждёт новый прогон collect_hh ограниченное время", () => {
  assert.equal(EXPECT_MS, 20000);
  assert.equal(takeExpectation(1000), null);
  expectCollect(1000);
  assert.deepEqual(takeExpectation(5000), { job: "collect_hh", until: 21000 });
  assert.equal(takeExpectation(5001), null);   // ожидание забирается один раз
  expectCollect(1000);
  assert.equal(takeExpectation(1000 + EXPECT_MS + 1), null);   // устарело
});
