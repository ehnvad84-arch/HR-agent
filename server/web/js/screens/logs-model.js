// «Журнал и отчёт» без DOM (S32, S33, S40): виды изменений, период и фильтр, «было → стало» словами, недельный отчёт, получатели.
// Журнал — записи agent_change (hr/reports.py: changelog), отчёт — weekly_report (build_weekly / latest).
import { capitalize, moneyText, plural, whenLabel } from "../format.js";
import { SETTING_TITLES, weekTitle } from "./learn-model.js";
import { FEATURES, GEO_OPTIONS, conditionLines, roleTitle, stateInfo } from "./profiles-model.js";

/** Виды изменений — как KIND_TITLE в hr/reports.py. */
export const KINDS = [["threshold", "Пороги"], ["weight", "Веса"], ["profile", "Профили должностей"], ["template", "Шаблоны"],
  ["source", "Источники и пределы"], ["permission", "Права"]];
export const kindLabel = (kind) => (KINDS.find(([code]) => code === kind) || [kind, String(kind ?? "")])[1];

export const DEFAULT_DAYS = 30;
export const RECIPIENTS_NOTE = "Адреса меняются в файле .env (переменная MAIL_TO, через запятую), здесь они только для чтения.";
export const RECIPIENTS_EMPTY = "Адреса не заданы";

// ------------------------------------------------------------------ период

const pad = (n) => String(n).padStart(2, "0");
/** «2026-10-29» по местному времени (не UTC: вечером дата не должна сдвигаться). */
export const isoDay = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;

function dateOnly(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value ?? ""));
  if (!match) return null;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return Number.isNaN(date.getTime()) ? null : date;
}

export const PRESET_DAYS = [7, 30, 90];

/** Последние `days` дней, считая сегодняшний: «7 дней» — те же даты, что в недельном отчёте. */
export function periodFor(days, now = new Date()) {
  return { from: isoDay(new Date(now.getFullYear(), now.getMonth(), now.getDate() - (days - 1))), to: isoDay(now) };
}

/** По умолчанию — последние 30 дней. */
export const defaultPeriod = (now = new Date()) => periodFor(DEFAULT_DAYS, now);

export function periodError(from, to) {
  const a = dateOnly(from);
  const b = dateOnly(to);
  if (!a || !b) return "Укажите обе даты периода";
  return a > b ? "Начало периода позже его конца" : "";
}

const dayText = (date, now) => date.toLocaleDateString("ru-RU", { day: "numeric", month: "long" })
  + (date.getFullYear() === now.getFullYear() ? "" : ` ${date.getFullYear()}`);

/** «с 29 сентября по 29 октября». */
export function periodLabel(from, to, now = new Date()) {
  const a = dateOnly(from);
  const b = dateOnly(to);
  return a && b ? `с ${dayText(a, now)} по ${dayText(b, now)}` : "";
}

// ------------------------------------------------------------------ фильтр по виду

/** «Все» и только те виды, по которым есть записи (в порядке KINDS; незнакомые — в конце). */
export function kindCounts(items) {
  const list = items || [];
  const counts = new Map();
  for (const item of list) counts.set(item.kind, (counts.get(item.kind) || 0) + 1);
  const known = KINDS.filter(([code]) => counts.has(code)).map(([code, label]) => ({ kind: code, label, count: counts.get(code) }));
  const other = [...counts.keys()].filter((code) => !KINDS.some(([c]) => c === code)).map((code) => ({ kind: code, label: kindLabel(code), count: counts.get(code) }));
  return [{ kind: "all", label: "Все", count: list.length }, ...known, ...other];
}

export const filterByKind = (items, kind) => ((!kind || kind === "all") ? [...(items || [])] : (items || []).filter((item) => item.kind === kind));

// ------------------------------------------------------------------ «было → стало» словами

const FIELD_ORDER = [["title", "Название"], ["geo", "География"], ["salary_min", "Вилка от"], ["salary_max", "Вилка до"],
  ["must_have", "Обязательные условия"], ["nice_to_have", "Желательные условия"], ["stop_factors", "Стоп-факторы"], ["weights", "Веса"],
  ["keywords", "Ключевые слова"], ["donor_companies", "Компании-доноры"]];
const CONDITIONS = new Set(["must_have", "nice_to_have", "stop_factors"]);
const featureTitle = (key) => (FEATURES.find(([code]) => code === key) || [key, String(key)])[1];

export const PERMISSION_TITLES = {
  "base.read": "читать базу", "person.take": "брать человека в работу", "profile.edit": "править профили", "profile.approve": "утверждать профили",
  "digest.decide": "решать по недельной сводке", "settings.edit": "менять пороги и веса", "report.read": "читать журнал и отчёт",
  "access.manage": "управлять доступом", "org.read": "читать оргструктуру", "org.map": "сопоставлять оргструктуру",
  "draft.own": "готовить свои черновики заявок", "draft.read": "читать черновики заявок", "draft.review": "рассматривать черновики заявок",
  "contact.open": "открывать контакт", "vacancy.edit": "править вакансии", "feedback.write": "писать в чат обучения",
  "dossier.export": "выгружать досье", "vacancy.own": "вести свои вакансии", "candidate.own": "видеть своих кандидатов",
};
const LIMIT_TITLES = {
  ...SETTING_TITLES,
  crawl_max_pages: "страниц за прогон", crawl_per_profile_pages: "страниц на профиль", hh_full_resume_per_run: "полных резюме hh за прогон",
  hh_recheck_per_run: "перепроверок «пропал ли» за прогон", social_rate_per_sec: "запросов в секунду к соцсетям",
};
const limitTitle = (key) => LIMIT_TITLES[key] || String(key);

