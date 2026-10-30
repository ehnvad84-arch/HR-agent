// Оргструктура 1С в «Базе должностей» (S47): дерево подразделений, скрытие пустых, поиск, счётчики, ставки, тексты окна
import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_ORG_BYTES, allUnitIds, buildTree, canLoadPosition, filterTree, orgFileError, orgHeading, orgResultText, positionLink, positionState, rateText,
  rootOptions, syncText, treeCounts,
} from "../../web/js/screens/org-model.js";
import { rowSub, stateInfo } from "../../web/js/screens/profiles-model.js";

const prof = (id, state, extra = {}) => ({ id, code: `p${id}`, title: `Портрет ${id}`, state, version: "1.0", geo: "krasnodar", pending_id: null, ...extra });
const pos = (id, title, profile, rates = [1, 1, 0], extra = {}) => ({ id, title, title_1c: `${title}/Отдел/`, rates_total: rates[0], rates_occupied: rates[1], rates_open: rates[2], profile, ...extra });
const UNITS = [
  { id: 1, parent_id: null, title: "Дирекция", order: 1, positions: [pos(11, "Директор", prof(5, "approved"))] },
  { id: 2, parent_id: null, title: "Производство", order: 2, positions: [] },
  { id: 3, parent_id: 2, title: "Цех сборки", order: 3, positions: [pos(31, "Монтажник РЭА", prof(6, "empty"), [7, 5, 2]), pos(32, "Контролёр ОТК", prof(7, "pending"), [2, 2, 0])] },
  { id: 4, parent_id: 3, title: "Участок наладки", order: 4, positions: [] },
  { id: 5, parent_id: 2, title: "Склад", order: 5, positions: [] },
  { id: 6, parent_id: null, title: "Отдел продаж", order: 6, positions: [pos(61, "Менеджер проектных продаж", prof(8, "approved"), [3, 2.5, 0.5]), pos(62, "Менеджер без портрета", null)] },
  { id: 7, parent_id: 99, title: "Подразделение-сирота", order: 7, positions: [pos(71, "Кладовщик", prof(9, "draft"))] },
];

test("дерево строится по parent_id, порядок 1С сохраняется, уровень вложенности известен", () => {
  const forest = buildTree(UNITS);
  assert.deepEqual(forest.map((n) => n.title), ["Дирекция", "Производство", "Отдел продаж", "Подразделение-сирота"]);   // сирота — на верхнем уровне
  assert.deepEqual(forest.map((n) => n.depth), [0, 0, 0, 0]);
  const production = forest[1];
  assert.deepEqual(production.children.map((n) => n.title), ["Цех сборки"]);   // «Склад» пуст и скрыт
  assert.equal(production.children[0].depth, 1);
  assert.equal(production.children[0].children.length, 0);   // «Участок наладки» пуст и скрыт
});

test("подразделения без должностей и без детей с должностями скрываются", () => {
  const ids = allUnitIds(buildTree(UNITS));
  assert.deepEqual(ids, [1, 2, 3, 6, 7]);
  assert.deepEqual(buildTree([{ id: 1, parent_id: null, title: "Пустое", order: 1, positions: [] }]), []);
  assert.deepEqual(buildTree([]), []);
  assert.deepEqual(buildTree(null), []);
});

test("цикл в данных не зацикливает построение", () => {
  const loop = [{ id: 1, parent_id: 2, title: "А", order: 1, positions: [pos(1, "Ж", prof(1, "approved"))] }, { id: 2, parent_id: 1, title: "Б", order: 2, positions: [pos(2, "З", prof(2, "approved"))] }];
  assert.doesNotThrow(() => buildTree(loop));
  assert.equal(treeCounts(buildTree(loop)).positions, 2);
});

test("счётчики: должностей всего и без критериев (портрет пустой или не привязан)", () => {
  const forest = buildTree(UNITS);
  assert.deepEqual(treeCounts(forest), { positions: 6, noCriteria: 2 });   // 11, 31, 32, 61, 62, 71; без критериев: 31 (пустой) и 62 (нет портрета)
  assert.equal(forest[1].count, 2);   // у подразделения — сколько должностей в нём и ниже
  assert.equal(forest[1].children[0].count, 2);
  assert.equal(forest[0].count, 1);
  assert.deepEqual(treeCounts([]), { positions: 0, noCriteria: 0 });
});

test("заголовок: «База должностей: N должностей, без критериев K»", () => {
  assert.equal(orgHeading({ positions: 6, noCriteria: 2 }), "База должностей: 6 должностей, без критериев 2");
  assert.equal(orgHeading({ positions: 1, noCriteria: 0 }), "База должностей: 1 должность, без критериев 0");
  assert.equal(orgHeading({ positions: 22, noCriteria: 1 }), "База должностей: 22 должности, без критериев 1");
});

test("поиск по дереву: по названию должности (и названию в 1С), регистр не важен, подразделения-предки остаются", () => {
  const forest = buildTree(UNITS);
  const found = filterTree(forest, { q: "МОНТАЖ" });
  assert.deepEqual(found.map((n) => n.title), ["Производство"]);
  assert.deepEqual(found[0].children.map((n) => n.title), ["Цех сборки"]);
  assert.deepEqual(found[0].children[0].positions.map((p) => p.title), ["Монтажник РЭА"]);
  assert.deepEqual(treeCounts(found), { positions: 1, noCriteria: 1 });
  assert.deepEqual(filterTree(forest, { q: "Отдел/" }).length, 4);   // хвост названия 1С тоже ищется
  assert.deepEqual(filterTree(forest, { q: "нетакой" }), []);
  assert.equal(filterTree(forest, { q: "   " }).length, 4);   // пустой запрос — всё дерево
});

