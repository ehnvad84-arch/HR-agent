// Оргструктура 1С в «Базе должностей» (S47): дерево подразделений, скрытие пустых, поиск, счётчики, ставки, тексты окна
import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_ORG_BYTES, allUnitIds, breakdownLines, buildTree, canLoadPosition, filterTree, orgFileError, orgHeading, orgResultText, positionLink, positionState,
  rateText, rootOptions, syncText, treeCounts,
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

// ------------------------------------------------------------------ без повторов (T41)

const COMM = prof(20, "approved", { code: "comm_dir", title: "Коммерческий директор" });
const SALES = prof(21, "approved", { code: "sales_project", title: "Менеджер проектных продаж" });
const HEAD = prof(22, "approved", { code: "sales_head", title: "Руководитель отдела проектных продаж" });
const DIR = prof(23, "approved", { code: "director", title: "Директор" });
const MKT = prof(24, "empty", { code: "marketer", title: "Маркетолог" });
const DUP = [
  { id: 1, parent_id: null, title: "Компания", order: 1, positions: [pos(101, "Директор", DIR, [1, 1, 0])] },
  { id: 2, parent_id: 1, title: "Департамент продаж", order: 2, positions: [pos(201, "Коммерческий директор", COMM, [1, 1, 0]), pos(202, "Коммерческий директор", COMM, [1, 0, 1])] },
  { id: 3, parent_id: 2, title: "Отдел продаж №1", order: 3, positions: [pos(301, "Менеджер проектных продаж", SALES, [7, 5, 2]), pos(302, "Руководитель отдела проектных продаж", HEAD, [1, 1, 0])] },
  { id: 4, parent_id: 2, title: "Отдел продаж №2", order: 4, positions: [pos(401, "Менеджер проектных продаж", SALES, [7, 6, 1]), pos(402, "Руководитель отдела проектных продаж", HEAD, [1, 1, 0])] },
  { id: 5, parent_id: 2, title: "Отдел продаж №3", order: 5, positions: [pos(501, "Менеджер проектных продаж", SALES, [6, 4, 2]), pos(502, "Руководитель отдела проектных продаж", HEAD, [1, 0, 1])] },
  { id: 6, parent_id: 2, title: "Отдел маркетинга", order: 6, positions: [pos(601, "Маркетолог", MKT, [2, 2, 0]), pos(602, "Менеджер проектных продаж", SALES, [2, 2, 0])] },
  { id: 7, parent_id: 1, title: "Департамент аутсорсинга", order: 7, positions: [pos(701, "Директор", DIR, [1, 1, 0])] },
];
const find = (forest, title) => { let out = null; const visit = (nodes) => nodes.forEach((n) => { if (n.title === title) out = n; visit(n.children); }); visit(forest); return out; };
const row = (node, title) => node.positions.find((p) => p.title === title);

test("T41: две позиции одной должности в одном подразделении — одна строка, ставки сложены", () => {
  const dept = find(buildTree(DUP), "Департамент продаж");
  const comm = dept.positions.filter((p) => p.title === "Коммерческий директор");
  assert.equal(comm.length, 1);
  assert.deepEqual([comm[0].rates_total, comm[0].rates_occupied, comm[0].rates_open], [2, 1, 1]);
  assert.equal(comm[0].unitCount, 1);
  assert.equal(rateText(comm[0]), "ставки: занято 1 из 2, свободно 1");   // одно подразделение — подпись как раньше
});

test("T41: одна должность в братьях-отделах — строка в их общем родителе, отделы-братья без своих должностей скрыты", () => {
  const forest = buildTree(DUP);
  const dept = find(forest, "Департамент продаж");
  assert.deepEqual(dept.positions.map((p) => p.title), ["Коммерческий директор", "Менеджер проектных продаж", "Руководитель отдела проектных продаж"]);
  assert.equal(find(forest, "Отдел продаж №1"), null);
  assert.equal(find(forest, "Отдел продаж №2"), null);
  assert.equal(find(forest, "Отдел продаж №3"), null);
  assert.deepEqual(dept.children.map((n) => n.title), ["Отдел маркетинга"]);   // у маркетинга есть своя должность — он остаётся
  assert.deepEqual(find(forest, "Отдел маркетинга").positions.map((p) => p.title), ["Маркетолог"]);
});

test("T41: корень и его потомок — строка в корне; подразделение, где остался только повтор, скрыто", () => {
  const forest = buildTree(DUP);
  const root = find(forest, "Компания");
  const director = row(root, "Директор");
  assert.ok(director);
  assert.equal(director.unitCount, 2);
  assert.equal(find(forest, "Департамент аутсорсинга"), null);
  assert.deepEqual(allUnitIds(forest), [1, 2, 6]);
});

