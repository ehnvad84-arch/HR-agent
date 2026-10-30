// Передача кандидата руководителем в отдел персонала без DOM (S9–S11): список вакансий в окне, счётчик комментария, бейдж статуса, карточка у HR.
// Данные — GET /api/handoff/options?person_id= и /api/handoffs, /api/today (handoffs).
import { plural } from "./format.js";

export const NO_OPEN_TEXT = "Нет открытых вакансий — передать некуда";
export const DEFAULT_MIN = 50;   // порог приходит в ответе API (settings.handoff_comment_min); это запасное значение
export const zoneTitle = () => "Передали руководители — решите, брать ли в работу";
export const NO_INBOX = "Передач от руководителей нет";
export const REJECT_HINT = "Причину увидит руководитель";
export const REJECT_REASONS = ["нет нужного опыта", "не подходит по зарплате", "не подходит по городу", "уже в работе по другой вакансии"];

const pad = (n) => String(n).padStart(2, "0");
const toDate = (value) => {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? date : null;
};
const ddmm = (date) => `${pad(date.getDate())}.${pad(date.getMonth() + 1)}`;

/** «Петрова А.» → «Петровой А.», «Иванов И.» → «Иванова И.» (родительный: «в работе у …»); незнакомая форма остаётся как есть. */
function genitive(name) {
  const match = /^(\S+)\s+(\S.*)$/.exec(String(name ?? "").trim());
  if (!match) return String(name ?? "").trim();
  const [, last, rest] = match;
  let form = last;
  if (/(ова|ева|ёва|ина|ына)$/.test(last)) form = `${last.slice(0, -1)}ой`;
  else if (/(ская|цкая)$/.test(last)) form = `${last.slice(0, -2)}ой`;
  else if (/ский$/.test(last)) form = `${last.slice(0, -2)}ого`;
  else if (/[бвгджзклмнпрстфхцчшщ]$/.test(last)) form = `${last}а`;
  return `${form} ${rest}`;
}

const REFUSED = new Set(["rejected_by_us", "rejected_by_candidate", "no_show"]);
const JUST_TAKEN = new Set([null, undefined, "", "picked_by_agent"]);

/** Статус для руководителя: {text, tone, reason}; причина отказа — строкой под бейджем. */
export function handoffBadge(handoff) {
  const who = String(handoff.recruiter ?? "").trim();
  if (handoff.state === "rejected") return { text: "отклонён отделом персонала", tone: "r", reason: handoff.reject_reason || "" };
  if (handoff.state === "taken") {
    if (handoff.stage === "hired") return { text: "вышел на работу", tone: "g", reason: "" };
    if (REFUSED.has(handoff.stage)) return { text: "отказ", tone: "r", reason: handoff.reject_reason || "" };
    if (JUST_TAKEN.has(handoff.stage)) return { text: `в работе у ${who ? genitive(who) : "отдела персонала"}`, tone: "g", reason: "" };
    return { text: `этап: ${String(handoff.stage_title || handoff.stage).toLowerCase()}`, tone: "v", reason: "" };
  }
  const at = toDate(handoff.created_at);
  return { text: at ? `передан ${ddmm(at)}` : "передан", tone: "b", reason: "" };
}

export const sentToast = (reply) => (reply && reply.recruiter ? `Передано ${genitive(reply.recruiter)}` : "Передано в отдел персонала");

/** Вакансии для выбора: с баллом сверху (по убыванию), остальные по названию; уже переданная видна, но неактивна. */
export function optionList(options) {
  const list = options && Array.isArray(options.vacancies) ? options.vacancies : [];
  const scored = (v) => typeof v.score === "number";
  const sorted = [...list].sort((a, b) => (scored(b) - scored(a)) || (scored(a) && scored(b) ? b.score - a.score : 0)
    || String(a.title).localeCompare(String(b.title), "ru"));
  return sorted.map((v) => ({
    id: v.id, title: v.title, score: scored(v) ? v.score : null, recruiter: v.recruiter || "", disabled: Boolean(v.already),
    label: `${v.title}${scored(v) ? ` — балл ${v.score}` : ""}${v.already ? " · уже передан" : ""}`,
  }));
}

/** Выбрано по умолчанию: первая доступная вакансия, где у человека есть балл; иначе первая доступная; доступных нет — ничего. */
export function defaultVacancy(list) {
  const open = (list || []).filter((v) => !v.disabled);
  return (open.find((v) => v.score !== null) || open[0] || { id: null }).id;
}

/** До пяти вакансий — радиокнопки, больше — выпадающий список. */
export const pickMode = (count) => (count > 5 ? "select" : "radios");

export const recipientText = (vacancy) => (vacancy && vacancy.recruiter ? `Получит: ${vacancy.recruiter}, ответственный HR` : "");

const chars = (text) => [...String(text ?? "").trim()].length;

/** «Ещё 54 знака» → «✓ достаточно». */
export function commentCounter(text, min = DEFAULT_MIN) {
  const left = Math.max(0, (Number.isFinite(min) ? min : DEFAULT_MIN) - chars(text));
  return left === 0 ? { text: "✓ достаточно", ok: true } : { text: `Ещё ${left} ${plural(left, "знак", "знака", "знаков")}`, ok: false };
}

export function canSend({ vacancyId, comment, min = DEFAULT_MIN, list }) {
  const chosen = (list || []).find((v) => v.id === vacancyId);
  return Boolean(chosen) && !chosen.disabled && commentCounter(comment, min).ok;
}

/** «Передал: Иванов И. И., начальник ОГМ · 30.10 09:12» — карточка передачи у отдела персонала. */
export function handoffMeta(handoff) {
  const at = toDate(handoff.created_at);
  const who = [handoff.from_user, handoff.from_unit].filter(Boolean).join(", ");
  return `Передал: ${who}${at ? ` · ${ddmm(at)} ${pad(at.getHours())}:${pad(at.getMinutes())}` : ""}`;
}

/** Передачи одного человека (из общего списка GET /api/handoffs), свежие сверху: блок «Передачи» в досье руководителя. */
export function handoffsOfPerson(items, personId) {
  return (Array.isArray(items) ? items : [])
    .filter((x) => String(x.person_id) === String(personId))
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
}

/** Строки карточки передачи у отдела персонала; «Ответственный» — у руководителя отдела (он видит передачи всех). */
export function inboxLines(handoff, { seeAll = false } = {}) {
  return [
    `Вакансия: ${handoff.vacancy_title}`,
    handoffMeta(handoff),
    seeAll && handoff.recruiter ? `Ответственный: ${handoff.recruiter}` : null,
  ].filter(Boolean);
}

export const takenToast = (handoff) => `Взят в работу по вакансии «${handoff.vacancy_title}»`;
