// «Загрузить резюме» без DOM (S44): остаток просмотров hh и пакета, проверка числа «сколько», ожидание запущенного прогона.
// Данные — GET /api/hh/limits → {views_left, package_left}; запуск — POST /api/hh/collect {count} (сервер сам проверяет 1…остаток).
import { numText, plural } from "./format.js";

export const DAILY_LIMIT = 500;   // дневной лимит просмотров hh (замер 2026-10-29): завтра остаток снова такой
export const EXPLAIN = "Поиск бесплатный; полное резюме тратит один просмотр из дневного лимита hh — тот же лимит, что на сайте. "
  + "Контакты не открываются. Каждый день в 18:00 по Москве остаток выбирается автоматически.";
export const STARTED_TOAST = "Загрузка запущена";
export const EXPECT_MS = 20000;   // сколько «Схема» ждёт появления нового прогона после запуска

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

// ------------------------------------------------------------------ после запуска

let expecting = null;   // {job, until}: экран «Схема» после запуска ждёт, пока прогон появится в ops.job_run

/** Запомнить, что запустили сбор: «Схема» (в том числе после перехода с «Базы») будет ждать новый прогон. */
export function expectCollect(now = Date.now()) {
  expecting = { job: "collect_hh", until: now + EXPECT_MS };
}

/** Забрать ожидание (один раз); устаревшее не возвращается. */
export function takeExpectation(now = Date.now()) {
  const found = expecting && expecting.until >= now ? expecting : null;
  expecting = null;
  return found;
}
