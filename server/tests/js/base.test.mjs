// «База»: состояние в адресе, запрос к API, сортировка и страницы (S19, S20, S22)
import assert from "node:assert/strict";
import test from "node:test";

import { hashFor, parseHash } from "../../web/js/router.js";
import {
  COLUMNS, apiRequest, ariaSort, canShowFit, columnTitle, filtersActive, fitAvailability, fitBarText, fitEmptyText, fitQuery, fitRoleTitle, nextSort, pageInfo, parseState,
  similarityText, sourceTitle, stateToQuery, whereTitle,
} from "../../web/js/screens/base-model.js";

test("колонки таблицы — как в прототипе, у каждой своё имя сортировки для API", () => {
  assert.deepEqual(COLUMNS.map((c) => c.title), [
    "ФИО", "Город", "Должность", "Подразделение", "Балл", "Где числится", "Источник", "Профилей в сети", "Последнее изменение"]);
  assert.deepEqual(COLUMNS.map((c) => c.sort), ["name", "city", "role", "dept", "score", "where", "source", "socn", "upd"]);
});

test("пустой адрес: вся база, по убыванию балла, первая страница", () => {
  assert.deepEqual(parseState({}), {
    dept: "", city: "", role: "", score_min: "", where: "", source: "", sort: "score", dir: "desc", page: 0, q: "", vacancy: "", fit: "" });
});

test("адрес читается в состояние: фильтры, сортировка, страница, поиск", () => {
  const state = parseState({ city: "Краснодар", dept: "sales", score_min: "70", sort: "city", dir: "asc", page: "2", q: "схемотехник Altium" });
  assert.equal(state.city, "Краснодар");
  assert.equal(state.dept, "sales");
  assert.equal(state.score_min, "70");
  assert.equal(state.sort, "city");
  assert.equal(state.dir, "asc");
  assert.equal(state.page, 2);
  assert.equal(state.q, "схемотехник Altium");
});

test("мусор в адресе не доходит до API: чужая колонка, направление, страница, балл", () => {
  const state = parseState({ sort: "drop table", dir: "up", page: "-3", score_min: "abc", where: "x" });
  assert.equal(state.sort, "score");
  assert.equal(state.dir, "desc");
  assert.equal(state.page, 0);
  assert.equal(state.score_min, "");
  assert.equal(state.where, "");   // «где числится» — только известные значения
  assert.equal(parseState({ page: "7.5" }).page, 0);
  assert.equal(parseState({ vacancy: "12" }).vacancy, "12");
  assert.equal(parseState({ vacancy: "abc" }).vacancy, "");
});

test("в адрес попадает только изменённое: умолчания не пишутся", () => {
  assert.deepEqual(stateToQuery(parseState({})), {});
  assert.deepEqual(stateToQuery(parseState({ city: "Томск", sort: "score", dir: "desc", page: "0" })), { city: "Томск" });
  assert.deepEqual(stateToQuery(parseState({ sort: "name", dir: "asc", page: "1" })), { sort: "name", dir: "asc", page: 1 });
});

test("адрес круглый: состояние → ссылка → то же состояние (F5 и копирование ссылки)", () => {
  const original = parseState({ city: "Ростов-на-Дону", role: "sales_project", score_min: "65", where: "candidate",
    source: "hh", sort: "upd", dir: "desc", page: "1", q: "a & b ?" });
  const link = hashFor("base", {}, stateToQuery(original));
  assert.ok(link.startsWith("#/base?"));
  assert.deepEqual(parseState(parseHash(link).query), original);
});

test("S19: запрос к /api/base — фильтры, сортировка, страница; пустые значения не уходят", () => {
  const request = apiRequest(parseState({ city: "Краснодар", score_min: "70", sort: "city", dir: "asc", page: "1" }));
  assert.equal(request.path, "/api/base");
  assert.deepEqual(request.query, { city: "Краснодар", score_min: "70", sort: "city", dir: "asc", page: 1 });
  const whole = apiRequest(parseState({}));
  assert.deepEqual(whole.query, { sort: "score", dir: "desc", page: 0 });
});

test("S22: «подходящие» — запрос к вакансии, только город, балл, сортировка и страница", () => {
  const request = apiRequest(parseState({ vacancy: "7", city: "Томск", dept: "sales", where: "candidate", sort: "name", dir: "asc" }));
  assert.equal(request.path, "/api/vacancy/7/matching");
  assert.deepEqual(request.query, { city: "Томск", sort: "name", dir: "asc", page: 0 });   // остальные фильтры сервер для вакансии не знает
});