test("T41: ставки складываются, подпись «в N подразделениях» со склонением", () => {
  const sales = row(find(buildTree(DUP), "Департамент продаж"), "Менеджер проектных продаж");
  assert.deepEqual([sales.rates_total, sales.rates_occupied, sales.rates_open], [22, 17, 5]);
  assert.equal(sales.unitCount, 4);
  assert.equal(rateText(sales), "ставки: занято 17 из 22, свободно 5 · в 4 подразделениях");
  const director = row(find(buildTree(DUP), "Компания"), "Директор");
  assert.equal(rateText(director), "ставки: занято 2 из 2, свободных нет · в 2 подразделениях");
  assert.equal(rateText({ rates_total: 3, rates_occupied: 1, rates_open: 2, unitCount: 21 }), "ставки: занято 1 из 3, свободно 2 · в 21 подразделении");
  assert.equal(rateText({ rates_total: 3, rates_occupied: 1, rates_open: 2, unitCount: 2 }), "ставки: занято 1 из 3, свободно 2 · в 2 подразделениях");
  assert.equal(rateText({ rates_total: 3, rates_occupied: 1, rates_open: 2, unitCount: 8 }), "ставки: занято 1 из 3, свободно 2 · в 8 подразделениях");
});

test("T41: дробные ставки при сложении не расползаются (0,1 + 0,2 = 0,3)", () => {
  const units = [{ id: 1, parent_id: null, title: "Цех", order: 1, positions: [] },
    { id: 2, parent_id: 1, title: "Участок 1", order: 2, positions: [pos(1, "Наладчик", prof(1, "approved", { code: "n" }), [0.3, 0.1, 0.2])] },
    { id: 3, parent_id: 1, title: "Участок 2", order: 3, positions: [pos(2, "Наладчик", prof(1, "approved", { code: "n" }), [0.2, 0.2, 0])] }];
  const sum = buildTree(units)[0].positions[0];
  assert.deepEqual([sum.rates_total, sum.rates_occupied, sum.rates_open], [0.5, 0.3, 0.2]);
  assert.equal(rateText(sum), "ставки: занято 0,3 из 0,5, свободно 0,2 · в 2 подразделениях");
});

test("T41: разбивка по подразделениям — в порядке дерева, «занято N из M»", () => {
  const sales = row(find(buildTree(DUP), "Департамент продаж"), "Менеджер проектных продаж");
  assert.deepEqual(breakdownLines(sales), ["Отдел продаж №1 — занято 5 из 7", "Отдел продаж №2 — занято 6 из 7", "Отдел продаж №3 — занято 4 из 6", "Отдел маркетинга — занято 2 из 2"]);
  const comm = row(find(buildTree(DUP), "Департамент продаж"), "Коммерческий директор");
  assert.deepEqual(breakdownLines(comm), []);   // одно подразделение — разбивки нет
  assert.deepEqual(breakdownLines({}), []);
});

test("T41: ближайший общий родитель — не обязательно корень", () => {
  const units = [{ id: 1, parent_id: null, title: "Корень", order: 1, positions: [] },
    { id: 2, parent_id: 1, title: "Ветка А", order: 2, positions: [] },
    { id: 3, parent_id: 2, title: "А-1", order: 3, positions: [pos(1, "Кладовщик", null, [1, 1, 0])] },
    { id: 4, parent_id: 2, title: "А-2", order: 4, positions: [pos(2, "Кладовщик", null, [1, 0, 1])] },
    { id: 5, parent_id: 1, title: "Ветка Б", order: 5, positions: [pos(3, "Бухгалтер", null)] },
    { id: 6, parent_id: 5, title: "Б-1", order: 6, positions: [pos(4, "Кладовщик", null, [2, 2, 0])] }];
  const forest = buildTree(units);
  assert.equal(row(find(forest, "Корень"), "Кладовщик").unitCount, 3);   // А-1, А-2 и Б-1: общий предок — корень
  const a = [units[0], units[1], units[2], units[3]];
  const forestA = buildTree(a);
  assert.equal(row(find(forestA, "Ветка А"), "Кладовщик").unitCount, 2);   // только в двух отделах ветки А — остаётся в ветке А
  assert.equal(find(forestA, "А-1"), null);
});

test("T41: у должностей без портрета ключ — название без регистра, лишних пробелов и «ё/е»", () => {
  const units = [{ id: 1, parent_id: null, title: "Цех", order: 1, positions: [] },
    { id: 2, parent_id: 1, title: "Участок 1", order: 2, positions: [pos(1, "Мастёр участка", null)] },
    { id: 3, parent_id: 1, title: "Участок 2", order: 3, positions: [pos(2, "мастер   участка", null)] }];
  const rows = buildTree(units)[0].positions;
  assert.equal(rows.length, 1);
  assert.equal(rows[0].title, "Мастёр участка");   // название — первой по дереву позиции
  assert.equal(rows[0].unitCount, 2);
  assert.equal(treeCounts(filterTree(buildTree(units), { q: "мастер" })).positions, 1);   // и поиск не различает «ё» и «е»
});

