// «Загрузить резюме» без DOM (S44): остаток просмотров hh и пакета, проверка числа «сколько», ожидание запущенного прогона.
// Данные — GET /api/hh/limits → {views_left, package_left}; запуск — POST /api/hh/collect {count} (сервер сам проверяет 1…остаток).
import { numText, plural } from "./format.js";

export const DAILY_LIMIT = 500;   // дневной лимит просмотров hh (замер 2026-10-29): завтра остаток снова такой
export const EXPLAIN = "Поиск бесплатный; полное резюме тратит один просмотр из дневного лимита hh — тот же лимит, что на сайте. "
  + "Контакты не открываются. Каждый день в 18:00 по Москве остаток выбирается автоматически.";
export const STARTED_TOAST = "Загрузка запущена";

const isNumber = (value) => typeof value === "number" && Number.isFinite(value);

/** Ответ сервера → {left: сколько просмотров осталось, packageLeft: остаток пакета API или null}. */
export function normalizeLimits(reply) {
  const r = reply && typeof reply === "object" ? reply : {};
  return { left: isNumber(r.views_left) ? Math.max(0, Math.floor(r.views_left)) : 0, packageLeft: isNumber(r.package_left) ? r.package_left : null };
}

export function limitsText({ left, packageLeft }) {
  const pack = packageLeft === null || packageLeft === undefined ? "нет данных" : numText(packageLeft);
  return `Сегодня можно загрузить ещё ${numText(left)} ${plural(left, "полное резюме", "полных резюме", "полных резюме")} `
    + `(дневной лимит просмотров hh). Пакет API: ${pack}`;
}

export const exhaustedText = () => `Сегодня лимит исчерпан, завтра снова ${DAILY_LIMIT}`;

/** Целое число из поля или null (пусто, дробь, знак, буквы, пробелы внутри). */
export function parseCount(text) {
  const clean = String(text ?? "").trim();
  return /^\d+$/.test(clean) ? Number(clean) : null;
}

/** "" — можно отправлять; иначе текст для поля. */
export function countError(text, left) {
  const n = parseCount(text);
  return n !== null && n >= 1 && n <= left ? "" : `Введите целое число от 1 до ${numText(left)}`;
}

export const canStart = (text, left) => left >= 1 && countError(text, left) === "";
export const defaultCount = (left) => (left >= 1 ? String(left) : "");

// ------------------------------------------------------------------ по должности и региону (S46)

export const ROLE_EXPLAIN = "Поиск только по этой должности в выбранном регионе. Балл считается по всем портретам.";
export const roleSheetTitle = (title) => `Загрузить резюме HH.ru: ${title}`;
export const areaLine = (area) => `Регион: ${area.name}`;
export const AREA_DEBOUNCE_MS = 300;

/** Быстрый выбор региона (то же отдаёт GET /api/hh/areas без запроса); запасной список, если hh или сервер не ответили. */
export const FALLBACK_AREAS = [{ id: "113", name: "Вся Россия" }, { id: "1438", name: "Краснодарский край" }, { id: "53", name: "Краснодар" }];
const AREA_BY_GEO = { krasnodar: FALLBACK_AREAS[2], remote_ru: FALLBACK_AREAS[0], relocation_ru: FALLBACK_AREAS[1] };

/** Регион по умолчанию — из портрета: Краснодар, вся Россия (удалённо), Краснодарский край (переезд). */
export const areaForGeo = (geo) => ({ ...(AREA_BY_GEO[geo] || FALLBACK_AREAS[2]) });

/** Ответ /api/hh/areas → [{id, name}] без мусора и повторов, не больше десяти. */
export function normalizeAreas(reply) {
  const seen = new Set();
  const out = [];
  for (const item of (reply && Array.isArray(reply.items) ? reply.items : [])) {
    const id = item && item.id !== undefined && item.id !== null ? String(item.id) : "";
    const name = item && typeof item.name === "string" ? item.name.trim() : "";
    if (!/^\d+$/.test(id) || !name || seen.has(id)) continue;
    seen.add(id);
    out.push({ id, name });
  }
  return out.slice(0, 10);
}

/** Подсказки hh по названию — от двух букв. */
export const canSearchAreas = (text) => String(text ?? "").trim().length >= 2;

/** Стрелки в списке подсказок: индекс остаётся в пределах списка (-1 — ничего не выбрано). */
export function moveHighlight(index, delta, length) {
  if (length <= 0) return -1;
  return Math.min(length - 1, Math.max(0, index + delta));
}

/** Тело POST /api/hh/collect: число, а для загрузки по должности ещё портрет и регион. */
export function collectBody({ count, profileId = null, areaId = null }) {
  const body = { count: parseCount(count) };
  if (profileId !== null && profileId !== undefined) {
    body.role_profile_id = profileId;
    if (areaId !== null && areaId !== undefined) body.area_id = String(areaId);
  }
  return body;
}
