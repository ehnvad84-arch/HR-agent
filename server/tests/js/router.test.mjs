// Адрес экрана в хэше: F5 и «Назад» возвращают на тот же экран (S1)
import assert from "node:assert/strict";
import test from "node:test";

import { TAB_ORDER, hashFor, parseHash } from "../../web/js/router.js";
import { SCREENS } from "../../web/js/screens/index.js";

test("вкладки идут в порядке прототипа", () => {
  assert.deepEqual(TAB_ORDER, ["today", "base", "vacancies", "profiles", "learn", "logs", "funnel", "arch"]);   // «Досье» — без вкладки
});

test("простые вкладки", () => {
  for (const name of TAB_ORDER) {
    const r = parseHash(`#/${name === "funnel" ? "funnel" : name}`);
    assert.equal(r.name, name);
    assert.deepEqual(r.params, {});
  }
});

test("досье по номеру и без него", () => {
  assert.deepEqual(parseHash("#/person/1042"), { name: "person", params: { id: "1042" }, query: {} });
  assert.deepEqual(parseHash("#/person"), { name: "person", params: {}, query: {} });
});

test("воронка вакансии", () => {
  assert.deepEqual(parseHash("#/vacancy/7/funnel"), { name: "funnel", params: { id: "7" }, query: {} });
});

test("запрос в хэше: фильтры базы", () => {
  const r = parseHash("#/base?vacancy=3&q=%D0%B0+%D0%B1");
  assert.equal(r.name, "base");
  assert.deepEqual(r.query, { vacancy: "3", q: "а б" });
});

test("мусор и чужие адреса не открывают экран", () => {
  for (const bad of ["", "#", "#/", "#/nope", "#/person/../../x", "#/person/1/2", "#/vacancy/7", "#/person/<script>", "#//evil.example"]) {
    assert.equal(parseHash(bad), null, bad);
  }
});

test("hashFor собирает адрес обратно", () => {
  assert.equal(hashFor("today"), "#/today");
  assert.equal(hashFor("person", { id: 5 }), "#/person/5");
  assert.equal(hashFor("person"), "#/person");
  assert.equal(hashFor("funnel", { id: 7 }), "#/vacancy/7/funnel");
  assert.equal(hashFor("funnel"), "#/funnel");
  assert.equal(hashFor("base", {}, { vacancy: 3, q: "", dept: "ОТК" }), "#/base?vacancy=3&dept=%D0%9E%D0%A2%D0%9A");
});

test("hashFor и parseHash обратимы", () => {
  for (const [name, params, query] of [["person", { id: "9" }, {}], ["funnel", { id: "2" }, {}], ["base", {}, { vacancy: "4" }]]) {
    assert.deepEqual(parseHash(hashFor(name, params, query)), { name, params, query });
  }
});

test("у каждой вкладки есть экран с названием, правом и render()", () => {
  assert.deepEqual(Object.keys(SCREENS).sort(), [...TAB_ORDER, "access", "person"].sort());   // «Доступ» — по праву; досье — экран без вкладки
  for (const [name, screen] of Object.entries(SCREENS)) {
    assert.ok(screen.title, name);
    assert.ok(screen.perm, name);
    assert.equal(typeof screen.render, "function", name);
  }
});
