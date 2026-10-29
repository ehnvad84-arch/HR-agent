// Адрес экрана хранится в хэше: «Назад» и F5 возвращают на тот же экран.
//   #/today   #/person/<id>   #/base?vacancy=3   #/vacancy/<id>/funnel
// Новый адрес — строка в ROUTES (и вкладка в screens/index.js).

export const TAB_ORDER = ["today", "person", "base", "vacancies", "profiles", "learn", "logs", "funnel", "arch"];
export const DEFAULT_HASH = "#/today";

// ":имя" — обязательная часть, ":имя?" — необязательная (только в конце)
const ROUTES = [
  { name: "today", pattern: ["today"] },
  { name: "person", pattern: ["person", ":id?"] },
  { name: "base", pattern: ["base"] },
  { name: "vacancies", pattern: ["vacancies"] },
  { name: "profiles", pattern: ["profiles"] },
  { name: "learn", pattern: ["learn"] },
  { name: "logs", pattern: ["logs"] },
  { name: "funnel", pattern: ["vacancy", ":id", "funnel"] },
  { name: "funnel", pattern: ["funnel"] },
  { name: "arch", pattern: ["arch"] },
];
const SAFE_ID = /^[\w-]+$/;

function matchPattern(pattern, segments) {
  const params = {};
  for (let i = 0; i < pattern.length; i += 1) {
    const token = pattern[i];
    const segment = segments[i];
    if (token.startsWith(":")) {
      const optional = token.endsWith("?");
      if (segment === undefined) {
        if (optional) continue;
        return null;
      }
      if (!SAFE_ID.test(segment)) return null;
      params[token.slice(1).replace("?", "")] = segment;
    } else if (token !== segment) {
      return null;
    }
  }
  return segments.length > pattern.length ? null : params;
}

/** "#/person/12?x=1" → {name, params, query}; всё непонятное → null. */
export function parseHash(hash) {
  if (typeof hash !== "string" || !hash.startsWith("#/")) return null;
  const [path, search = ""] = hash.slice(2).split("?", 2);
  const segments = path.split("/");
  for (const route of ROUTES) {
    const params = matchPattern(route.pattern, segments);
    if (params) return { name: route.name, params, query: Object.fromEntries(new URLSearchParams(search)) };
  }
  return null;
}

/** Обратное: hashFor("person", {id: 5}) → "#/person/5". */
export function hashFor(name, params = {}, query = {}) {
  for (const route of ROUTES.filter((r) => r.name === name)) {
    const parts = [];
    let ok = true;
    for (const token of route.pattern) {
      if (!token.startsWith(":")) { parts.push(token); continue; }
      const key = token.slice(1).replace("?", "");
      const value = params[key];
      if (value === undefined || value === null || value === "") {
        if (token.endsWith("?")) continue;
        ok = false;
        break;
      }
      parts.push(encodeURIComponent(String(value)));
    }
    if (!ok) continue;
    const search = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== "") search.set(k, String(v));
    const qs = search.toString();
    return `#/${parts.join("/")}${qs ? `?${qs}` : ""}`;
  }
  return DEFAULT_HASH;
}

/**
 * Подписка на смену адреса. onRoute(route) вызывается сразу и после каждой смены хэша.
 * Пустой или неизвестный адрес заменяется на «Сегодня» без лишней записи в истории.
 */
export function startRouter(onRoute) {
  const apply = () => {
    const route = parseHash(window.location.hash);
    if (!route) {
      window.history.replaceState(null, "", DEFAULT_HASH);
      onRoute(parseHash(DEFAULT_HASH));
      return;
    }
    onRoute(route);
  };
  window.addEventListener("hashchange", apply);
  apply();
  return {
    /** Перейти на экран (новая запись в истории) или заменить текущую запись (replace). */
    go(name, params = {}, query = {}, { replace = false } = {}) {
      const hash = hashFor(name, params, query);
      if (replace) {
        window.history.replaceState(null, "", hash);
        apply();
      } else if (window.location.hash === hash) {
        apply();
      } else {
        window.location.hash = hash;
      }
    },
    /** Перерисовать текущий адрес (после повторного входа, выхода-входа другого человека). */
    refresh: apply,
    stop() { window.removeEventListener("hashchange", apply); },
  };
}
