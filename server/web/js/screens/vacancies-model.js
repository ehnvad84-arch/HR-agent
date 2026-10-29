// «Вакансии» без DOM (S21–S23, S38): тексты карточек, подсказки окна закрытия, тело запроса новой вакансии.
// Тексты — из прототипа (VACS, REASONS.closeVacancy, SHEET_HINTS.closeVacancy).
import { absoluteDay, plural } from "../format.js";

export const CLOSE_REASONS = ["человек вышел на работу", "потребность закрыта внутренним переводом", "заявка отозвана заказчиком",
  "ставка заморожена бюджетом", "требования изменились, нужен другой профиль"];
export const CLOSE_HINT = "Опишите, чем закрыта потребность и что стоит учесть в следующий раз по этой должности. "
  + "Важно: закрытие вакансии не останавливает подбор — профиль должности остаётся активным, наблюдение по должности продолжится, "
  + "агент продолжает копить подходящих в базу знаний на будущее.";
const CONTINUES = "профиль активен, наблюдение не остановлено.";

export const closeResult = (length) => `Вакансия закрыта, комментарий на ${length} знаков записан. Подбор по должности продолжается: ${CONTINUES}`;
export const CLOSE_TOAST = "Вакансия закрыта, подбор по позиции продолжается";
export const REOPEN_TOAST = "Вакансия открыта заново, наблюдение уже велось";

const ORIGINS = { recruiter: "завёл рекрутер", request_1c: "заявка из 1С", hh_publication: "публикация на hh" };
export const originText = (origin) => ORIGINS[origin] || String(origin ?? "");

/** Дата «2026-11-15» без времени как местная дата (а не полночь по UTC). */
function dateOnly(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value ?? ""));
  return match ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3])) : null;
}

export function daysBetween(from, to) {
  const a = dateOnly(from);
  const b = dateOnly(to);
  return a && b ? Math.round((Date.UTC(b.getFullYear(), b.getMonth(), b.getDate()) - Date.UTC(a.getFullYear(), a.getMonth(), a.getDate())) / 86400000) : null;
}

export const vacancyTitle = (v) => `${v.title} · ${v.headcount} ${plural(v.headcount, "ставка", "ставки", "ставок")}`;

/** Вакансия «голодает»: открыта дольше нормы. */
export const isStarving = (v) => typeof v.norm_days === "number" && v.open_days > v.norm_days;

export function openText(v, now = new Date()) {
  const days = `${v.open_days} ${plural(v.open_days, "день", "дня", "дней")}`;
  const parts = [`Открыта ${days}${v.norm_days ? ` из ${v.norm_days}` : ""}`,
    `в работе ${v.in_work ?? 0} ${plural(v.in_work ?? 0, "человек", "человека", "человек")}`];
  const target = dateOnly(v.target_date);
  if (target) parts.push(`срок до ${absoluteDay(target, now)}`);
  parts.push(`источник: ${originText(v.origin)}`);
  return parts.join(" · ");
}

export function closedText(v, now = new Date()) {
  const closed = dateOnly(v.closed_at);
  const days = daysBetween(v.opened_at, v.closed_at);
  const head = closed ? `Закрыта ${absoluteDay(closed, now)}${days === null ? "" : `, была открыта ${days} ${plural(days, "день", "дня", "дней")}`}` : "Закрыта";
  return `${head}. Подбор по позиции продолжается: ${CONTINUES}`;
}

/** Утверждённые профили для «Новой вакансии»: по подразделению и названию. */
export function profileChoices(items) {
  return (items || []).filter((p) => p.state === "approved")
    .sort((a, b) => String(a.dept).localeCompare(String(b.dept), "ru") || String(a.title).localeCompare(String(b.title), "ru"))
    .map((p) => ({ id: p.id, title: p.title, label: p.dept ? `${p.title} — ${p.dept}` : p.title }));
}

/** Поля формы → тело POST /api/vacancy; ошибка заполнения — текстом по-русски. */
export function buildVacancy({ profileId, title, headcount, targetDate }) {
  if (!/^\d+$/.test(String(profileId ?? ""))) return { ok: false, error: "Выберите профиль должности" };
  const name = String(title ?? "").trim();
  if (!name) return { ok: false, error: "Введите название вакансии" };
  if (!/^\d+$/.test(String(headcount ?? "")) || Number(headcount) < 1) return { ok: false, error: "Ставок должно быть не меньше одной" };
  const payload = { role_profile_id: Number(profileId), title: name, headcount: Number(headcount) };
  if (targetDate) payload.target_date = targetDate;
  return { ok: true, payload };
}
