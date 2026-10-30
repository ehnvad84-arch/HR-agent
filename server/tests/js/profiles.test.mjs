// «База должностей»: список, форма правки, изменения для новой версии, предпросмотр (S25–S28, S39)
import assert from "node:assert/strict";
import test from "node:test";

import { hashFor, parseHash } from "../../web/js/router.js";
import {
  EXTRA_FEATURES, FEATURES, FEATURE_FIELDS, GEO_OPTIONS, changesFor, conditionLines, conditionsFromLines, defaultWeights, docError, formFromProfile, groupByDept,
  extraFeatures, featureLabel, parseMoney, previewText, roleTitle, rowSub, splitChips, stateInfo, stepWeight, validateForm, weightShare, weightSum,
  weightSumText,
} from "../../web/js/screens/profiles-model.js";

const PROFILE = {
  id: 5, code: "hw_lead", title: "Ведущий инженер-электронщик", dept_code: "rnd_hw", dept: "Инженерные разработки", version: "1.1",
  state: "approved", geo: "krasnodar", salary_min: 150000, salary_max: 200000,
  keywords: ["Altium", "OrCAD"], donor_companies: ["Болид"],
  must_have: [{ text: "Профильный опыт от 3 лет", feature: "exp_years", op: ">=", value: 3 }, { text: "Схемотехника" }],
  nice_to_have: [{ text: "ЭМС" }], stop_factors: [], weights: { exp_years: 20, keywords: 20, title_match: 15, donor_company: 10, tenure: 15, geo: 10, salary: 10, open_code: 0 },
};

test("признаки весов и подписи — как в задаче", () => {
  assert.deepEqual(FEATURES.map(([key]) => key), ["exp_years", "keywords", "title_match", "donor_company", "tenure", "geo", "salary", "open_code"]);
  assert.deepEqual(FEATURES.map(([, label]) => label), ["Опыт по профилю", "Ключевые слова и инструменты", "Совпадение должности",
    "Опыт в компании-доноре", "Держится на месте", "География", "Ожидания против вилки", "Открытый код"]);
  assert.deepEqual(GEO_OPTIONS.map(([code]) => code), ["krasnodar", "remote_ru", "relocation_ru"]);
});

test("состояния версии: «действует», «ждёт утверждения», «черновик»", () => {
  assert.deepEqual(stateInfo("approved"), { text: "действует", pill: "cand" });
  assert.deepEqual(stateInfo("pending"), { text: "ждёт утверждения", pill: "watch" });
  assert.deepEqual(stateInfo("draft"), { text: "черновик", pill: "obs" });
  assert.deepEqual(stateInfo("zzz"), { text: "zzz", pill: "obs" });
});

test("строка списка: версия, дата утверждения и состояние", () => {
  assert.equal(rowSub({ dept: "Инженерные разработки", version: "1.1", state: "approved", approved_at: "2026-09-20T10:00:00+00:00" }),
    "Инженерные разработки · в. 1.1 от 20.09, утверждена");
  assert.equal(rowSub({ dept: "Разработка ПО", version: "1.2", state: "pending", approved_at: null }), "Разработка ПО · в. 1.2, ждёт утверждения");
  assert.equal(rowSub({ dept: "Продажи", version: "0.1", state: "draft", approved_at: null }), "Продажи · черновик из ДИ, не утверждён");
});

test("список группируется по подразделению, внутри — по названию", () => {
  const groups = groupByDept([
    { id: 1, title: "Б", dept: "Продажи" }, { id: 2, title: "А", dept: "Продажи" }, { id: 3, title: "Я", dept: "ОТК" }]);
  assert.deepEqual(groups.map((g) => [g.dept, g.items.map((i) => i.id)]), [["ОТК", [3]], ["Продажи", [2, 1]]]);
  assert.deepEqual(groupByDept([]), []);
});

test("ввод чипсов: запятая, точка с запятой и перенос строки; пустое и повторы уходят", () => {
  assert.deepEqual(splitChips("Altium, OrCAD;\nKiCad ,, altium"), ["Altium", "OrCAD", "KiCad"]);
  assert.deepEqual(splitChips("   "), []);
});

test("условия правятся строками; неизменённая строка сохраняет разбор сервера", () => {
  assert.deepEqual(conditionLines(PROFILE.must_have), ["Профильный опыт от 3 лет", "Схемотехника"]);
  const kept = conditionsFromLines("Профильный опыт от 3 лет\n\n  Новое условие  ", PROFILE.must_have);
  assert.deepEqual(kept, [PROFILE.must_have[0], { text: "Новое условие" }]);
  assert.deepEqual(conditionsFromLines("", PROFILE.must_have), []);
});