const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const isEmpty = (value) => value === null || value === undefined || value === "";
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/** Значение для человека: список — через запятую, пусто — «не задано», объект — коротким JSON (крайний случай). */
function valueText(value) {
  if (isEmpty(value)) return "не задано";
  if (typeof value === "boolean") return value ? "да" : "нет";
  if (Array.isArray(value)) return value.length ? value.map((x) => (isObject(x) ? valueText(x.text ?? JSON.stringify(x)) : String(x))).join(", ") : "нет";
  if (isObject(value)) return JSON.stringify(value).slice(0, 160);
  return String(value);
}

function fieldValue(key, value) {
  if (key === "salary_min" || key === "salary_max") return typeof value === "number" ? moneyText(value) : "не задана";
  if (key === "geo") return (GEO_OPTIONS.find(([code]) => code === value) || [null, valueText(value)])[1];
  if (CONDITIONS.has(key)) return Array.isArray(value) && value.length ? conditionLines(value).join("; ") : "нет";
  return valueText(value);
}

const pairs = (object) => Object.entries(object).map(([key, value]) => `${key} — ${valueText(value)}`).join("; ");

/** Запись не узнана: ничего не теряем, но показываем «ключ — значение», а не JSON. */
function fallback(before, after) {
  const details = [];
  if (isObject(before) && Object.keys(before).length) details.push(`Было: ${pairs(before)}`);
  if (isObject(after) && Object.keys(after).length) details.push(`Стало: ${pairs(after)}`);
  return { summary: null, details };
}

function thresholdChange(before, after) {
  if (!isObject(before) || !isObject(after) || !after.setting || before.setting !== after.setting) return fallback(before, after);
  const title = capitalize(String(SETTING_TITLES[after.setting] || after.setting));
  return { summary: isEmpty(before.value) ? `${title}: задан ${after.value}` : `${title}: было ${before.value} → стало ${after.value}`, details: [] };
}

function weightChange(before, after, profileTitles) {
  if (!isObject(after) || !after.feature) return fallback(before, after);
  const profile = profileTitles[after.profile_code] || after.profile_code || "?";
  const old = isObject(before) && isObject(before.weights) ? before.weights[after.feature] : undefined;
  const head = `Вес «${featureTitle(after.feature)}» в профиле «${profile}»`;
  return { summary: isEmpty(old) ? `${head}: был не задан → стал ${after.value}` : `${head}: было ${old} → стало ${after.value}`, details: [] };
}

function profileEdit(before, after) {
  const lines = [];
  const names = [];
  for (const [key, label] of FIELD_ORDER) {
    if (!(key in after) || same(before[key], after[key])) continue;
    names.push(label.toLowerCase());
    if (key === "weights") {
      const was = isObject(before.weights) ? before.weights : {};
      const now = isObject(after.weights) ? after.weights : {};
      for (const [feature] of FEATURES) {
        if (was[feature] !== now[feature]) lines.push(`Вес «${featureTitle(feature)}»: ${isEmpty(was[feature]) ? "не задан" : was[feature]} → ${isEmpty(now[feature]) ? "не задан" : now[feature]}`);
      }
    } else {
      lines.push(`${label}: ${fieldValue(key, before[key])} → ${fieldValue(key, after[key])}`);
    }
  }
  const head = before.version && after.version ? `Версия ${before.version} → ${after.version}, ждёт утверждения`
    : after.version ? `Версия ${after.version}, ждёт утверждения` : "Новая версия, ждёт утверждения";
  return { summary: names.length ? `${head}. Изменено: ${names.join(", ")}` : head, details: lines };
}

function profileChange(before, after) {
  if (!isObject(after)) return fallback(before, after);
  const b = isObject(before) ? before : null;
  if (after.state === "approved") {
    const from = b && b.state ? stateInfo(b.state).text : null;
    const to = stateInfo("approved").text;
    return { summary: `${after.version ? `Версия ${after.version}` : "Состояние версии"}: ${from ? `${from} → ${to}` : to}`, details: [] };
  }
  if (after.state === "draft") {
    return { summary: `Черновая версия ${after.version} из новой должностной инструкции${b && b.version ? ` (прежняя ${b.version})` : ""}`, details: [] };
  }
  if (!b && after.title) {
    const details = Array.isArray(after.keywords) && after.keywords.length ? [`Ключевые слова: ${valueText(after.keywords)}`] : [];
    return { summary: `Добавлен профиль «${after.title}»${after.version ? `, версия ${after.version}` : ""}`, details };
  }
  if (b && after.state === "pending") return profileEdit(b, after);
  return fallback(before, after);
}

