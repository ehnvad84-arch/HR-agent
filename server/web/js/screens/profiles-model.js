// «База должностей» без DOM (S25–S28, S39): состояния версий, форма правки, изменения для новой версии, предпросмотр.
// Тексты — из прототипа (PROFILES, editProfile). Веса и признаки — как в hr/scoring.py.
import { plural } from "../format.js";

/** Признаки балла в порядке показа: код для API и подпись. */
export const FEATURES = [["exp_years", "Опыт по профилю"], ["keywords", "Ключевые слова и инструменты"], ["title_match", "Совпадение должности"],
  ["donor_company", "Опыт в компании-доноре"], ["tenure", "Держится на месте"], ["geo", "География"], ["salary", "Ожидания против вилки"],
  ["open_code", "Открытый код"]];
export const GEO_OPTIONS = [["krasnodar", "Краснодар"], ["remote_ru", "Удалённо по России"], ["relocation_ru", "Переезд по России"]];
const ROLES = { head_recruiter: "Руководитель отдела персонала", recruiter: "Рекрутер", hiring_manager: "Нанимающий руководитель", director: "Директор" };
const STATES = { approved: { text: "действует", pill: "cand" }, pending: { text: "ждёт утверждения", pill: "watch" }, draft: { text: "черновик", pill: "obs" },
  empty: { text: "критерии не заполнены", pill: "warm" } };
const OPEN_CODE_DEPTS = new Set(["rnd_hw", "rnd_sw"]);   // инженерные разработки и разработка ПО: открытый код считается

export const roleTitle = (role) => ROLES[role] || String(role ?? "");
export const stateInfo = (state) => STATES[state] || { text: String(state ?? ""), pill: "obs" };

/** Подпись строки списка: «Инженерные разработки · в. 1.1 от 20.09, утверждена». */
export function rowSub(item) {
  const date = item.approved_at ? new Date(item.approved_at).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" }) : "";
  const tail = item.state === "approved" ? `в. ${item.version}${date ? ` от ${date}` : ""}, утверждена`
    : item.state === "pending" ? `в. ${item.version}, ждёт утверждения`
      : item.state === "empty" ? "критерии не заполнены" : "черновик из ДИ, не утверждён";
  return `${item.dept} · ${tail}`;
}

export function groupByDept(items) {
  const groups = new Map();
  for (const item of items || []) {
    if (!groups.has(item.dept)) groups.set(item.dept, []);
    groups.get(item.dept).push(item);
  }
  return [...groups.entries()]
    .sort((a, b) => String(a[0]).localeCompare(String(b[0]), "ru"))
    .map(([dept, list]) => ({ dept, items: list.sort((a, b) => String(a.title).localeCompare(String(b.title), "ru")) }));
}

/** Ввод чипсов: запятая, точка с запятой, перенос строки; пустое и повторы (без учёта регистра) убираются. */
export function splitChips(text) {
  const seen = new Set();
  const out = [];
  for (const part of String(text ?? "").split(/[,;\n]+/)) {
    const value = part.trim();
    if (value && !seen.has(value.toLowerCase())) {
      seen.add(value.toLowerCase());
      out.push(value);
    }
  }
  return out;
}

// ------------------------------------------------------------------ условия: «обязательно», «желательно», стоп-факторы

export const conditionLines = (list) => (list || [])
  .map((r) => String(r.text || [r.feature, r.op, r.value].filter((x) => x !== undefined).join(" ")).trim()).filter(Boolean);

/** Строки → условия сервера. Неизменённая строка сохраняет прежний объект (разбор признака, знак, значение). */
export function conditionsFromLines(text, existing) {
  const byText = new Map((existing || []).map((r) => [String(r.text ?? "").trim(), r]));
  return String(text ?? "").split("\n").map((line) => line.trim()).filter(Boolean)
    .map((line) => (byText.has(line) ? { ...byText.get(line) } : { text: line }));
}

// ------------------------------------------------------------------ веса

export function defaultWeights(deptCode) {
  return { exp_years: 20, keywords: 25, title_match: 15, donor_company: 10, tenure: 10, geo: 10, salary: 10,
    open_code: OPEN_CODE_DEPTS.has(deptCode) ? 10 : 0 };
}
export const weightSum = (weights) => Object.values(weights || {}).reduce((sum, v) => sum + (Number(v) || 0), 0);

// ------------------------------------------------------------------ портрет: где чей вес

/** Признаки, у которых в портрете есть своё поле: вес стоит рядом с ним. Ключ — признак, значение — поле. */
export const FEATURE_FIELDS = { keywords: "keywords", donor_company: "donors", geo: "geo", salary: "salary" };
/** Признаки без своего поля: агент считает их по резюме сам, правится только вес («Ещё признаки»). */
export const EXTRA_FEATURES = FEATURES.map(([key]) => key).filter((key) => !(key in FEATURE_FIELDS));
const EXTRA_HINTS = {
  exp_years: "Сколько лет человек работает по этому профилю. Планка берётся из условия «опыт от N лет» в обязательных требованиях.",
  title_match: "Насколько должности из резюме похожи на название этой должности.",
  tenure: "Как долго человек держится на местах работы: частые смены снижают оценку.",
  open_code: "Подтверждённый профиль с открытым кодом. Важен для инженерных и программных должностей.",
};
export const featureLabel = (key) => (FEATURES.find(([code]) => code === key) || [key, String(key)])[1];
/** [[код, подпись, пояснение]] для блока «Ещё признаки». */
export const extraFeatures = () => EXTRA_FEATURES.map((key) => [key, featureLabel(key), EXTRA_HINTS[key]]);