test("веса: из профиля или стандартные по подразделению, сумма не обязана быть 100", () => {
  assert.deepEqual(defaultWeights("sales"), { exp_years: 20, keywords: 25, title_match: 15, donor_company: 10, tenure: 10, geo: 10, salary: 10, open_code: 0 });
  assert.equal(defaultWeights("rnd_hw").open_code, 10);
  assert.equal(defaultWeights("rnd_sw").open_code, 10);
  const form = formFromProfile(PROFILE);
  assert.equal(form.usingDefaultWeights, false);
  assert.equal(weightSum(form.weights), 100);
  const empty = formFromProfile({ ...PROFILE, weights: {} });
  assert.equal(empty.usingDefaultWeights, true);
  assert.deepEqual(empty.weights, defaultWeights("rnd_hw"));
  assert.equal(weightSum({ exp_years: 30, keywords: "", geo: "15" }), 45);   // пустое — ноль
});

test("форма из профиля: поля как есть, вилка текстом", () => {
  const form = formFromProfile(PROFILE);
  assert.equal(form.title, "Ведущий инженер-электронщик");
  assert.equal(form.geo, "krasnodar");
  assert.equal(form.salary_min, "150000");
  assert.equal(form.salary_max, "200000");
  assert.deepEqual(form.keywords, ["Altium", "OrCAD"]);
  assert.equal(form.must_have, "Профильный опыт от 3 лет\nСхемотехника");
  assert.equal(formFromProfile({ ...PROFILE, salary_min: null, salary_max: null }).salary_min, "");
});

test("вилка: число или пусто; всё остальное — ошибка", () => {
  assert.equal(parseMoney("150 000"), 150000);
  assert.equal(parseMoney(""), null);
  assert.equal(parseMoney("  "), null);
  assert.ok(Number.isNaN(parseMoney("много")));
  assert.ok(Number.isNaN(parseMoney("-5")));
});

test("S26: в запрос уходят только изменённые поля", () => {
  const form = formFromProfile(PROFILE);
  assert.deepEqual(changesFor(PROFILE, form), {});
  form.title = "Ведущий инженер-электронщик (РЭА)";
  form.keywords = ["Altium", "OrCAD", "KiCad"];
  form.salary_max = "220000";
  form.geo = "relocation_ru";
  form.must_have = "Профильный опыт от 3 лет\nСхемотехника\nВывод изделия в серию";
  form.weights = { ...form.weights, keywords: 25, exp_years: 15 };
  const changes = changesFor(PROFILE, form);
  assert.deepEqual(Object.keys(changes).sort(), ["geo", "keywords", "must_have", "salary_max", "title", "weights"]);
  assert.equal(changes.salary_max, 220000);
  assert.deepEqual(changes.keywords, ["Altium", "OrCAD", "KiCad"]);
  assert.deepEqual(changes.must_have, [PROFILE.must_have[0], PROFILE.must_have[1], { text: "Вывод изделия в серию" }]);
  assert.deepEqual(changes.weights, { exp_years: 15, keywords: 25, title_match: 15, donor_company: 10, tenure: 15, geo: 10, salary: 10, open_code: 0 });
});

test("стандартные веса не отправляются, пока человек их не менял", () => {
  const empty = { ...PROFILE, weights: {} };
  const form = formFromProfile(empty);
  assert.deepEqual(changesFor(empty, form), {});
  form.weights = { ...form.weights, geo: 20 };
  assert.equal(changesFor(empty, form).weights.geo, 20);
});

test("вилку можно очистить", () => {
  const form = formFromProfile(PROFILE);
  form.salary_min = "";
  assert.deepEqual(changesFor(PROFILE, form), { salary_min: null });
});

test("проверка формы до запроса", () => {
  const ok = formFromProfile(PROFILE);
  assert.equal(validateForm(ok), null);
  assert.equal(validateForm({ ...ok, title: "  " }), "Введите название должности");
  assert.equal(validateForm({ ...ok, salary_min: "abc" }), "Вилка: только целое число рублей");
  assert.equal(validateForm({ ...ok, salary_min: "300000", salary_max: "200000" }), "Вилка: «от» больше «до»");
  assert.equal(validateForm({ ...ok, weights: { ...ok.weights, geo: 120 } }), "Веса: целые числа от 0 до 100");
  assert.equal(validateForm({ ...ok, weights: { ...ok.weights, geo: "1.5" } }), "Веса: целые числа от 0 до 100");
});

