// Оргструктура 1С в «Базе должностей» без DOM (S47): дерево подразделений, скрытие пустых, поиск и фильтр, счётчики, ставки, тексты окна.
// Данные — GET /api/org: {sync, units: [{id, parent_id, title, order, positions: [{id, title, title_1c, rates_*, profile}]}]},
// плоский список активных подразделений в порядке 1С; дерево строится здесь по parent_id.
import { plural, whenLabel } from "../format.js";
import { stateInfo } from "./profiles-model.js";

export const MAX_ORG_BYTES = 5 * 1024 * 1024;

/** Без регистра, лишних пробелов и различия «ё/е»: так сравниваются названия и ищется текст. */
const norm = (text) => String(text ?? "").toLowerCase().replace(/ё/g, "е").replace(/\s+/g, " ").trim();
const round2 = (n) => Math.round((Number(n) || 0) * 100) / 100;

// ------------------------------------------------------------------ дерево

/** Ключ должности: код портрета, а без портрета — название (без хвоста «/…/», регистр и «ё/е» не важны). Один ключ — одна строка. */
const positionKey = (p) => (p.profile ? `p:${p.profile.code ?? p.profile.id}` : `t:${norm(p.title)}`);

/** Одна строка вместо нескольких позиций: ставки сложены, разбивка по подразделениям — в порядке дерева. */
function mergeRow(entries, key) {
  const first = entries[0].p;
  const byUnit = new Map();
  for (const { node, p } of entries) {
    const part = byUnit.get(node.id) || { unitId: node.id, unitTitle: node.title, rates_total: 0, rates_occupied: 0, rates_open: 0 };
    part.rates_total = round2(part.rates_total + Number(p.rates_total || 0));
    part.rates_occupied = round2(part.rates_occupied + Number(p.rates_occupied || 0));
    part.rates_open = round2(part.rates_open + Number(p.rates_open || 0));
    byUnit.set(node.id, part);
  }
  const breakdown = [...byUnit.values()];
  const sum = (field) => round2(breakdown.reduce((total, part) => total + part[field], 0));
  return { ...first, key, title_1c: [...new Set(entries.map((e) => e.p.title_1c).filter(Boolean))].join(" | "),
    rates_total: sum("rates_total"), rates_occupied: sum("rates_occupied"), rates_open: sum("rates_open"), unitCount: breakdown.length, breakdown };
}

/**
 * Дерево подразделений без повторов. Одна должность (T41) — одна строка: если она есть в нескольких подразделениях, строка стоит в их
 * ближайшем общем родителе (в разных верхних ветках общего родителя нет — такие не объединяются). Подразделения без строк и без детей со
 * строками скрываются; у каждого: depth, count (строк в нём и ниже).
 */
export function buildTree(units) {
  const list = Array.isArray(units) ? units : [];
  const nodes = new Map(list.map((u) => [u.id, { id: u.id, title: u.title, order: u.order, positions: [], children: [], depth: 0, count: 0 }]));
  const own = new Map(list.map((u) => [u.id, u.positions || []]));
  const byId = new Map(list.map((u) => [u.id, u]));
  const parentOf = (u) => (u.parent_id !== null && u.parent_id !== undefined && nodes.has(u.parent_id) ? u.parent_id : null);
  const inLoop = (u) => {   // родитель за родителем приводит обратно к самому себе: в данных цикл, такой узел делаем верхним
    let id = parentOf(u);
    for (let steps = 0; id !== null && steps <= list.length; steps += 1) {
      if (id === u.id) return true;
      id = parentOf(byId.get(id));
    }
    return false;
  };
  const roots = [];
  const parent = new Map();   // узел → родитель (в самом узле ссылки на родителя нет: дерево остаётся без кругов)
  for (const u of list) {
    const p = parentOf(u);
    if (p === null || inLoop(u)) {
      roots.push(nodes.get(u.id));
    } else {
      nodes.get(p).children.push(nodes.get(u.id));
      parent.set(nodes.get(u.id), nodes.get(p));
    }
  }
  const ordered = [];   // подразделения в порядке дерева (родитель раньше детей)
  const depthFirst = (node, depth) => {
    node.depth = depth;
    ordered.push(node);
    node.children.forEach((child) => depthFirst(child, depth + 1));
  };
  roots.forEach((root) => depthFirst(root, 0));
  const pathTo = (node) => {
    const path = [];
    for (let at = node; at; at = parent.get(at)) path.unshift(at);
    return path;
  };

  // одна должность — одна строка в пределах верхней ветки
  const groups = new Map();
  for (const node of ordered) {
    const root = pathTo(node)[0];
    for (const p of own.get(node.id)) {
      const key = positionKey(p);
      const slot = `${key}@${root.id}`;
      if (!groups.has(slot)) groups.set(slot, { key, entries: [] });
      groups.get(slot).entries.push({ node, p });
    }
  }
  for (const { key, entries } of groups.values()) {
    const paths = entries.map((entry) => pathTo(entry.node));
    let common = 0;   // длина общего начала путей от корня: последний общий узел — ближайший общий родитель
    while (paths.every((path) => path[common] && path[common] === paths[0][common])) common += 1;
    paths[0][common - 1].positions.push(mergeRow(entries, key));
  }

  const finish = (items) => items.map((node) => {
    node.children = finish(node.children);
    node.count = node.positions.length + node.children.reduce((sum, child) => sum + child.count, 0);
    return node;
  }).filter((node) => node.count > 0);
  return finish(roots);
}