/** Доля признака в сумме весов, целых процентов: для мини-полоски у веса. Сумма 0 — доли нет. */
export function weightShare(weights, key) {
  const total = weightSum(weights);
  const own = Number((weights || {})[key]) || 0;
  return total > 0 ? Math.round((own / total) * 100) : 0;
}

export const WEIGHT_STEP = 5;
/** Шаг степпера «− 20 +»: на 5 вверх или вниз в пределах 0…100; пусто и мусор считаются нулём. */
export function stepWeight(text, direction) {
  const n = /^\d+$/.test(String(text ?? "").trim()) ? Number(String(text).trim()) : 0;
  return String(Math.min(100, Math.max(0, n + (direction > 0 ? WEIGHT_STEP : -WEIGHT_STEP))));
}

export const weightSumText = (total) => `Сумма весов: ${total} — сервер приводит к 100`;

// ------------------------------------------------------------------ форма

export function formFromProfile(profile) {
  const own = profile.weights && Object.keys(profile.weights).length > 0;
  return {
    title: profile.title || "",
    geo: profile.geo || "krasnodar",
    salary_min: profile.salary_min === null || profile.salary_min === undefined ? "" : String(profile.salary_min),
    salary_max: profile.salary_max === null || profile.salary_max === undefined ? "" : String(profile.salary_max),
    keywords: [...(profile.keywords || [])],
    donor_companies: [...(profile.donor_companies || [])],
    must_have: conditionLines(profile.must_have).join("\n"),
    nice_to_have: conditionLines(profile.nice_to_have).join("\n"),
    stop_factors: conditionLines(profile.stop_factors).join("\n"),
    weights: own ? Object.fromEntries(FEATURES.map(([key]) => [key, profile.weights[key] ?? 0])) : defaultWeights(profile.dept_code),
    usingDefaultWeights: !own,
  };
}

/** «150 000» → 150000; пусто → null; всё остальное — NaN. */
export function parseMoney(text) {
  const clean = String(text ?? "").replace(/[\s ]/g, "");
  if (!clean) return null;
  return /^\d+$/.test(clean) ? Number(clean) : Number.NaN;
}

export function validateForm(form) {
  if (!String(form.title ?? "").trim()) return "Введите название должности";
  const min = parseMoney(form.salary_min);
  const max = parseMoney(form.salary_max);
  if (Number.isNaN(min) || Number.isNaN(max)) return "Вилка: только целое число рублей";
  if (min !== null && max !== null && min > max) return "Вилка: «от» больше «до»";
  for (const value of Object.values(form.weights || {})) {
    const text = String(value ?? "").trim();
    if (text !== "" && (!/^\d+$/.test(text) || Number(text) > 100)) return "Веса: целые числа от 0 до 100";
  }
  return null;
}

const sameList = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/** Только изменённые поля (S26): так в запрос не попадает то, чего человек не трогал. */
export function changesFor(profile, form) {
  const changes = {};
  if (form.title.trim() !== profile.title) changes.title = form.title.trim();
  if (form.geo !== profile.geo) changes.geo = form.geo;
  for (const key of ["salary_min", "salary_max"]) {
    const value = parseMoney(form[key]);
    if (value !== (profile[key] ?? null)) changes[key] = value;
  }
  for (const key of ["keywords", "donor_companies"]) {
    if (!sameList(form[key], profile[key] || [])) changes[key] = [...form[key]];
  }
  for (const key of ["must_have", "nice_to_have", "stop_factors"]) {
    const next = conditionsFromLines(form[key], profile[key]);
    if (!sameList(next, profile[key] || [])) changes[key] = next;
  }
  const initial = formFromProfile(profile).weights;
  const next = Object.fromEntries(FEATURES.map(([key]) => [key, Number(String(form.weights[key] ?? "").trim() || 0)]));
  if (FEATURES.some(([key]) => next[key] !== Number(initial[key] || 0))) changes.weights = next;
  return changes;
}

// ------------------------------------------------------------------ предпросмотр, ДИ

export function previewText({ passes_now: now, passes_new: next, checked, threshold }) {
  const verb = now % 10 === 1 && now % 100 !== 11 ? "проходит" : "проходят";
  return `Сейчас порог ${threshold} ${verb} ${now} ${plural(now, "человек", "человека", "человек")}, по новой версии — ${next} (проверено ${checked})`;
}

export function docError(file) {
  if (!file) return "Выберите файл";
  if (!/\.(docx|pdf)$/i.test(file.name || "")) return "Нужен файл .docx или .pdf";
  return file.size > 0 ? null : "Файл пустой";
}