test("S26: предпросмотр — сколько проходит сейчас и по новой версии", () => {
  assert.equal(previewText({ passes_now: 28, passes_new: 35, checked: 199, threshold: 60 }),
    "Сейчас порог 60 проходят 28 человек, по новой версии — 35 (проверено 199)");
  assert.equal(previewText({ passes_now: 1, passes_new: 1, checked: 3, threshold: 60 }),
    "Сейчас порог 60 проходит 1 человек, по новой версии — 1 (проверено 3)");
});

test("S39: роли по-русски", () => {
  assert.equal(roleTitle("head_recruiter"), "Руководитель отдела персонала");
  assert.equal(roleTitle("recruiter"), "Рекрутер");
  assert.equal(roleTitle("hiring_manager"), "Нанимающий руководитель");
  assert.equal(roleTitle("director"), "Директор");
  assert.equal(roleTitle("новая"), "новая");
});

test("S28: ДИ — только .docx или .pdf", () => {
  assert.equal(docError({ name: "Инструкция.docx", size: 1000 }), null);
  assert.equal(docError({ name: "ДИ.PDF", size: 1000 }), null);
  assert.equal(docError({ name: "ДИ.doc", size: 1000 }), "Нужен файл .docx или .pdf");
  assert.equal(docError(null), "Выберите файл");
  assert.equal(docError({ name: "ДИ.docx", size: 0 }), "Файл пустой");
});

test("адрес редактора: #/profiles и #/profiles/<id>", () => {
  assert.deepEqual(parseHash("#/profiles"), { name: "profiles", params: {}, query: {} });
  assert.deepEqual(parseHash("#/profiles/12"), { name: "profiles", params: { id: "12" }, query: {} });
  assert.equal(hashFor("profiles", { id: 12 }), "#/profiles/12");
  assert.equal(hashFor("profiles"), "#/profiles");
  assert.equal(parseHash("#/profiles/1/2"), null);
});

test("портрет: вес стоит у поля, которое задаёт признак; остальные признаки — в блоке «Ещё признаки»", () => {
  assert.deepEqual(FEATURE_FIELDS, { keywords: "keywords", donor_company: "donors", geo: "geo", salary: "salary" });
  assert.deepEqual(EXTRA_FEATURES, ["exp_years", "title_match", "tenure", "open_code"]);
  // каждый признак показан ровно один раз: либо у своего поля, либо в «Ещё признаках»
  const placed = [...Object.keys(FEATURE_FIELDS), ...EXTRA_FEATURES].sort();
  assert.deepEqual(placed, FEATURES.map(([key]) => key).sort());
  assert.deepEqual(extraFeatures().map(([key]) => key), ["exp_years", "title_match", "tenure", "open_code"]);
  assert.equal(extraFeatures().every(([, label, hint]) => label.length > 0 && hint.length > 10), true);
  assert.equal(featureLabel("donor_company"), "Опыт в компании-доноре");
  assert.equal(featureLabel("неведомое"), "неведомое");
});

test("вес: доля от суммы для полоски (сумма 0 — доли нет)", () => {
  const weights = { exp_years: "20", keywords: 20, title_match: "15", donor_company: 10, tenure: 15, geo: 10, salary: 10, open_code: 0 };
  assert.equal(weightShare(weights, "keywords"), 20);
  assert.equal(weightShare(weights, "exp_years"), 20);
  assert.equal(weightShare(weights, "open_code"), 0);
  assert.equal(weightShare({ a: 1, b: 2 }, "a"), 33);   // округление до целого процента
  assert.equal(weightShare({ a: 0, b: 0 }, "a"), 0);
  assert.equal(weightShare({ a: "x", b: 5 }, "b"), 100);   // мусор в поле не считается
  assert.equal(weightShare({}, "a"), 0);
});

test("степпер веса: шаг 5, от 0 до 100, пустое и мусор считаются нулём", () => {
  assert.equal(stepWeight("20", 1), "25");
  assert.equal(stepWeight("20", -1), "15");
  assert.equal(stepWeight("2", -1), "0");
  assert.equal(stepWeight("0", -1), "0");
  assert.equal(stepWeight("98", 1), "100");
  assert.equal(stepWeight("100", 1), "100");
  assert.equal(stepWeight("", 1), "5");
  assert.equal(stepWeight("abc", 1), "5");
  assert.equal(stepWeight("12", 1), "17");   // не кратное пяти — просто прибавляем
});

test("сумма весов одной строкой", () => {
  assert.equal(weightSumText(100), "Сумма весов: 100 — сервер приводит к 100");
  assert.equal(weightSumText(110), "Сумма весов: 110 — сервер приводит к 100");
  assert.equal(weightSumText(0), "Сумма весов: 0 — сервер приводит к 100");
});
