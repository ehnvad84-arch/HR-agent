// Личное обучение (S14–S16): поправки к баллу, предложения агента, «мой балл» — модель без DOM
import assert from "node:assert/strict";
import test from "node:test";

import {
  CHAT_CAPTION, CHAT_INTRO, MAX_WORD, MIN_WORD, RESET_HINT, SAVE_HINT, diffTweaks, draftFrom, groupTweaks, isDirty, proposalText,
  tweakText, validateWord, weightRows,
} from "../../web/js/screens/tweaks-model.js";

const T = (over = {}) => ({ id: 1, kind: "plus", value: { text: "Altium" }, state: "active", source: "manual", role_code: null, created_at: "2026-10-29T09:00:00", in_digest: false, ...over });
const COMMON = { exp_years: 20, keywords: 25, title_match: 15, donor_company: 10, tenure: 10, geo: 10, salary: 10, open_code: 0 };

test("S21: подпись и описание чата — как в спеке", () => {
  assert.equal(CHAT_CAPTION, "Ваши замечания видите вы, агент и руководитель — в недельной сводке");
  assert.equal(CHAT_INTRO, "Сюда каждый пишет замечания, рекомендации и критику. Агент учится под вас: предлагает личные поправки к вашему баллу. "
    + "Раз в неделю все замечания уходят руководителю сводкой; общими поправки становятся только после его решения.");
});

test("S15: подсказки под кнопками — только ваш балл и порядок", () => {
  assert.equal(SAVE_HINT, "Меняет только ваш балл и порядок ваших списков");
  assert.ok(RESET_HINT.includes("общим"));
});

test("слово поправки: от 2 до 60 знаков, без ссылок и разметки, без повтора в своём виде", () => {
  assert.equal(MIN_WORD, 2);
  assert.equal(MAX_WORD, 60);
  assert.deepEqual(validateWord("  Altium   Designer ", []), { ok: true, value: "Altium Designer" });
  assert.equal(validateWord("а", []).error, "Слово или фраза — от 2 до 60 знаков");
  assert.equal(validateWord("а".repeat(61), []).error, "Слово или фраза — от 2 до 60 знаков");
  assert.equal(validateWord("а".repeat(60), []).ok, true);
  assert.equal(validateWord("см. https://example.org/x", []).error, "Без ссылок и разметки");
  assert.equal(validateWord("www.example.org", []).error, "Без ссылок и разметки");
  assert.equal(validateWord("<script>", []).error, "Без ссылок и разметки");
  assert.equal(validateWord("altium", ["Altium"]).error, "Это слово уже есть в списке");
  assert.equal(validateWord("Ёлка", ["елка"]).error, "Это слово уже есть в списке");   // ё = е, регистр не важен
  assert.equal(validateWord("", []).error, "Слово или фраза — от 2 до 60 знаков");
  assert.equal(validateWord(null, []).error, "Слово или фраза — от 2 до 60 знаков");
});

test("S15: поправки группируются по видам; предложения агента отдельно; выключенных нет", () => {
  const items = [T({ id: 1 }), T({ id: 2, kind: "minus", value: { text: "1С-программист" } }), T({ id: 3, kind: "stop", value: { text: "стажёр" } }),
    T({ id: 4, kind: "weight", value: { feature: "open_code", weight: 15 } }),
    T({ id: 5, kind: "minus", value: { text: "бухгалтер" }, state: "proposed", source: "agent" }), T({ id: 6, state: "off", value: { text: "старое" } })];
  const groups = groupTweaks(items);
  assert.deepEqual(groups.plus.map((t) => t.id), [1]);
  assert.deepEqual(groups.minus.map((t) => t.id), [2]);
  assert.deepEqual(groups.stop.map((t) => t.id), [3]);
  assert.deepEqual(groups.weights, { open_code: 15 });
  assert.deepEqual(groups.proposed.map((t) => t.id), [5]);
  assert.deepEqual(groupTweaks(null), { plus: [], minus: [], stop: [], weights: {}, proposed: [] });
});

