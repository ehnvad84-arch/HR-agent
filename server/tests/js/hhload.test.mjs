// «Загрузить резюме» (S44): окно «сколько» — остаток просмотров hh и пакета, число от 1 до остатка
import assert from "node:assert/strict";
import test from "node:test";

import {
  AREA_DEBOUNCE_MS, DAILY_LIMIT, EXPLAIN, FALLBACK_AREAS, ROLE_EXPLAIN, STARTED_TOAST, areaForGeo, areaLine, canSearchAreas, canStart,
  collectBody, countError, defaultCount, exhaustedText, limitsText, moveHighlight, normalizeAreas, normalizeLimits, parseCount,
  roleSheetTitle,
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

// ------------------------------------------------------------------ по должности и региону (S46)

test("S46: регион по умолчанию — из портрета (geo)", () => {
  assert.deepEqual(areaForGeo("krasnodar"), { id: "53", name: "Краснодар" });
  assert.deepEqual(areaForGeo("remote_ru"), { id: "113", name: "Вся Россия" });
  assert.deepEqual(areaForGeo("relocation_ru"), { id: "1438", name: "Краснодарский край" });
  assert.deepEqual(areaForGeo("что-то новое"), { id: "53", name: "Краснодар" });
  assert.deepEqual(areaForGeo(undefined), { id: "53", name: "Краснодар" });
});

test("S46: быстрый выбор региона — вся Россия, Краснодарский край, Краснодар", () => {
  assert.deepEqual(FALLBACK_AREAS, [{ id: "113", name: "Вся Россия" }, { id: "1438", name: "Краснодарский край" }, { id: "53", name: "Краснодар" }]);
  assert.deepEqual(normalizeAreas({ items: [{ id: "113", name: "Вся Россия" }, { id: 53, name: "Краснодар" }] }),
    [{ id: "113", name: "Вся Россия" }, { id: "53", name: "Краснодар" }]);
  assert.deepEqual(normalizeAreas({ items: [{ id: "x", name: "Сломано" }, { id: "1", name: "" }, { id: "2" }, null, { id: "3", name: "Сочи" }, { id: "3", name: "Сочи" }] }),
    [{ id: "3", name: "Сочи" }]);
  assert.deepEqual(normalizeAreas(null), []);
  assert.deepEqual(normalizeAreas({}), []);
  assert.equal(normalizeAreas({ items: Array.from({ length: 15 }, (_, i) => ({ id: String(i + 1), name: `Город ${i + 1}` })) }).length, 10);
});

test("S46: поиск региона — от 2 букв, через 300 мс после ввода", () => {
  assert.equal(AREA_DEBOUNCE_MS, 300);
  assert.equal(canSearchAreas("Со"), true);
  assert.equal(canSearchAreas("  Со "), true);
  assert.equal(canSearchAreas("С"), false);
  assert.equal(canSearchAreas("  "), false);
  assert.equal(canSearchAreas(null), false);
});

test("S46: клавиши в списке подсказок региона — вверх, вниз, без выхода за края", () => {
  assert.equal(moveHighlight(-1, 1, 3), 0);
  assert.equal(moveHighlight(0, 1, 3), 1);
  assert.equal(moveHighlight(2, 1, 3), 2);
  assert.equal(moveHighlight(1, -1, 3), 0);
  assert.equal(moveHighlight(0, -1, 3), 0);
  assert.equal(moveHighlight(-1, -1, 3), 0);
  assert.equal(moveHighlight(0, 1, 0), -1);
});

test("S46: тексты окна по должности", () => {
  assert.equal(roleSheetTitle("Ведущий инженер-электронщик"), "Загрузить резюме HH.ru: Ведущий инженер-электронщик");
  assert.equal(ROLE_EXPLAIN, "Поиск только по этой должности в выбранном регионе. Балл считается по всем портретам.");
  assert.equal(areaLine({ id: "53", name: "Краснодар" }), "Регион: Краснодар");
});

test("S46: запрос на сервер — число, должность и регион; без должности только число", () => {
  assert.deepEqual(collectBody({ count: "150", profileId: 5, areaId: "53" }), { count: 150, role_profile_id: 5, area_id: "53" });
  assert.deepEqual(collectBody({ count: " 40 ", profileId: 5, areaId: 1438 }), { count: 40, role_profile_id: 5, area_id: "1438" });
  assert.deepEqual(collectBody({ count: "150" }), { count: 150 });
  assert.deepEqual(collectBody({ count: "150", profileId: null, areaId: null }), { count: 150 });
});