test("поиск по названию подразделения оставляет все его должности", () => {
  const found = filterTree(buildTree(UNITS), { q: "цех" });
  assert.deepEqual(found[0].children[0].positions.map((p) => p.title), ["Монтажник РЭА", "Контролёр ОТК"]);
});

test("фильтр по подразделению — верхний уровень", () => {
  const forest = buildTree(UNITS);
  assert.deepEqual(rootOptions(forest), [{ id: 1, title: "Дирекция" }, { id: 2, title: "Производство" }, { id: 6, title: "Отдел продаж" }, { id: 7, title: "Подразделение-сирота" }]);
  assert.deepEqual(filterTree(forest, { deptId: 2 }).map((n) => n.title), ["Производство"]);
  assert.deepEqual(filterTree(forest, { deptId: "2" }).map((n) => n.title), ["Производство"]);
  assert.equal(filterTree(forest, { deptId: "" }).length, 4);
  assert.deepEqual(filterTree(forest, { deptId: 2, q: "продаж" }), []);   // фильтр и поиск складываются
});

test("ставки: занято из всего и свободно", () => {
  assert.equal(rateText({ rates_total: 7, rates_occupied: 5, rates_open: 2 }), "ставки: занято 5 из 7, свободно 2");
  assert.equal(rateText({ rates_total: 3, rates_occupied: 2.5, rates_open: 0.5 }), "ставки: занято 2,5 из 3, свободно 0,5");
  assert.equal(rateText({ rates_total: 2, rates_occupied: 2, rates_open: 0 }), "ставки: занято 2 из 2, свободных нет");
  assert.equal(rateText({}), "");
});

test("состояние портрета у должности: критерии не заполнены — заметно, тёплым", () => {
  assert.deepEqual(positionState(pos(1, "А", prof(1, "empty"))), { text: "критерии не заполнены", pill: "warm" });
  assert.deepEqual(positionState(pos(1, "А", prof(1, "pending"))), { text: "ждёт утверждения", pill: "watch" });
  assert.deepEqual(positionState(pos(1, "А", prof(1, "approved"))), { text: "действует", pill: "cand" });
  assert.deepEqual(positionState(pos(1, "А", prof(1, "draft"))), { text: "черновик", pill: "obs" });
  assert.deepEqual(positionState(pos(1, "А", null)), { text: "портрет не привязан", pill: "warm" });
  assert.deepEqual(stateInfo("empty"), { text: "критерии не заполнены", pill: "warm" });
});

test("строка плоского списка для пустого портрета", () => {
  assert.equal(rowSub({ dept: "Производство", version: "1.0", state: "empty", approved_at: null }), "Производство · критерии не заполнены");
});

test("клик по должности открывает портрет; загрузка резюме — только у утверждённого", () => {
  assert.equal(positionLink(pos(1, "А", prof(5, "empty"))), 5);
  assert.equal(positionLink(pos(1, "А", null)), null);
  assert.equal(canLoadPosition(pos(1, "А", prof(5, "approved"))), true);
  assert.equal(canLoadPosition(pos(1, "А", prof(5, "empty"))), false);
  assert.equal(canLoadPosition(pos(1, "А", prof(5, "pending"))), false);
  assert.equal(canLoadPosition(pos(1, "А", null)), false);
});

test("итог обновления из 1С — тост", () => {
  assert.equal(orgResultText({ state: "applied", file_name: "оргструктура.json", units: 42, positions: 180, changes: 7, new_profiles: 3 }),
    "Оргструктура обновлена: подразделений 42, должностей 180, изменений 7, новых портретов 3");
  assert.equal(orgResultText({ state: "same", file_name: "оргструктура.json" }), "Эта выгрузка уже применена");
  assert.equal(orgResultText({ state: "applied", units: 1, positions: 2 }), "Оргструктура обновлена: подразделений 1, должностей 2, изменений 0, новых портретов 0");
});

test("последняя выгрузка в окне", () => {
  const now = new Date(2026, 9, 30, 15, 0);
  assert.equal(syncText(null, now), "Выгрузок из 1С ещё не было");
  const at = new Date(2026, 9, 30, 12, 40).toISOString();
  assert.equal(syncText({ received_at: at, file_name: "оргструктура.json", units: 42, positions: 180 }, now),
    "Последняя выгрузка: сегодня, 12:40 · оргструктура.json · подразделений 42, должностей 180");
});

test("файл выгрузки: .json до 5 МБ", () => {
  assert.equal(MAX_ORG_BYTES, 5 * 1024 * 1024);
  assert.equal(orgFileError({ name: "orgstruct.json", size: 1200 }), null);
  assert.equal(orgFileError({ name: "ОРГ.JSON", size: 1200 }), null);
  assert.equal(orgFileError(null), "Выберите файл");
  assert.equal(orgFileError({ name: "orgstruct.xlsx", size: 1200 }), "Нужен файл .json");
  assert.equal(orgFileError({ name: "orgstruct.json", size: 0 }), "Файл пустой");
  assert.equal(orgFileError({ name: "orgstruct.json", size: MAX_ORG_BYTES + 1 }), "Файл больше 5 МБ");
});
