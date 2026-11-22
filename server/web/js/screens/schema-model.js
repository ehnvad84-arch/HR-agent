// «Схема» без DOM (S34): названия задач, последний прогон каждой из ops.job_run словами, живые цифры базы.
// Данные — GET /api/schema: {jobs: последний прогон каждой задачи, counts: объём базы} (hr/views.py: schema).
import { capitalize, numText, plural, timeLabel, whenLabel } from "../format.js";

/** Задачи в порядке пути данных: сбор → оценка → сигналы → обучение → почта → пересчёт. */
export const JOBS = [["collect_hh", "Сбор с hh"], ["import_files", "Выгрузки из кабинетов"], ["mark_gone", "Проверка пропавших"],
  ["score", "Оценка и разборы"], ["alerts", "Сигналы"], ["collect_social", "Следы в сети"], ["photos", "Фото"],
  ["digest", "Недельная сводка"], ["apply_digest", "Применение решений"], ["digest_mail", "Утренний дайджест"],
  ["weekly_report", "Недельный отчёт"], ["rescore_profile", "Пересчёт по профилю"], ["reembed_night", "Пересчёт векторов"]];
export const jobTitle = (code) => (JOBS.find(([c]) => c === code) || [code, String(code ?? "")])[1];

/** Задача без конца дольше этого срока считается прерванной: экран не ждёт её вечно. */
export const STALE_HOURS = 3;

// ------------------------------------------------------------------ сколько длилось

const toDate = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
};

/** «меньше секунды», «2 с», «1 мин 30 с», «12 мин», «1 ч 5 мин»; конец раньше начала или не дата — пустая строка. */
export function durationText(from, to) {
  const a = toDate(from);
  const b = toDate(to);
  if (!a || !b || b < a) return "";
  const total = Math.floor((b - a) / 1000);
  if (total < 1) return "меньше секунды";
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours) return minutes ? `${hours} ч ${minutes} мин` : `${hours} ч`;
  if (minutes) return seconds ? `${minutes} мин ${seconds} с` : `${minutes} мин`;
  return `${seconds} с`;
}

// ------------------------------------------------------------------ цифры прогона словами

// порядок — как в hr/cli.py (печать итога прогона): просмотрено, новых, изменилось, карточек, сигналов, страниц
const STAT_ORDER = ["seen", "new", "changed", "persons", "alerts", "pages", "skipped", "errors"];
const STAT_LABELS = { seen: "просмотрено", new: "новых", changed: "изменилось", persons: "карточек", alerts: "сигналов", pages: "страниц",
  skipped: "пропущено", errors: "ошибок" };
// слова отдельных задач: те же счётчики, но значат у них своё (hr/pipeline.py, photos.py, learning.py, mailer.py, files.py)
const JOB_LABELS = {
  rescore_profile: { seen: "пересчитано людей", persons: "новых карточек", skipped: "наблюдений без карточки" },
  photos: { new: "загружено фото" },
  digest: { seen: "сообщений в чате", new: "предложений" },
  apply_digest: { seen: "принятых предложений", changed: "применено" },
  digest_mail: { changed: "писем отправлено" },
  weekly_report: { changed: "писем отправлено" },
  import_files: { skipped: "файлов пропущено" },
  mark_gone: { seen: "проверено" },
};
const EXTRA_LABELS = { vectors: "векторов", vector_errors: "ошибок векторов" };

/** Ненулевые цифры прогона словами: ["просмотрено 100", "новых 99", …]. */
export function statFacts(job, stats) {
  const s = stats && typeof stats === "object" ? stats : {};
  const labels = { ...STAT_LABELS, ...(JOB_LABELS[job] || {}) };
  const facts = STAT_ORDER.filter((key) => Number(s[key]) > 0).map((key) => `${labels[key]} ${numText(Number(s[key]))}`);
  const extra = s.extra && typeof s.extra === "object" ? s.extra : {};
  for (const [key, label] of Object.entries(EXTRA_LABELS)) if (Number(extra[key]) > 0) facts.push(`${label} ${numText(Number(extra[key]))}`);
  if (s.limit_reached && !s.note) facts.push("достигнут предел страниц");
  return facts;
}

// ------------------------------------------------------------------ прогон для показа

const STATUS = {
  ok: { text: "готово", tone: "g" }, error: { text: "ошибка", tone: "r" }, running: { text: "идёт", tone: "b" },
  blocked: { text: "остановлена", tone: "o" }, stale: { text: "не завершилась", tone: "o" }, never: { text: "ещё не запускалась", tone: "" },
};

function statusOf(row, now) {
  if (row.finished_at === null || row.finished_at === undefined) {
    if (row.ok === false) return "error";
    const started = toDate(row.started_at);
    return started && (now - started) / 3600000 >= STALE_HOURS ? "stale" : "running";
  }
  if (row.ok === false) return row.stats && row.stats.blocked ? "blocked" : "error";
  return "ok";
}

