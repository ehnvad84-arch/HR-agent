// Ответ /api/person/{id}/dossier → то, что показывает «Досье» (без DOM; проверяется в tests/js).
import { monthYear, moneyText, numText, statusTitle, yearsText } from "../format.js";

// ------------------------------------------------------------------ шапка

/** «Имя — должность, город»; у кандидата без имени в fio лежит сам ярлык, его не повторяем. */
export function headerTitle(person) {
  if (person.name && person.name !== person.label) return person.label ? `${person.name} — ${person.label}` : person.name;
  return person.label || person.name || "Без имени";
}

export function bestScore(person, matches) {
  if (typeof person.best_score === "number") return person.best_score;
  const scores = (matches || []).map((m) => m.score).filter((v) => typeof v === "number");
  return scores.length ? Math.max(...scores) : null;
}

/** Состояние для строки под именем и для значка: кандидат, веду особо, не наш, наблюдение. */
export function personState(person, candidate) {
  if (candidate) return { text: `кандидат: ${statusTitle(candidate.status)}`, tone: "g" };
  if (person.watch === "watched") return { text: "веду особо", tone: "v" };
  if (person.watch === "dropped") return { text: "не наш: вне выдачи", tone: "r" };
  return { text: "наблюдение", tone: "b" };
}

// ------------------------------------------------------------------ балл и разбор

/**
 * Признаки балла для полос «сколько из скольких». features — словарь {код: {label, points, max, detail}} или список;
 * коды с «_» служебные, признаки без веса не показываются. Сверху самые весомые.
 */
export function featureRows(features) {
  const list = Array.isArray(features)
    ? features.map((f, i) => [String(i), f])
    : Object.entries(features || {});
  return list
    .filter(([key, f]) => !key.startsWith("_") && f && f.max > 0)
    .map(([key, f]) => ({
      key, label: f.label || key, points: f.points ?? 0, max: f.max, detail: f.detail || "",
      pct: Math.round(((f.points ?? 0) / f.max) * 100),
      low: (f.points ?? 0) / f.max < 0.75,
    }))
    .sort((a, b) => b.max - a.max || b.points - a.points);
}

const EXPLANATION_LABELS = /^(Сильнее всего|Чего не хватает|Спросить):\s*(.*)$/;

/** Текст разбора построчно: «Сильнее всего: …», «Чего не хватает: …», «Спросить: …» (подпись выделяется отдельно). */
export function explanationBlocks(text) {
  return String(text || "").split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
    const match = EXPLANATION_LABELS.exec(line);
    return match ? { label: match[1], text: match[2] } : { label: null, text: line };
  });
}

/** Для каких оценок просить модель (S7): разбор моделью уже сохранён — повторно не просим. */
export const explainTargets = (matches) => (matches || []).filter((m) => m.model === "template");

// ------------------------------------------------------------------ лента изменений

const EVENT_TITLES = {
  new: "Попал в поле зрения агента", resume_updated: "Обновил резюме", employer_changed: "Сменил работодателя",
  salary_changed: "Изменил зарплатные ожидания", opened_for_search: "Открыл резюме для поиска", skill_added: "Добавил навык",
  geo_changed: "Переехал или заявил готовность к переезду", disappeared: "Резюме скрыто или удалено",
};
const FIELD_LABELS = {
  skills: "Навыки", relocation_ready: "Готовность к переезду", salary_expect: "Ожидания по зарплате", title: "Должность",
  city: "Город", exp_years: "Опыт", employer_current: "Текущий работодатель", employers: "Работодатели",
  open_for_search: "Открыт для поиска", education: "Образование",
};
const LIST_LIMIT = 5;

export const eventTitle = (kind) => EVENT_TITLES[kind] || "Событие";
export const decisionTitle = (kind) => ({ postpone: "Отложил", reject: "Отметил «не наш»" }[kind] || String(kind));

function value(field, v) {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "да" : "нет";
  if (field === "salary_expect" && typeof v === "number") return moneyText(v);
  if (field === "exp_years" && typeof v === "number") return yearsText(v);
  if (typeof v === "number") return numText(v);
  if (Array.isArray(v)) return v.join(", ");
  return String(v);
}

function listText(items) {
  const shown = items.slice(0, LIST_LIMIT).join(", ");
  return items.length > LIST_LIMIT ? `${shown} и ещё ${items.length - LIST_LIMIT}` : shown;
}