function walk(forest, visit) {
  for (const node of forest) {
    visit(node);
    walk(node.children, visit);
  }
}

export function allUnitIds(forest) {
  const ids = [];
  walk(forest, (node) => ids.push(node.id));
  return ids;
}

const noCriteria = (position) => !position.profile || position.profile.state === "empty";

export function treeCounts(forest) {
  const counts = { positions: 0, noCriteria: 0 };
  walk(forest, (node) => {
    counts.positions += node.positions.length;
    counts.noCriteria += node.positions.filter(noCriteria).length;
  });
  return counts;
}

export function orgHeading({ positions, noCriteria: empty }) {
  return `База должностей: ${positions} ${plural(positions, "должность", "должности", "должностей")}, без критериев ${empty}`;
}

// ------------------------------------------------------------------ поиск и фильтр

/** Верхние подразделения — для выпадающего списка фильтра. */
export const rootOptions = (forest) => forest.map((node) => ({ id: node.id, title: node.title }));

/** Строка находится по названию, названию в 1С любой из позиций и названию подразделения из разбивки (само подразделение могло опустеть и исчезнуть). */
const matches = (p, needle) => norm(p.title).includes(needle) || norm(p.title_1c).includes(needle)
  || (p.breakdown || []).some((part) => norm(part.unitTitle).includes(needle));

function narrow(node, needle, keepAll) {
  const own = keepAll || norm(node.title).includes(needle);   // подразделение нашлось по названию — остаётся всё, что в нём
  const positions = own ? node.positions : node.positions.filter((p) => matches(p, needle));
  const children = node.children.map((child) => narrow(child, needle, own)).filter(Boolean);
  if (!positions.length && !children.length) return null;
  return { ...node, positions, children, count: positions.length + children.reduce((sum, child) => sum + child.count, 0) };
}

/** Поиск по названию должности (и названию в 1С) или подразделения; фильтр — верхнее подразделение. Родители найденного остаются. */
export function filterTree(forest, { q = "", deptId = "" } = {}) {
  const roots = deptId === "" || deptId === null || deptId === undefined ? forest : forest.filter((node) => String(node.id) === String(deptId));
  const needle = norm(q);
  return needle ? roots.map((node) => narrow(node, needle, false)).filter(Boolean) : roots;
}

// ------------------------------------------------------------------ строка должности

const rate = (n) => String(Number((Number(n) || 0).toFixed(2))).replace(".", ",");

/** «ставки: занято 5 из 7, свободно 2»; у должности из нескольких подразделений — суммы и «· в 8 подразделениях». */
export function rateText(position) {
  if (position.rates_total === undefined || position.rates_total === null) return "";
  const open = Number(position.rates_open) || 0;
  const base = `ставки: занято ${rate(position.rates_occupied)} из ${rate(position.rates_total)}, ${open > 0 ? `свободно ${rate(open)}` : "свободных нет"}`;
  const n = position.unitCount;
  return n > 1 ? `${base} · в ${n} ${plural(n, "подразделении", "подразделениях", "подразделениях")}` : base;
}

/** Разбивка по подразделениям в порядке дерева: «Отдел продаж №3 — занято 5 из 7». Одно подразделение — разбивки нет. */
export const breakdownLines = (position) => (position.unitCount > 1
  ? position.breakdown.map((part) => `${part.unitTitle} — занято ${rate(part.rates_occupied)} из ${rate(part.rates_total)}`) : []);

/** Бейдж портрета у должности; «критерии не заполнены» и «не привязан» — тёплым. */
export function positionState(position) {
  return position.profile ? stateInfo(position.profile.state) : { text: "портрет не привязан", pill: "warm" };
}

export const positionLink = (position) => (position.profile ? position.profile.id : null);
/** «Загрузить резюме HH.ru» — только у должности с утверждённым портретом. */
export const canLoadPosition = (position) => Boolean(position.profile && position.profile.state === "approved");

// ------------------------------------------------------------------ окно «Обновить из 1С»

export function orgResultText(reply) {
  if (reply && reply.state === "same") return "Эта выгрузка уже применена";
  const r = reply || {};
  return `Оргструктура обновлена: подразделений ${r.units ?? 0}, должностей ${r.positions ?? 0}, изменений ${r.changes ?? 0}, новых портретов ${r.new_profiles ?? 0}`;
}

export function syncText(sync, now = new Date()) {
  if (!sync) return "Выгрузок из 1С ещё не было";
  return `Последняя выгрузка: ${whenLabel(sync.received_at, now)} · ${sync.file_name} · подразделений ${sync.units}, должностей ${sync.positions}`;
}

export function orgFileError(file) {
  if (!file) return "Выберите файл";
  if (!/\.json$/i.test(file.name || "")) return "Нужен файл .json";
  if (!file.size) return "Файл пустой";
  return file.size > MAX_ORG_BYTES ? "Файл больше 5 МБ" : null;
}