test("T41: разные портреты у одного названия или портрет и его отсутствие — не объединяются", () => {
  const units = [{ id: 1, parent_id: null, title: "Цех", order: 1, positions: [] },
    { id: 2, parent_id: 1, title: "Участок 1", order: 2, positions: [pos(1, "Технолог", prof(1, "approved", { code: "tech_a" })), pos(3, "Слесарь", null)] },
    { id: 3, parent_id: 1, title: "Участок 2", order: 3, positions: [pos(2, "Технолог", prof(2, "approved", { code: "tech_b" })), pos(4, "Слесарь", prof(5, "empty", { code: "locksmith" }))] }];
  const titles = buildTree(units)[0].children.flatMap((n) => n.positions.map((p) => p.title));
  assert.deepEqual(titles.sort(), ["Слесарь", "Слесарь", "Технолог", "Технолог"]);
  assert.equal(treeCounts(buildTree(units)).positions, 4);
});

test("T41: должность в разных верхних подразделениях не объединяется — общего родителя у них нет", () => {
  const units = [{ id: 1, parent_id: null, title: "Завод", order: 1, positions: [pos(1, "Директор", prof(1, "approved", { code: "director" }))] },
    { id: 2, parent_id: null, title: "Торговый дом", order: 2, positions: [pos(2, "Директор", prof(1, "approved", { code: "director" }))] }];
  const forest = buildTree(units);
  assert.deepEqual(forest.map((n) => [n.title, n.positions.length]), [["Завод", 1], ["Торговый дом", 1]]);
  assert.equal(forest[0].positions[0].unitCount, 1);
});

test("T41: счётчики — по уникальным должностям", () => {
  const forest = buildTree(DUP);
  assert.deepEqual(treeCounts(forest), { positions: 5, noCriteria: 1 });   // Директор, Коммерческий директор, Менеджер, Руководитель, Маркетолог
  assert.equal(forest[0].count, 5);
  assert.equal(find(forest, "Департамент продаж").count, 4);
  assert.equal(find(forest, "Отдел маркетинга").count, 1);
  assert.equal(orgHeading(treeCounts(forest)), "База должностей: 5 должностей, без критериев 1");
  assert.deepEqual(treeCounts(filterTree(forest, { q: "менеджер" })), { positions: 1, noCriteria: 0 });
});

test("T41: поиск находит строку по названию подразделения из разбивки, хотя само подразделение скрыто", () => {
  const forest = buildTree(DUP);
  const found = filterTree(forest, { q: "Отдел продаж №3" });
  assert.deepEqual(treeCounts(found).positions, 2);   // «Менеджер…» и «Руководитель…»: у обеих позиция есть в отделе №3
  assert.deepEqual(find(found, "Департамент продаж").positions.map((p) => p.title), ["Менеджер проектных продаж", "Руководитель отдела проектных продаж"]);
  assert.equal(find(found, "Отдел маркетинга"), null);
  assert.deepEqual(treeCounts(filterTree(forest, { q: "маркетинга" })).positions, 2);   // «Маркетолог» и «Менеджер» (есть в отделе маркетинга)
});

test("T41: поиск по названию в 1С любой из позиций; подразделение нашлось — все его строки остаются", () => {
  const units = [{ id: 1, parent_id: null, title: "Департамент", order: 1, positions: [pos(1, "Коммерческий директор", COMM, [1, 1, 0], { title_1c: "Коммерческий директор/Департамент/" }),
    pos(2, "Коммерческий директор", COMM, [1, 0, 1], { title_1c: "Коммерческий директор /Основное/" })] }];
  const forest = buildTree(units);
  assert.equal(treeCounts(filterTree(forest, { q: "основное" })).positions, 1);
  assert.equal(treeCounts(filterTree(forest, { q: "департамент/" })).positions, 1);
  assert.equal(filterTree(buildTree(DUP), { q: "департамент продаж" })[0].children[0].children[0].positions.length, 1);   // «Отдел маркетинга» целиком
});

test("T41: фильтр по верхнему подразделению оставляет строку, если она в его ветке", () => {
  const forest = buildTree(DUP);
  assert.deepEqual(filterTree(forest, { deptId: 1 }).map((n) => n.title), ["Компания"]);
  assert.deepEqual(treeCounts(filterTree(forest, { deptId: 1 })), treeCounts(forest));
  assert.deepEqual(filterTree(forest, { deptId: 99 }), []);
});

test("T41: кнопки и бейдж — по портрету строки", () => {
  const sales = row(find(buildTree(DUP), "Департамент продаж"), "Менеджер проектных продаж");
  assert.equal(canLoadPosition(sales), true);
  assert.equal(positionLink(sales), 21);
  assert.deepEqual(positionState(sales), { text: "действует", pill: "cand" });
  const marketer = row(find(buildTree(DUP), "Отдел маркетинга"), "Маркетолог");
  assert.deepEqual(positionState(marketer), { text: "критерии не заполнены", pill: "warm" });
  assert.equal(canLoadPosition(marketer), false);
});