/** Последний прогон задачи для строки таблицы: когда, сколько длился, итог, цифры, замечание или ошибка. */
export function runInfo(row, now = new Date()) {
  const status = statusOf(row, now);
  const facts = status === "running" || status === "stale" ? [] : statFacts(row.job, row.stats);
  const note = status === "ok" || status === "blocked" || status === "error" ? capitalize(String((row.stats && row.stats.note) || "")) : "";
  let error = row.error || "";
  if (status === "stale") error = `Запуск начат ${durationText(row.started_at, now)} назад и не закончен — возможно, процесс прервали`;
  const duration = status === "running" ? `идёт ${durationText(row.started_at, now)}`.trim()
    : status === "stale" ? "" : durationText(row.started_at, row.finished_at);
  return {
    job: row.job, title: jobTitle(row.job), never: false, status, statusText: STATUS[status].text, tone: STATUS[status].tone,
    when: whenLabel(row.started_at, now), duration, facts, note, error,
    empty: status === "running" ? "Идёт сейчас, экран обновляется сам" : status === "ok" && !facts.length && !note ? "без изменений" : "",
  };
}

/** Все задачи по порядку: у запускавшихся — последний прогон, у остальных «ещё не запускалась»; незнакомые — в конце. */
export function jobRows(jobs, now = new Date()) {
  const byJob = new Map((jobs || []).map((row) => [row.job, row]));
  const known = new Set(JOBS.map(([code]) => code));
  const codes = [...JOBS.map(([code]) => code), ...[...byJob.keys()].filter((code) => !known.has(code))];
  return codes.map((code) => (byJob.has(code) ? runInfo(byJob.get(code), now)
    : { job: code, title: jobTitle(code), never: true, status: "never", statusText: STATUS.never.text, tone: STATUS.never.tone,
      when: "", duration: "", facts: [], note: "", error: "", empty: "" }));
}

/** Идёт ли сейчас хоть одна задача (не прерванная): пока идёт, экран перечитывает данные. */
export const hasRunning = (jobs, now = new Date()) => (jobs || []).some((row) => statusOf(row, now) === "running");

// ------------------------------------------------------------------ живые цифры

const TILES = [["observations", "Резюме в базе"], ["persons", "Карточек людей"], ["candidates", "Кандидатов"],
  ["profiles", "Действующих профилей"], ["signals", "Новых сигналов"]];
const countOf = (counts, key) => (counts && typeof counts[key] === "number" ? counts[key] : null);

export const countTiles = (counts) => TILES.map(([key, label]) => ({ key, label, value: countOf(counts, key) === null ? "—" : numText(countOf(counts, key)) }));

/** «4. Оценка по 83 профилям»: число — из базы, а не из прототипа. */
export function profilesLabel(count) {
  return typeof count === "number" ? `4. Оценка по ${numText(count)} ${plural(count, "профилю", "профилям", "профилям")}` : "4. Оценка по профилям должностей";
}

/** Заголовок блока базы на схеме с живыми цифрами. */
export function zonesTitle(counts) {
  const base = "База на сервере компании — две зоны";
  const o = countOf(counts, "observations");
  const p = countOf(counts, "persons");
  const c = countOf(counts, "candidates");
  if (o === null || p === null || c === null) return base;
  return `${base}: ${numText(o)} резюме, ${numText(p)} ${plural(p, "карточка", "карточки", "карточек")}, ${numText(c)} ${plural(c, "кандидат", "кандидата", "кандидатов")}`;
}

// ------------------------------------------------------------------ пределы суток и предупреждения (S8, S10–S12)

const QUOTA_TILES = [["hh_full_resume", "Полных резюме hh сегодня"], ["llm", "Запросов к модели сегодня"]];
const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/** Плитки «Полных резюме hh сегодня 214 / 300» и «Запросов к модели сегодня 12 / 500»; нет данных — нет плитки. */
export function quotaTiles(quota) {
  return QUOTA_TILES
    .filter(([key]) => quota && quota[key] && isNum(quota[key].used) && isNum(quota[key].limit))
    .map(([key, label]) => ({ key, label, value: `${numText(quota[key].used)} / ${numText(quota[key].limit)}` }));
}

/**
 * Строки над плитками: видны, пока выполняется условие. {key, text, progress?: {now, max, percent}}.
 * Порядок: модель недоступна, пересчёт векторов, предел резюме hh.
 */
export function schemaNotices(data) {
  const out = [];
  const vectors = (data && data.vectors) || {};
  if (vectors.down_since) {
    const since = timeLabel(vectors.down_since);
    out.push({ key: "embed_down", text: `Модель векторов недоступна${since ? ` с ${since}` : ""}: балл считается без смысла, поиск по смыслу не работает. Сбор идёт.` });
  }
  if (isNum(vectors.pending) && vectors.pending > 0 && isNum(vectors.total) && vectors.total > 0) {
    const done = Math.max(0, vectors.total - vectors.pending);
    out.push({ key: "vectors_pending",
      text: `Пересчёт векторов базы: осталось ${numText(vectors.pending)} из ${numText(vectors.total)} резюме. Идёт ночью по частям; до пересчёта человек оценивается без смысла.`,
      progress: { now: done, max: vectors.total, percent: Math.round((done / vectors.total) * 100) } });
  }
  const hh = data && data.quota && data.quota.hh_full_resume;
  if (hh && isNum(hh.used) && isNum(hh.limit) && hh.limit > 0 && hh.used >= hh.limit) {
    out.push({ key: "hh_full_cap",
      text: `Предел полных резюме hh на сегодня исчерпан: ${numText(hh.used)} из ${numText(hh.limit)}. Остальные подходящие — в завтрашнем сборе.` });
  }
  return out;
}