function permissionChange(before, after) {
  if (!isObject(after) || (!Array.isArray(after.add) && !Array.isArray(after.remove))) return fallback(before, after);
  const group = (list, verb) => {
    const byRole = new Map();
    for (const entry of list || []) {
      const [role, ...rest] = String(entry).split(":");
      const code = rest.join(":");
      if (!byRole.has(role)) byRole.set(role, []);
      byRole.get(role).push(PERMISSION_TITLES[code] || code);
    }
    return [...byRole.entries()].map(([role, titles]) => `${verb} — ${role === "agent" ? "Агент" : roleTitle(role)}: ${titles.join(", ")}`);
  };
  const added = (after.add || []).length;
  const removed = (after.remove || []).length;
  const summary = added && removed ? `Добавлено прав: ${added}, убрано: ${removed}` : removed ? `Убрано прав: ${removed}` : `Добавлено прав: ${added}`;
  return { summary, details: [...group(after.add, "Добавлено"), ...group(after.remove, "Убрано")] };
}

function sourceChange(before, after) {
  if (!isObject(after) || !Object.keys(after).length) return fallback(before, after);
  const was = isObject(before) ? before : {};
  const keys = [...Object.keys(after), ...Object.keys(was).filter((key) => !(key in after))];
  const changed = keys.filter((key) => String(was[key]) !== String(after[key]) || !(key in was) || !(key in after));
  if (!changed.length) {
    return { summary: `Заданы пределы: ${keys.map((key) => `${limitTitle(key)} — ${valueText(after[key])}`).join("; ")}`, details: [] };
  }
  const line = (key, long) => {
    const title = capitalize(limitTitle(key));
    if (!(key in after)) return `${title}: убрано`;
    if (!(key in was)) return `${title}: задано ${valueText(after[key])}`;
    return long ? `${title}: ${valueText(was[key])} → ${valueText(after[key])}` : `${title}: было ${valueText(was[key])} → стало ${valueText(after[key])}`;
  };
  return changed.length === 1
    ? { summary: line(changed[0], false), details: [] }
    : { summary: `Изменено значений: ${changed.length}`, details: changed.map((key) => line(key, true)) };
}

/** Строка журнала для показа: время, что сделано, кто, вид, «было → стало» коротко и подробности для раскрытия. */
export function describeChange(item, { now = new Date(), profileTitles = {} } = {}) {
  const { before, after } = item;
  let body;
  switch (item.kind) {
    case "threshold": body = thresholdChange(before, after); break;
    case "weight": body = weightChange(before, after, profileTitles); break;
    case "profile": body = profileChange(before, after); break;
    case "permission": body = permissionChange(before, after); break;
    case "source": body = sourceChange(before, after); break;
    case "template":
      // решение по сводке без автоматической правки: суть уже в тексте записи
      body = isObject(after) && Object.keys(after).every((key) => key === "accepted") ? { summary: null, details: [] } : fallback(before, after);
      break;
    default: body = fallback(before, after);
  }
  return {
    id: item.id, kind: item.kind, kindLabel: kindLabel(item.kind), at: item.applied_at, when: whenLabel(item.applied_at, now),
    note: item.note || "", author: item.author || "система", summary: body.summary, details: body.details,
  };
}

// ------------------------------------------------------------------ недельный отчёт

const changesText = (n) => `${n} ${plural(n, "изменение", "изменения", "изменений")}`;
const NO_REPORT = "Отчёт ещё не формировался. Он собирается по пятницам в 17:00 или кнопкой «Сформировать отчёт сейчас»";

/** Отчёт для показа. Нет отчёта (сервер отдаёт changes:0 без id) — подсказка, как его получить. */
export function reportView(report, now = new Date()) {
  if (!report || report.id === null || report.id === undefined) {
    return { hasReport: false, title: "Отчёта пока нет", summary: NO_REPORT, groups: [], sent: "", sentTone: "" };
  }
  const week = weekTitle(report.period_from, report.period_to, now);
  const groups = ((report.body && report.body.groups) || []).map((group) => ({
    kind: group.kind, title: group.title || kindLabel(group.kind), count: group.count, countText: changesText(group.count),
    items: (group.items || []).map((item) => ({ when: whenLabel(item.at, now), note: item.note || "", author: item.author || "система" })),
  }));
  const recipients = report.recipients || [];
  return {
    hasReport: true,
    title: week === "Неделя" ? "Отчёт за неделю" : `Отчёт за ${week.slice("Неделя ".length)}`,
    summary: report.changes ? changesText(report.changes) : "За неделю изменений не было",
    groups,
    sent: report.sent_at ? `Отправлен ${whenLabel(report.sent_at, now)}${recipients.length ? ` на ${recipients.join(", ")}` : ""}` : "Ещё не отправлен",
    sentTone: report.sent_at ? "g" : "o",
  };
}

// ------------------------------------------------------------------ получатели

export const recipientsText = (list) => (list && list.length ? list.join(", ") : RECIPIENTS_EMPTY);
