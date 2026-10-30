// Оргструктура 1С в «Базе должностей» без DOM (S47): дерево подразделений, скрытие пустых, поиск и фильтр, счётчики, ставки, тексты окна.
// Данные — GET /api/org: {sync, units: [{id, parent_id, title, order, positions: [{id, title, title_1c, rates_*, profile}]}]},
// плоский список активных подразделений в порядке 1С; дерево строится здесь по parent_id.
import { plural, whenLabel } from "../format.js";
import { stateInfo } from "./profiles-model.js";

export const MAX_ORG_BYTES = 5 * 1024 * 1024;

const norm = (text) => String(text ?? "").trim().toLowerCase();

// ------------------------------------------------------------------ дерево

/** Подразделения без должностей и без детей с должностями скрываются; у каждого: depth, count (должностей в нём и ниже). */
export function buildTree(units) {
  const list = Array.isArray(units) ? units : [];
  const nodes = new Map(list.map((u) => [u.id, { id: u.id, title: u.title, order: u.order, positions: u.positions || [], children: [], depth: 0, count: 0 }]));
  const parentOf = (u) => (u.parent_id !== null && u.parent_id !== undefined && nodes.has(u.parent_id) ? u.parent_id : null);
  const inLoop = (u) => {   // родитель за родителем приводит обратно к самому себе: в данных цикл, такой узел делаем верхним
    let id = parentOf(u);
    for (let steps = 0; id !== null && steps <= list.length; steps += 1) {
      if (id === u.id) return true;
      id = parentOf(list.find((x) => x.id === id));
    }
    return false;
  };
  const roots = [];
  for (const u of list) {
    const parent = parentOf(u);
    if (parent === null || inLoop(u)) roots.push(nodes.get(u.id));
    else nodes.get(parent).children.push(nodes.get(u.id));
  }
  const finish = (items, depth) => items.map((node) => {
    node.depth = depth;
    node.children = finish(node.children, depth + 1);
    node.count = node.positions.length + node.children.reduce((sum, child) => sum + child.count, 0);
    return node;
  }).filter((node) => node.count > 0);
  return finish(roots, 0);
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

function narrow(node, needle, keepAll) {
  const own = keepAll || norm(node.title).includes(needle);   // подразделение нашлось по названию — остаётся всё, что в нём
  const positions = own ? node.positions : node.positions.filter((p) => norm(p.title).includes(needle) || norm(p.title_1c).includes(needle));
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

/** «ставки: занято 5 из 7, свободно 2». */
export function rateText(position) {
  if (position.rates_total === undefined || position.rates_total === null) return "";
  const open = Number(position.rates_open) || 0;
  return `ставки: занято ${rate(position.rates_occupied)} из ${rate(position.rates_total)}, ${open > 0 ? `свободно ${rate(open)}` : "свободных нет"}`;
}

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
