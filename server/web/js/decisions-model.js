// Решения по человеку без DOM: счётчик знаков, подсказки-причины, выбор вакансии (S11–S13).
// Тексты — из прототипа (REASONS, SHEET_HINTS, saveSheet).

/** Порог комментария по умолчанию (settings.decision_comment_min). Уточняется из ответа сервера, см. minFromError. */
export const DEFAULT_MIN = 200;

export const REASONS = {
  postpone: ["недавно сменил работу", "ждём открытия ставки", "высокие ожидания сейчас", "вернуться после релокации"],
  reject: ["нет профильного опыта", "частая смена работы", "не подходит география", "ожидания выше вилки", "опыт из другой отрасли"],
};
export const TITLES = { postpone: "Временно отложить", reject: "Категорически нет" };
export const HINTS = {
  postpone: "Напоминание через три месяца, наблюдение продолжается. Опишите, почему откладываем и что должно измениться, чтобы вернуться к человеку.",
  reject: "Человек уходит из выдачи, причина пойдёт в настройку весов профиля. Опишите, что именно не подошло: по этому тексту агент учится не носить таких же.",
};
export const PLACEHOLDER = "Что именно не подошло, на что смотрели, что стоит учесть агенту в следующий раз. Чем конкретнее, тем быстрее агент перестанет носить таких же.";
export const TOASTS = { postpone: "Отложено, наблюдение продолжается", reject: "Решение записано, агент учтёт причину" };
export const ALL_PROFILES = "По всем профилям — человек уйдёт из выдачи совсем";

/** Знаки комментария: без пробелов по краям, по символам (как len() на сервере), а не по кодовым единицам UTF-16. */
export const countChars = (text) => [...String(text ?? "").trim()].length;
export const canSave = (text, min) => countChars(text) >= min;
export const counterText = (text, min) => `${countChars(text)} из ${min} символов`;

/**
 * Сервер строже клиента: «Комментарий не короче 150 знаков» (решение по человеку, 422 comment_too_short) или
 * «Проверьте: комментарий не короче 150 знаков» (закрытие вакансии, 422 validation) — берём число оттуда.
 */
export function minFromError(error, fallback) {
  const tooShort = error && (error.status === 422 || error.code === "comment_too_short");
  if (!tooShort) return fallback;
  const match = /не короче\s+(\d+)\s*знаков/i.exec(error.message || "");
  return match ? Number(match[1]) : fallback;
}

/** Подсказка-причина → фраза в конце текста: «Нет профильного опыта. ». */
export function appendReason(text, reason) {
  const base = String(text ?? "").trim();
  return `${base ? `${base} ` : ""}${reason.charAt(0).toUpperCase()}${reason.slice(1)}. `;
}

export const resultText = (kind, length) =>
  `${kind === "reject" ? "Помечен «категорически нет»" : "Отложен на три месяца"}, комментарий на ${length} символов записан и отправлен агенту`;

/**
 * Куда можно поставить человека при «Сохранить» (в кандидаты): открытые вакансии по кодам его профилей. Порядок — как у сервера
 * (hr/decisions.py:_open_vacancy): сначала код с лучшим баллом, внутри — давно открытые. Вакансия «на паузе» не годится.
 */
export function pickVacancies(open, matches) {
  const best = new Map();
  for (const m of matches || []) best.set(m.role_code, Math.max(best.get(m.role_code) ?? -Infinity, m.score ?? 0));
  return (open || [])
    .filter((v) => v.state === "open" && best.has(v.role_code))
    .sort((a, b) => best.get(b.role_code) - best.get(a.role_code)
      || (a.opened_at < b.opened_at ? -1 : a.opened_at > b.opened_at ? 1 : 0) || a.id - b.id);
}

export const takenText = (vacancy) => (vacancy
  ? `Взят в работу: воронка «${vacancy.title}», этап «Отобран агентом». Контакт не открыт`
  : "Взят в работу в резерв: открытой вакансии по профилю нет, привязать к вакансии можно позже. Контакт не открыт");

/** Профили для окна решения: лучшие сверху; для «категорически нет» ещё «по всем профилям» (role_profile_id: null). */
export function profileOptions(matches, kind) {
  const list = [...(matches || [])].sort((a, b) => (b.score ?? -1) - (a.score ?? -1))
    .map((m) => ({ id: m.role_profile_id, label: `«${m.role_title}»${typeof m.score === "number" ? ` — балл ${m.score}` : ""}` }));
  return kind === "reject" ? [...list, { id: null, label: ALL_PROFILES }] : list;
}
