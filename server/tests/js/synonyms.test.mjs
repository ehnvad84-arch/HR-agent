// «Синонимы ключей» в «Базе должностей» (S1–S4): черновик, правка, состояния карточки, история и откат
import assert from "node:assert/strict";
import test from "node:test";

import { ApiError, createClient } from "../../web/js/api.js";
import { createSession } from "../../web/js/session.js";
import {
  NO_KEYS_TIP, READONLY_NOTE, applySuggestions, changeCount, diffText, dirtyText, discard, draftFromView, historyLines, isDirty, normKey,
  panelState, rollbackSheet, savePayload, setTerms, shortName, suggestFailure, suggestedText,
} from "../../web/js/screens/synonyms-model.js";

const at = (d, m, hh, mm) => new Date(2026, m - 1, d, hh, mm).toISOString();
const VIEW = {
  role_code: "hw_lead", can_edit: true,
  items: [{ keyword: "Altium", synonyms: ["P-CAD"] }, { keyword: "схемотехника", synonyms: [] }, { keyword: "STM32", synonyms: [] }],
  history: [
    { id: 9, applied_at: at(22, 11, 14, 5), by: "Котенко Алексей", diff: { altium: { "+": ["KiCad"], "-": ["OrCAD"] } }, rollback_of: null, can_rollback: true },
    { id: 8, applied_at: at(21, 11, 10, 12), by: "Иванова Мария", diff: {}, rollback_of: 7, can_rollback: false },
    { id: 7, applied_at: at(20, 11, 9, 0), by: "Иванова Мария", diff: { "схемотехника": { "+": ["разработка схем"], "-": [] } }, rollback_of: null, can_rollback: false },
  ],
};

test("S1: предложение модели ложится «новыми» чипсами, повторы и сами ключи отсеяны", () => {
  const draft = draftFromView(VIEW);
  const { draft: next, added } = applySuggestions(draft, {
    Altium: ["KiCad", "p-cad", "OrCAD", "схемотехника"], "схемотехника": ["разработка схем", "Kicad"], "Неизвестный": ["Х"],
  });
  assert.equal(added, 3);
  assert.deepEqual(next.rows[0].terms, ["P-CAD", "KiCad", "OrCAD"]);
  assert.deepEqual(next.rows[0].fresh, ["KiCad", "OrCAD"]);
  assert.deepEqual(next.rows[1].terms, ["разработка схем"]);
  assert.deepEqual(draft.rows[0].terms, ["P-CAD"], "исходный черновик не меняется");
  assert.equal(dirtyText(next), "Не сохранено: +3");
  assert.equal(suggestedText(3), "Модель предложила 3 термина. Лишние уберите ×, свои допишите.");
  assert.equal(suggestedText(1), "Модель предложила 1 термин. Лишние уберите ×, свои допишите.");
  assert.equal(suggestedText(0), "Модель ничего не предложила — впишите синонимы сами");
});

test("S1: правка чипсами — счётчик «+N −M», «Отменить» возвращает сохранённое", () => {
  let draft = draftFromView(VIEW);
  assert.equal(isDirty(draft), false);
  assert.equal(dirtyText(draft), "");
  draft = setTerms(draft, "Altium", ["KiCad", "OrCAD"]);   // P-CAD убрали, два добавили
  assert.deepEqual(changeCount(draft), { plus: 2, minus: 1 });
  assert.equal(dirtyText(draft), "Не сохранено: +2 −1");
  draft = setTerms(draft, "Altium", ["P-CAD"]);
  assert.equal(dirtyText(draft), "");
  draft = setTerms(draft, "STM32", ["STM"]);
  draft = setTerms(draft, "STM32", []);
  assert.equal(isDirty(draft), false);
  const back = discard(applySuggestions(draft, { Altium: ["KiCad"] }).draft);
  assert.deepEqual(back.rows[0].terms, ["P-CAD"]);
  assert.deepEqual(back.rows[0].fresh, []);
});

test("S1: убранный чипс предложения больше не «новый»", () => {
  const { draft } = applySuggestions(draftFromView(VIEW), { Altium: ["KiCad", "OrCAD"] });
  const next = setTerms(draft, "Altium", ["P-CAD", "OrCAD"]);
  assert.deepEqual(next.rows[0].fresh, ["OrCAD"]);
});

test("S2: тело сохранения — все ключи и номер последней записи; без истории — null", () => {
  const { draft } = applySuggestions(draftFromView(VIEW), { STM32: ["Cortex-M"] });
  assert.deepEqual(savePayload(draft), { items: [
    { keyword: "Altium", synonyms: ["P-CAD"] }, { keyword: "схемотехника", synonyms: [] }, { keyword: "STM32", synonyms: ["Cortex-M"] }], base_change_id: 9 });
  assert.equal(savePayload(draftFromView({ ...VIEW, history: [] })).base_change_id, null);
});