test("S15: предложение агента — «По вашим замечаниям: минус «…»», вес — с названием признака", () => {
  assert.equal(proposalText(T({ kind: "minus", value: { text: "1С-программист" }, state: "proposed" })), "По вашим замечаниям: минус «1С-программист»");
  assert.equal(proposalText(T({ kind: "plus", value: { text: "Altium" } })), "По вашим замечаниям: плюс «Altium»");
  assert.equal(proposalText(T({ kind: "stop", value: { text: "стажёр" } })), "По вашим замечаниям: стоп-слово «стажёр»");
  assert.equal(proposalText(T({ kind: "weight", value: { feature: "open_code", weight: 15 } })), "По вашим замечаниям: вес «Открытый код» → 15");
  assert.equal(tweakText(T({ kind: "weight", value: { feature: "geo", weight: 0 } })), "вес «География» → 0");
});

test("S15: вес признака — общий рядом, свой поверх; по умолчанию свой равен общему", () => {
  const rows = weightRows(COMMON, { open_code: 15, keywords: 40 });
  assert.equal(rows.length, 8);
  assert.deepEqual(rows[0], { key: "exp_years", label: "Опыт по профилю", common: 20, value: 20, changed: false });
  assert.deepEqual(rows.find((r) => r.key === "keywords"), { key: "keywords", label: "Ключевые слова и инструменты", common: 25, value: 40, changed: true });
  assert.deepEqual(rows.find((r) => r.key === "open_code"), { key: "open_code", label: "Открытый код", common: 0, value: 15, changed: true });
  assert.equal(weightRows(null, {}).every((r) => r.common === 0), true);   // общие веса не пришли: нули, форма не падает
});

test("S15: «Сохранить мои поправки» шлёт только разницу: что добавить и что выключить", () => {
  const items = [T({ id: 1 }), T({ id: 2, kind: "minus", value: { text: "бухгалтер" } }), T({ id: 4, kind: "weight", value: { feature: "keywords", weight: 40 } })];
  const initial = draftFrom(items, COMMON);
  assert.deepEqual(initial, { plus: ["Altium"], minus: ["бухгалтер"], stop: [], weights: { exp_years: 20, keywords: 40, title_match: 15, donor_company: 10, tenure: 10, geo: 10, salary: 10, open_code: 0 } });
  assert.equal(isDirty(items, initial, COMMON), false);

  const draft = { plus: ["Altium", "KiCad"], minus: [], stop: ["стажёр"], weights: { ...initial.weights, keywords: 25, open_code: 10 } };
  assert.equal(isDirty(items, draft, COMMON), true);
  const diff = diffTweaks(items, draft, COMMON);
  assert.deepEqual(diff.add, [
    { kind: "plus", value: { text: "KiCad" } }, { kind: "stop", value: { text: "стажёр" } }, { kind: "weight", value: { feature: "open_code", weight: 10 } },
  ]);
  assert.deepEqual(diff.off, [2, 4]);   // «бухгалтер» убран; вес «ключевые слова» вернулся к общему — поправка выключена
});

test("S15: вес, изменённый на другое значение, — новая поправка (старую сервер выключит сам); пустая разница — пусто", () => {
  const items = [T({ id: 4, kind: "weight", value: { feature: "keywords", weight: 40 } })];
  const base = draftFrom(items, COMMON);
  const draft = { ...base, weights: { ...base.weights, keywords: 50 } };
  assert.deepEqual(diffTweaks(items, draft, COMMON), { add: [{ kind: "weight", value: { feature: "keywords", weight: 50 } }], off: [] });
  assert.deepEqual(diffTweaks(items, base, COMMON), { add: [], off: [] });
});

test("S15: слова сравниваются без учёта регистра и ё — перенабранное слово не плодит дубль", () => {
  const items = [T({ id: 1, value: { text: "Ёлка" } })];
  const draft = { plus: ["елка"], minus: [], stop: [], weights: draftFrom(items, COMMON).weights };
  assert.deepEqual(diffTweaks(items, draft, COMMON), { add: [], off: [] });
});