test("S20: поиск по смыслу — отдельный запрос с текстом", () => {
  const request = apiRequest(parseState({ q: "  схемотехник Altium  ", city: "Томск" }));
  assert.equal(request.path, "/api/search");
  assert.deepEqual(request.query, { q: "схемотехник Altium" });
  assert.equal(request.kind, "search");
  assert.equal(apiRequest(parseState({})).kind, "table");
  assert.equal(apiRequest(parseState({ vacancy: "3" })).kind, "table");
});

test("сортировка по клику: новая колонка — числа вниз, текст вверх; повторный клик — наоборот; страница с нуля", () => {
  const start = parseState({ page: "3" });
  assert.deepEqual(nextSort(start, "city"), { sort: "city", dir: "asc", page: 0 });
  assert.deepEqual(nextSort(start, "socn"), { sort: "socn", dir: "desc", page: 0 });
  assert.deepEqual(nextSort(start, "upd"), { sort: "upd", dir: "desc", page: 0 });   // свежие сверху
  assert.deepEqual(nextSort({ ...start, sort: "city", dir: "asc" }, "city"), { sort: "city", dir: "desc", page: 0 });
  assert.deepEqual(nextSort({ ...start, sort: "score", dir: "desc" }, "score"), { sort: "score", dir: "asc", page: 0 });
});

test("aria-sort у заголовков", () => {
  const state = parseState({ sort: "city", dir: "asc" });
  assert.equal(ariaSort(state, "city"), "ascending");
  assert.equal(ariaSort({ ...state, dir: "desc" }, "city"), "descending");
  assert.equal(ariaSort(state, "score"), "none");
});

test("фильтры включены — показываем «Сбросить» и «Ничего не нашлось»", () => {
  assert.equal(filtersActive(parseState({})), false);
  assert.equal(filtersActive(parseState({ sort: "city", dir: "asc" })), false);   // сортировка — не фильтр
  assert.equal(filtersActive(parseState({ city: "Томск" })), true);
  assert.equal(filtersActive(parseState({ score_min: "50" })), true);
  assert.equal(filtersActive(parseState({ where: "watched" })), true);
});

test("страницы: «Показано 50 из 55», доступность «Назад» и «Далее»", () => {
  assert.deepEqual(pageInfo({ total: 55, page: 0, page_size: 50, items: new Array(50) }),
    { shown: 50, total: 55, text: "Показано 50 из 55", page: 0, pages: 2, hasPrev: false, hasNext: true });
  assert.deepEqual(pageInfo({ total: 55, page: 1, page_size: 50, items: new Array(5) }),
    { shown: 5, total: 55, text: "Показано 5 из 55", page: 1, pages: 2, hasPrev: true, hasNext: false });
  assert.deepEqual(pageInfo({ total: 0, page: 0, page_size: 50, items: [] }),
    { shown: 0, total: 0, text: "Показано 0 из 0", page: 0, pages: 1, hasPrev: false, hasNext: false });
  assert.equal(pageInfo({ total: 120, page: 0, page_size: 0, items: [] }).pages, 1);   // размер страницы не пришёл
});

test("«Где числится» и источник — русскими словами", () => {
  assert.equal(whereTitle("observed"), "наблюдение");
  assert.equal(whereTitle("watched"), "ведётся особо");
  assert.equal(whereTitle("dropped"), "категорически нет");
  assert.equal(whereTitle("candidate"), "кандидат");
  assert.equal(whereTitle("zzz"), "zzz");
  assert.equal(sourceTitle("hh"), "hh.ru");
  assert.equal(sourceTitle("habr"), "Хабр Карьера");
  assert.equal(sourceTitle("новый"), "новый");
});

test("близость поиска — проценты", () => {
  assert.equal(similarityText(0.1807), "близость 18 %");
  assert.equal(similarityText(0.995), "близость 100 %");
  assert.equal(similarityText(null), "");
});

// ------------------------------------------------------------------ подходящие под одну должность (S49)

test("S49: адрес «подходящие под должность» — код портрета, сортировка по баллу по убыванию", () => {
  assert.deepEqual(fitQuery("hw_lead"), { fit: "hw_lead", sort: "score", dir: "desc" });
  assert.equal(hashFor("base", {}, fitQuery("hw_lead")), "#/base?fit=hw_lead&sort=score&dir=desc");
  const state = parseState(parseHash(hashFor("base", {}, fitQuery("hw_lead"))).query);
  assert.equal(state.fit, "hw_lead");
  assert.equal(state.sort, "score");
  assert.equal(state.dir, "desc");
});