test("состояния карточки: загрузка, ошибка, нет права, нет ключей, идёт запрос, есть правки", () => {
  assert.equal(panelState({ loading: true }).mode, "loading");
  assert.equal(panelState({}).mode, "loading");
  assert.equal(panelState({ error: new Error("x") }).mode, "error");

  const draft = draftFromView(VIEW);
  const ready = panelState({ view: VIEW, draft });
  assert.equal(ready.mode, "ready");
  assert.deepEqual(ready.suggest, { label: "Предложить синонимы", disabled: false, inactive: false, tip: "", stop: false });
  assert.equal(ready.showSave, false);
  assert.equal(ready.historyTitle, "История синонимов (3)");
  assert.equal(ready.emptyRow(ready ? draft.rows[2] : null), true);
  assert.equal(ready.emptyRow(draft.rows[0]), false);

  const asking = panelState({ view: VIEW, draft, asking: true });
  assert.deepEqual(asking.suggest, { label: "Модель подбирает…", disabled: true, inactive: false, tip: "", stop: true });

  const dirty = panelState({ view: VIEW, draft: setTerms(draft, "Altium", []) });
  assert.equal(dirty.showSave, true);
  assert.equal(dirty.dirtyText, "Не сохранено: −1");

  const nobody = panelState({ view: { ...VIEW, can_edit: false }, draft });
  assert.equal(nobody.canEdit, false);
  assert.equal(nobody.suggest, null);
  assert.equal(nobody.showSave, false);
  assert.equal(nobody.readonlyNote, READONLY_NOTE);
  assert.equal(READONLY_NOTE, "Синонимы правит рекрутер");

  const noKeys = panelState({ view: { ...VIEW, items: [], history: [] }, draft: draftFromView({ ...VIEW, items: [], history: [] }) });
  assert.equal(noKeys.suggest.inactive, true);
  assert.equal(noKeys.suggest.tip, NO_KEYS_TIP);
  assert.equal(NO_KEYS_TIP, "Сначала добавьте ключевые слова и сохраните версию");
  assert.equal(noKeys.historyTitle, "История синонимов (0)");
});

test("S4: тексты отказов модели", () => {
  assert.deepEqual(suggestFailure({ status: 503, code: "llm_unavailable" }), { text: "Модель сейчас недоступна — впишите синонимы сами" });
  assert.deepEqual(suggestFailure({ status: 429, code: "llm_daily_limit" }),
    { text: "Предел запросов к модели на сегодня исчерпан", extra: "Вписать вручную можно всегда" });
  assert.deepEqual(suggestFailure({ status: 422, code: "no_keywords", message: "У профиля нет ключевых слов" }), { text: "У профиля нет ключевых слов" });
  assert.match(suggestFailure(new Error("сеть")).text, /впишите синонимы сами/);
});

test("S3: история — «когда · кто · что», откат подписан датой отменённой правки, откатить можно только отмеченное сервером", () => {
  const lines = historyLines(VIEW);
  assert.deepEqual(lines.map((l) => [l.id, l.when, l.by, l.text, l.canRollback]), [
    [9, "22.11 14:05", "Котенко А.", "Altium: +KiCad, −OrCAD", true],
    [8, "21.11 10:12", "Иванова М.", "откат правки от 20.11", false],
    [7, "20.11 09:00", "Иванова М.", "схемотехника: +разработка схем", false],
  ]);
  assert.deepEqual(rollbackSheet(lines[0]), {
    title: "Откатить правку от 22.11 14:05?", confirmText: "Откатить",
    hint: "Синонимы станут как до неё. Откат тоже попадёт в журнал, профиль пересчитается в фоне" });
  assert.deepEqual(historyLines({ history: [] }), []);
  assert.equal(historyLines({ history: [{ id: 1, applied_at: at(1, 11, 1, 1), by: null, diff: {}, rollback_of: 99, can_rollback: false }] })[0].text, "откат правки");
});

test("вспомогательное: ключ как на сервере, короткое имя, дифф", () => {
  assert.equal(normKey("  Ёлка  Схема "), "елка схема");
  assert.equal(shortName("Котенко Алексей Петрович"), "Котенко А.");
  assert.equal(shortName("Котенко А."), "Котенко А.");
  assert.equal(shortName(""), "система");
  assert.equal(diffText({ stm32: { "+": ["STM"], "-": [] } }, ["STM32"]), "STM32: +STM");
});

test("S2: клиент умеет PUT с токеном и JSON (сохранение синонимов)", async () => {
  const calls = [];
  const session = createSession();
  session.set({ user: { id: 1, login: "dev", full_name: "И" }, permissions: [], csrf: "tok1" });
  const client = createClient({ session, timeoutMs: 200, fetchImpl: async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify({ change_id: 10 }), { status: 200, headers: { "content-type": "application/json" } });
  } });
  assert.deepEqual(await client.put("/api/profiles/5/synonyms", { items: [], base_change_id: null }), { change_id: 10 });
  assert.equal(calls[0].init.method, "PUT");
  assert.equal(calls[0].init.headers["X-CSRF-Token"], "tok1");
  assert.equal(calls[0].init.body, JSON.stringify({ items: [], base_change_id: null }));
  assert.ok(ApiError);
});
