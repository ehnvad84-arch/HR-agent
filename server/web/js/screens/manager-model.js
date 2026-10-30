// Кабинет руководителя без DOM (S5–S8): «Сегодня», вакансии, статусы портретов в «Моих должностях».
import { absoluteDay } from "../format.js";

export const EMPTY_SIGNALS = "Новых людей по вашим должностям нет";
export const EMPTY_VACANCIES = "Открытых вакансий нет. Вакансию заводит отдел персонала";
export const NO_HANDOFFS = "Вы пока никого не передавали";
export const zoneTitles = () => ({ signals: "Новые и изменившиеся — по вашим должностям", vacancies: "Открытые вакансии вашего подразделения", handoffs: "Кого вы передали" });

/** Ответ /api/today руководителя: {signals, vacancies, handoffs}; число на вкладке — новые люди. */
export function normalizeManagerToday(raw) {
  const r = raw || {};
  const signals = r.signals || [];
  return { signals, vacancies: r.vacancies || [], handoffs: r.handoffs || [], badge: signals.length };
}
export const managerBadge = (count) => (count > 0 ? count : "");

const dateOnly = (value) => {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value ?? ""));
  return match ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3])) : null;
};

/** «Ставок: 3 · срок до 15 ноября». */
export function vacancyMeta(v, now = new Date()) {
  const parts = [];
  if (v.headcount !== undefined && v.headcount !== null) parts.push(`Ставок: ${v.headcount}`);
  const target = dateOnly(v.target_date);
  if (target) parts.push(`срок до ${absoluteDay(target, now)}`);
  return parts.join(" · ");
}
export const hrLine = (v) => (v.recruiter ? `Ответственный: ${v.recruiter}` : "Ответственный не назначен");
export const customerLine = (v) => (v.hiring_manager ? `Заказчик: ${v.hiring_manager}` : "");
export const matchingLabel = (count) => `Подходящих: ${count ?? 0}`;

// ------------------------------------------------------------------ «Мои должности»

export const saveLabel = (cabinet) => (cabinet ? "Отправить на утверждение" : "Сохранить как новую версию");
export const myPositionsNote = (shared) => (shared ? "Должность есть и в других подразделениях: правка меняет общий портрет" : "");

const ddmm = (value) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : `${String(date.getDate()).padStart(2, "0")}.${String(date.getMonth() + 1).padStart(2, "0")}`;
};

/** «утверждён» / «ждёт утверждения — правка Сидорова С. П. от 30.10» (автор и дата — если сервер их прислал). */
export function positionStatus(profile) {
  if (profile.state === "approved") return { text: "утверждён", tone: "g" };
  if (profile.state === "empty") return { text: "критерии не заполнены", tone: "o" };
  if (profile.state === "pending") {
    const who = profile.author ? genitiveName(profile.author) : "";
    const when = profile.at ? ddmm(profile.at) : "";
    return { text: who || when ? `ждёт утверждения — правка ${[who, when ? `от ${when}` : ""].filter(Boolean).join(" ")}` : "ждёт утверждения", tone: "o" };
  }
  return { text: "черновик", tone: "" };
}

function genitiveName(name) {
  const match = /^(\S+)\s+(\S.*)$/.exec(String(name).trim());
  if (!match) return String(name).trim();
  const [, last, rest] = match;
  let form = last;
  if (/(ова|ева|ёва|ина|ына)$/.test(last)) form = `${last.slice(0, -1)}ой`;
  else if (/[бвгджзклмнпрстфхцчшщ]$/.test(last)) form = `${last}а`;
  return `${form} ${rest}`;
}
