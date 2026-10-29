// Подписи и даты для экранов. Без DOM: проверяется в tests/js. Названия этапов — как в hr/funnel.py (STAGES).

export function plural(n, one, few, many) {
  const mod100 = Math.abs(n) % 100;
  const mod10 = mod100 % 10;
  if (mod100 > 10 && mod100 < 20) return many;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
}

export const capitalize = (text) => (text ? text[0].toUpperCase() + text.slice(1) : text);

// ------------------------------------------------------------------ даты (часовой пояс браузера)

const toDate = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
};
const dayStart = (date) => new Date(date.getFullYear(), date.getMonth(), date.getDate());
const dayDiff = (date, now) => Math.round((dayStart(date) - dayStart(now)) / 86400000);

/** «3 ноября»; с годом, если год другой. */
export function absoluteDay(value, now = new Date()) {
  const date = toDate(value);
  if (!date) return "";
  const text = date.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  return date.getFullYear() === now.getFullYear() ? text : `${text} ${date.getFullYear()}`;
}

/** «сегодня», «завтра», «вчера» или «3 ноября» (с годом, если год другой). */
export function dayLabel(value, now = new Date()) {
  const date = toDate(value);
  if (!date) return "";
  const diff = dayDiff(date, now);
  if (diff === 0) return "сегодня";
  if (diff === 1) return "завтра";
  if (diff === -1) return "вчера";
  return absoluteDay(date, now);
}

/** «октябрь 2026». */
export function monthYear(value) {
  const date = toDate(value);
  return date ? `${date.toLocaleDateString("ru-RU", { month: "long" })} ${date.getFullYear()}` : "";
}

/** «29 октября, 13:05» — для ленты изменений и истории решений. */
export function dateTimeLabel(value, now = new Date()) {
  const date = toDate(value);
  return date ? `${absoluteDay(date, now)}, ${timeLabel(date)}` : "";
}

/** «15:00». */
export function timeLabel(value) {
  const date = toDate(value);
  return date ? date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }) : "";
}

/** «сегодня, 15:00»; не дата — пустая строка. */
export function whenLabel(value, now = new Date()) {
  const date = toDate(value);
  return date ? `${dayLabel(date, now)}, ${timeLabel(date)}` : "";
}

// ------------------------------------------------------------------ числа

/** «190 000» (обычные пробелы: строка переносится как текст). */
export const numText = (n) => (typeof n === "number" ? n.toLocaleString("ru-RU").replace(/[\u00a0\u202f]/g, " ") : "");
export const moneyText = (n) => (typeof n === "number" ? `${numText(n)} ₽` : "");

/** «9 лет», «1 год», «21,8 года». */
export function yearsText(n) {
  if (typeof n !== "number") return "";
  return Number.isInteger(n) ? `${n} ${plural(n, "год", "года", "лет")}` : `${String(n).replace(".", ",")} года`;
}

// ------------------------------------------------------------------ названия

const STATUS_TITLES = {
  new: "Новый", picked_by_agent: "Отобран агентом", first_message_sent: "Написали", replied: "Ответил",
  refused_contact: "Отказался от общения", phone_interview: "Телефонное интервью", manager_interview: "Встреча с руководителем",
  test_task: "Тестовое задание", security_check: "Проверка СБ", offer: "Оффер", hired: "Принят", no_show: "Не пришёл",
  reserve: "Резерв", rejected_by_us: "Отказ с нашей стороны", rejected_by_candidate: "Отказ кандидата",
};
const ACTION_TITLES = {
  first_message: "Первое сообщение", follow_up: "Напоминание", invite_interview: "Приглашение на интервью",
  send_test_task: "Тестовое задание", reject_notice: "Сообщение об отказе", move_to_reserve: "Перевод в резерв",
  merge_persons: "Склеить два профиля", open_contact: "Открыть контакт",
};
const INTERVIEW_TITLES = {
  phone: "Телефонное интервью", recruiter: "Встреча с рекрутером", manager: "Встреча с руководителем", tech_case: "Технический кейс",
};
const ALERT_REASONS = {
  new_high_score: { title: "новый, высокий балл", tone: "g" },
  event_on_pool: { title: "событие по резюме", tone: "b" },
  score_grew: { title: "балл вырос", tone: "g" },
  donor_company: { title: "опыт в компании-доноре", tone: "v" },
  vacancy_starving: { title: "вакансия без кандидатов", tone: "o" },
};

export const statusTitle = (code) => STATUS_TITLES[code] || String(code ?? "");
export const actionTitle = (kind) => ACTION_TITLES[kind] || "Действие";
export const interviewTitle = (kind) => INTERVIEW_TITLES[kind] || "Встреча";
export const alertReason = (reason) => ALERT_REASONS[reason] || { title: "сигнал", tone: "" };

/** «Написали · последний контакт 6 дней назад». */
export function silentText(status, days) {
  const title = statusTitle(status);
  if (days === null || days === undefined) return title;
  return `${title} · последний контакт ${days} ${plural(days, "день", "дня", "дней")} назад`;
}

/** Цвет значка балла: ниже 75 — оранжевый. Это подсказка глазу, не порог решения (пороги — в settings на сервере). */
export const scoreBand = (score) => (typeof score === "number" && score >= 75 ? "" : "mid");

// ------------------------------------------------------------------ «Сегодня»

export const isEmptyDay = (data) =>
  !(data.signals.length || data.actions.length || data.stalled.length || data.meetings.length);

/** Число на вкладке: сигналы и подтверждения — то, что ждёт решения. */
export const badgeCount = (data) => data.signals.length + data.actions.length;
