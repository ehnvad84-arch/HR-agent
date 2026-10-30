// «База» без DOM: состояние экрана в адресе, запрос к API, сортировка, страницы (S19, S20, S22).
// Состояние живёт в хэше (#/base?city=Томск&sort=city&dir=asc): ссылку можно скопировать, F5 её сохраняет.

export const COLUMNS = [
  { key: "name", title: "ФИО", sort: "name", type: "text" },
  { key: "city", title: "Город", sort: "city", type: "text" },
  { key: "role", title: "Должность", sort: "role", type: "text" },
  { key: "dept", title: "Подразделение", sort: "dept", type: "text" },
  { key: "score", title: "Балл", sort: "score", type: "num" },
  { key: "where", title: "Где числится", sort: "where", type: "text" },
  { key: "source", title: "Источник", sort: "source", type: "text" },
  { key: "socn", title: "Профилей в сети", sort: "socn", type: "num" },
  { key: "upd", title: "Последнее изменение", sort: "upd", type: "date" },
];

export const MY_COLUMN = { key: "my", title: "Мой балл", sort: "my", type: "num" };

/** Колонки таблицы: «Мой балл» после «Балла» — если есть личные поправки; «Передать» в конце — у руководителя (право handoff.create). */
export function tableColumns({ hasMy = false, handoff = false } = {}) {
  const columns = COLUMNS.flatMap((c) => (c.key === "score" && hasMy ? [c, MY_COLUMN] : [c]));
  return handoff ? [...columns, { key: "act", title: "Передать", sort: null, type: "action" }] : columns;
}

export const WHERE_VALUES = ["observed", "watched", "dropped", "candidate"];
const WHERE_TITLES = { observed: "наблюдение", watched: "ведётся особо", dropped: "категорически нет", candidate: "кандидат" };
const SOURCE_TITLES = { hh: "hh.ru", trudvsem: "Работа России", habr: "Хабр Карьера", avito: "Авито Работа", manual: "Ручной ввод" };
export const FILTER_KEYS = ["dept", "city", "role", "score_min", "where", "source"];
const SORT_KEYS = [...COLUMNS.map((c) => c.sort), MY_COLUMN.sort];

/** Адрес «Подходящие кандидаты» из «Базы должностей»: код портрета, по баллу этой должности, лучшие сверху. */
export const fitQuery = (code) => ({ fit: code, sort: "score", dir: "desc" });
/** «Подходящие кандидаты» работает только у должности с действующим (утверждённым) портретом: баллы есть лишь по нему. */
export const canShowFit = (state) => state === "approved";
/**
 * Кнопка видна у каждой должности. Рабочая — у утверждённого портрета; у пустого, ждущего утверждения и черновика неактивна
 * (баллов ещё нет); у должности без портрета (state пуст) — тоже неактивна. tip — ключ подсказки в tips.js.
 */
export function fitAvailability(state) {
  if (state === undefined || state === null) return { active: false, tip: "fit-button-no-profile" };
  return canShowFit(state) ? { active: true, tip: "fit-button" } : { active: false, tip: "fit-button-no-scores" };
}
/** Подпись колонки: у балла при выбранной должности — «Балл по должности». */
export const columnTitle = (column, state) => (column.key === "score" && state.fit ? "Балл по должности" : column.title);

/** Название должности для плашки: из действующих профилей по коду, иначе из строки выдачи, иначе сам код. */
export function fitRoleTitle(code, profiles, items) {
  const profile = (profiles || []).find((p) => p.code === code);
  if (profile && profile.title) return profile.title;
  const row = (items || []).find((r) => r.role_code === code && r.role_title);
  return row ? row.role_title : code;
}
export const fitBarText = (title) => `Подходящие под «${title}»: по баллу этой должности, лучшие сверху`;
export const fitEmptyText = (canLoad) => (canLoad
  ? "Подходящих под эту должность пока нет — загрузите резюме кнопкой «Загрузить резюме HH.ru»" : "Подходящих под эту должность пока нет");

export const whereTitle = (where) => WHERE_TITLES[where] || String(where ?? "");
export const sourceTitle = (code) => SOURCE_TITLES[code] || String(code ?? "");
export const similarityText = (value) => (typeof value === "number" ? `близость ${Math.round(value * 100)} %` : "");