test("S49: fit живёт в адресе вместе с остальным состоянием; мусор отбрасывается", () => {
  const original = parseState({ fit: "sales_project", city: "Томск", where: "candidate", sort: "name", dir: "asc", page: "2" });
  const link = hashFor("base", {}, stateToQuery(original));
  assert.deepEqual(parseState(parseHash(link).query), original);
  assert.deepEqual(stateToQuery(parseState({ fit: "hw_lead" })), { fit: "hw_lead" });   // сортировка по умолчанию и так по баллу
  assert.equal(parseState({ fit: "../etc" }).fit, "");
  assert.equal(parseState({ fit: "a b" }).fit, "");
  assert.equal(parseState({ fit: "x".repeat(80) }).fit, "");
  assert.equal(parseState({}).fit, "");
});

test("S49: запрос к /api/base с fit — остальные фильтры поверх, «Должность» не уходит", () => {
  const request = apiRequest(parseState({ fit: "hw_lead", role: "sales_project", city: "Краснодар", score_min: "60", where: "candidate", dept: "rnd_hw", sort: "score", dir: "desc", page: "1" }));
  assert.equal(request.path, "/api/base");
  assert.equal(request.kind, "table");
  assert.deepEqual(request.query, { fit: "hw_lead", dept: "rnd_hw", city: "Краснодар", score_min: "60", where: "candidate", sort: "score", dir: "desc", page: 1 });
  assert.equal("role" in request.query, false);
  assert.deepEqual(apiRequest(parseState({ fit: "hw_lead" })).query, { fit: "hw_lead", sort: "score", dir: "desc", page: 0 });
  assert.equal("fit" in apiRequest(parseState({})).query, false);
  assert.equal(apiRequest(parseState({ fit: "hw_lead", vacancy: "7" })).path, "/api/vacancy/7/matching");   // вакансия главнее
  assert.equal(apiRequest(parseState({ fit: "hw_lead", q: "схемотехник" })).kind, "search");
});

test("S49: колонка балла называется «Балл по должности», пока выбрана должность", () => {
  const score = COLUMNS.find((c) => c.key === "score");
  assert.equal(columnTitle(score, parseState({})), "Балл");
  assert.equal(columnTitle(score, parseState({ fit: "hw_lead" })), "Балл по должности");
  assert.equal(columnTitle(COLUMNS[0], parseState({ fit: "hw_lead" })), "ФИО");
});

test("S49: плашка «Подходящие под «должность»»: название из профилей, иначе из первой строки, иначе код", () => {
  const profiles = [{ code: "hw_lead", title: "Ведущий инженер-электронщик" }];
  assert.equal(fitRoleTitle("hw_lead", profiles, []), "Ведущий инженер-электронщик");
  assert.equal(fitRoleTitle("sales_project", profiles, [{ role_code: "sales_project", role_title: "Менеджер проектных продаж" }]), "Менеджер проектных продаж");
  assert.equal(fitRoleTitle("sales_project", [], [{ role_code: "other", role_title: "Другая" }]), "sales_project");
  assert.equal(fitRoleTitle("sales_project", null, null), "sales_project");
  assert.equal(fitBarText("Менеджер проектных продаж"), "Подходящие под «Менеджер проектных продаж»: по баллу этой должности, лучшие сверху");
});

test("S49: пусто — подсказка, как набрать резюме (кнопка загрузки — только тем, у кого она есть)", () => {
  assert.equal(fitEmptyText(true), "Подходящих под эту должность пока нет — загрузите резюме кнопкой «Загрузить резюме HH.ru»");
  assert.equal(fitEmptyText(false), "Подходящих под эту должность пока нет");
});

test("S49: «Подходящие кандидаты» видна у каждой должности, рабочая — только с действующим портретом", () => {
  assert.equal(canShowFit("approved"), true);
  assert.equal(canShowFit("pending"), false);
  assert.equal(canShowFit("draft"), false);
  assert.equal(canShowFit("empty"), false);
  assert.equal(canShowFit(undefined), false);
  assert.equal(canShowFit(null), false);
  assert.deepEqual(fitAvailability("approved"), { active: true, tip: "fit-button" });
  // у пустого, ждущего утверждения и черновика баллов по должности ещё нет: кнопка видна, но неактивна
  for (const state of ["empty", "pending", "draft"]) assert.deepEqual(fitAvailability(state), { active: false, tip: "fit-button-no-scores" }, state);
  // портрет к должности не привязан
  assert.deepEqual(fitAvailability(null), { active: false, tip: "fit-button-no-profile" });
  assert.deepEqual(fitAvailability(undefined), { active: false, tip: "fit-button-no-profile" });
});