/** Что изменилось в резюме по сравнению с прошлой версией: строки «Навыки: добавил …», «Ожидания: 1 → 2». */
export function diffLines(diff) {
  if (!diff || typeof diff !== "object") return [];
  const lines = [];
  for (const [field, change] of Object.entries(diff)) {
    const label = FIELD_LABELS[field] || field;
    if (change && typeof change === "object" && ("added" in change || "removed" in change)) {
      const parts = [];
      if ((change.added || []).length) parts.push(`добавил ${listText(change.added)}`);
      if ((change.removed || []).length) parts.push(`убрал ${listText(change.removed)}`);
      if (parts.length) lines.push(`${label}: ${parts.join("; ")}`);
    } else if (change && typeof change === "object" && ("from" in change || "to" in change)) {
      const from = change.from === null || change.from === undefined ? null : value(field, change.from);
      lines.push(from === null ? `${label}: ${value(field, change.to)}` : `${label}: ${from} → ${value(field, change.to)}`);
    } else if (change !== null && typeof change !== "object") {
      lines.push(`${label}: ${value(field, change)}`);
    }
  }
  return lines;
}

/** Подробности события: «балл 80», «150 000 → 190 000»; нечего сказать — null. */
export function eventDetail(event) {
  const detail = event.detail;
  if (!detail || typeof detail !== "object") return null;
  if (typeof detail.score === "number") return `балл ${detail.score}`;
  if ("from" in detail || "to" in detail) return `${value("", detail.from)} → ${value("", detail.to)}`;
  const parts = Object.entries(detail)
    .filter(([, v]) => v !== null && v !== "" && (typeof v !== "object" || Array.isArray(v)))
    .map(([k, v]) => `${FIELD_LABELS[k] || k}: ${value(k, v)}`);
  return parts.length ? parts.join("; ") : null;
}

/** События и версии резюме одним списком, свежие сверху; при равном времени событие выше версии. */
export function timeline({ events, versions } = {}) {
  const at = (iso) => (Number.isNaN(Date.parse(iso)) ? 0 : Date.parse(iso));
  const items = [
    ...(events || []).map((e) => ({ kind: "event", at: at(e.happened_at), iso: e.happened_at, title: eventTitle(e.kind),
      detail: eventDetail(e) })),
    ...(versions || []).map((v) => {
      const lines = diffLines(v.diff);
      return { kind: "version", at: at(v.captured_at), iso: v.captured_at,
        title: v.diff ? "Резюме изменилось" : "Сохранена первая версия резюме", detail: lines.length ? lines.join("\n") : null };
    }),
  ];
  return items.sort((a, b) => b.at - a.at || (a.kind === "event" ? -1 : 1) - (b.kind === "event" ? -1 : 1));
}

// ------------------------------------------------------------------ резюме и следы в сети

function partLabel(v) {
  const match = /^(\d{4})(?:-(\d{2}))?/.exec(String(v));
  if (!match) return String(v);
  return match[2] ? monthYear(new Date(Number(match[1]), Number(match[2]) - 1, 1)) : match[1];
}

/** «январь 2024 — апрель 2026», «январь 2026 — по настоящее время». */
export function periodText(start, end) {
  if (!start && !end) return "";
  return `${start ? partLabel(start) : "?"} — ${end ? partLabel(end) : "по настоящее время"}`;
}

const SOCIAL = {
  github: ["GitHub", "gh", "GH"], gitlab: ["GitLab", "gl", "GL"], telegram: ["Telegram", "tg", "TG"],
  vk: ["ВКонтакте", "vk", "VK"], ok: ["Одноклассники", "ok", "ОК"], habr: ["Хабр Карьера", "ha", "ХК"],
  linkedin: ["LinkedIn", "in", "in"], facebook: ["Facebook*", "fb", "FB"], instagram: ["Instagram*", "ig", "IG"],
  x: ["X (Twitter)", "x", "X"], youtube: ["YouTube", "yt", "YT"], dzen: ["Дзен", "dz", "Дз"], tiktok: ["TikTok", "tt", "TT"],
  forum: ["Форум", "fo", "Ф"], conference: ["Конференция", "cf", "К"],
};

export function socialInfo(kind) {
  const [title, ico, abbr] = SOCIAL[kind] || ["Другое", "ot", "•"];
  return { title, ico, abbr };
}

/** Уверенность словом, не только цветом. */
export function confidenceInfo(confidence) {
  return {
    confirmed: { text: "подтверждено", tone: "g" },
    probable: { text: "вероятно", tone: "o" },
    rejected: { text: "отклонено", tone: "r" },
  }[confidence] || { text: String(confidence), tone: "" };
}