const text = (value, limit = 200) => String(value ?? "").trim().slice(0, limit);

/** Адрес → состояние. Всё, что не похоже на допустимое, заменяется умолчанием: до API мусор не доходит. */
export function parseState(query = {}) {
  const sort = SORT_KEYS.includes(query.sort) ? query.sort : "score";
  const dir = query.dir === "asc" || query.dir === "desc" ? query.dir : "desc";
  return {
    dept: text(query.dept, 100),
    city: text(query.city, 100),
    role: text(query.role, 100),
    score_min: /^\d{1,3}$/.test(query.score_min ?? "") ? query.score_min : "",
    where: WHERE_VALUES.includes(query.where) ? query.where : "",
    source: /^[\w-]{1,40}$/.test(query.source ?? "") ? query.source : "",
    sort,
    dir,
    page: /^\d+$/.test(query.page ?? "") ? Number(query.page) : 0,
    q: text(query.q),
    vacancy: /^\d+$/.test(query.vacancy ?? "") ? query.vacancy : "",
    fit: /^[\w.-]{1,64}$/.test(query.fit ?? "") ? query.fit : "",   // код портрета из «Базы должностей»: подходящие под одну должность (S49)
  };
}

/** Состояние → query адреса: только изменённое, умолчания не пишутся. */
export function stateToQuery(state) {
  const query = {};
  for (const key of FILTER_KEYS) if (state[key] !== "") query[key] = state[key];
  if (state.sort !== "score" || state.dir !== "desc") {
    query.sort = state.sort;
    query.dir = state.dir;
  }
  if (state.page > 0) query.page = state.page;
  if (state.q) query.q = state.q;
  if (state.vacancy) query.vacancy = state.vacancy;
  if (state.fit) query.fit = state.fit;
  return query;
}

const clean = (query) => Object.fromEntries(Object.entries(query).filter(([, v]) => v !== "" && v !== undefined && v !== null));

/**
 * Что запросить у API. Поиск по смыслу (q) — отдельный запрос со списком по близости; вакансия — «подходящие» (город,
 * балл, сортировка, страница: остальных фильтров у сервера для вакансии нет); иначе — вся база с фильтрами.
 */
export function apiRequest(state) {
  const q = state.q.trim();
  if (q) return { kind: "search", path: "/api/search", query: { q } };
  const order = { sort: state.sort, dir: state.dir, page: state.page };
  if (state.vacancy) {
    return { kind: "table", path: `/api/vacancy/${state.vacancy}/matching`,
      query: clean({ city: state.city, score_min: state.score_min, ...order }) };
  }
  // fit — балл именно по этой должности: фильтр «Должность» (по лучшему баллу) с ним не сочетается и не уходит
  return { kind: "table", path: "/api/base", query: clean({
    fit: state.fit, dept: state.dept, city: state.city, role: state.fit ? "" : state.role, score_min: state.score_min, where: state.where, source: state.source, ...order }) };
}

/** Клик по заголовку: новая колонка — числа и даты вниз, текст вверх; повторный клик — в обратную сторону. */
export function nextSort(state, sortKey) {
  if (state.sort === sortKey) return { sort: sortKey, dir: state.dir === "asc" ? "desc" : "asc", page: 0 };
  const column = COLUMNS.find((c) => c.sort === sortKey);
  return { sort: sortKey, dir: column && column.type === "text" ? "asc" : "desc", page: 0 };
}

export const ariaSort = (state, sortKey) => (state.sort !== sortKey ? "none" : state.dir === "asc" ? "ascending" : "descending");

export const filtersActive = (state) => FILTER_KEYS.some((key) => state[key] !== "");

/** «Показано 50 из 55» и положение среди страниц. */
export function pageInfo({ total, page, page_size: size, items }) {
  const pages = size > 0 ? Math.max(1, Math.ceil(total / size)) : 1;
  const shown = (items || []).length;
  return { shown, total, text: `Показано ${shown} из ${total}`, page, pages, hasPrev: page > 0, hasNext: page + 1 < pages };
}
