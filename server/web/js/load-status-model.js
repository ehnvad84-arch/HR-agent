// Полоса хода загрузки резюме hh без DOM (S48): что показать по ответу GET /api/hh/collect/status, когда опрашивать, как помнить «×».
// Ответ: {running, run: {id, started_at, finished_at, ok, error, stats: {seen, new, changed, …, note}, progress: {stage, full_done, full_total,
// profile_index, profiles, profile_title, seen}}}; progress у старого прогона может быть пустым.
import { capitalize } from "./format.js";

export const POLL_RUNNING_MS = 3000;     // пока идёт загрузка
export const POLL_STARTING_MS = 1500;    // сразу после «Загрузить»: прогон появится в ответе через секунду-две
export const POLL_IDLE_MS = 60000;       // иначе раз в минуту: чтобы увидеть и прогон по расписанию (18:00)
export const DONE_TTL_MS = 30 * 60 * 1000;   // итог висит 30 минут после окончания, если не нажали «×»
export const STARTING_TIMEOUT_MS = 30000;    // «Запускаю…» не висит дольше, если прогон так и не появился
export const DISMISS_KEY = "hrLoadDismissed";

const PREFIX = "Загрузка резюме HH.ru";
const STARTING = { kind: "starting", text: "Запускаю загрузку…", percent: null, link: null, note: "", dismissible: false, runId: null };

const num = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);

function runningView(run) {
  const p = (run && run.progress) || {};
  const view = { kind: "running", text: `${PREFIX} идёт`, percent: null, link: null, note: "", dismissible: false, runId: run ? run.id : null };
  if (p.stage === "photos") {
    view.text = `${PREFIX}: загружаю фото`;
  } else if (p.stage === "start") {
    view.text = `${PREFIX}: готовлюсь к поиску`;
  } else if (num(p.full_total) > 0) {
    const total = num(p.full_total);
    const done = num(p.full_done);
    view.text = `${PREFIX}: скачано ${done} из ${total} полных резюме`;
    if (num(p.profiles) > 0 && num(p.profile_index) > 0) {
      view.text += ` · должность ${num(p.profile_index)} из ${num(p.profiles)}${p.profile_title ? ` «${p.profile_title}»` : ""}`;
    }
    view.percent = Math.min(100, Math.round((done / total) * 100));
  } else if (num(p.seen) > 0) {
    view.text = `${PREFIX}: просмотрено ${num(p.seen)}`;
  }
  return view;
}

function finishedView(run, { now, dismissedId }) {
  const finished = Date.parse(run.finished_at);
  if (!Number.isFinite(finished) || now - finished > DONE_TTL_MS) return null;
  if (dismissedId !== null && dismissedId !== undefined && String(dismissedId) === String(run.id)) return null;
  const stats = run.stats || {};
  const note = capitalize(String(stats.note || ""));
  const base = { percent: null, note, dismissible: true, runId: run.id };
  if (run.ok === false) {
    return { ...base, kind: "failed", text: `Загрузка остановлена: ${run.error || "причина не указана"}`, link: null };
  }
  return { ...base, kind: "done", text: `Готово: просмотрено ${num(stats.seen)}, новых ${num(stats.new)}, изменилось ${num(stats.changed)}`,
    link: { text: "Открыть Базу", route: "base" } };
}

/**
 * Что показывает полоса: null (ничего) или {kind: starting | running | done | failed, text, percent|null, link|null, note, dismissible, runId}.
 *   starting — {since, baselineId}: человек только что нажал «Загрузить»; пока прогона новее baselineId нет — «Запускаю загрузку…».
 */
export function barView(reply, { now = Date.now(), dismissedId = null, starting = null } = {}) {
  const run = reply && reply.run ? reply.run : null;
  if (starting && now - starting.since <= STARTING_TIMEOUT_MS) {
    const newer = run && (starting.baselineId === null || starting.baselineId === undefined || Number(run.id) > Number(starting.baselineId));
    if (!newer) return { ...STARTING };
  }
  if (reply && reply.running) return runningView(run);
  return run && run.finished_at ? finishedView(run, { now, dismissedId }) : null;
}

/** Через сколько миллисекунд спрашивать сервер снова. */
export function pollDelay(view) {
  if (view && view.kind === "running") return POLL_RUNNING_MS;
  if (view && view.kind === "starting") return POLL_STARTING_MS;
  return POLL_IDLE_MS;
}

// ------------------------------------------------------------------ «×»: помнить закрытый итог

export function readDismissed(storage) {
  try {
    const value = storage ? storage.getItem(DISMISS_KEY) : null;
    return value === null || value === undefined ? null : String(value);
  } catch {
    return null;
  }
}

export function writeDismissed(storage, runId) {
  try {
    if (storage) storage.setItem(DISMISS_KEY, String(runId));
  } catch { /* хранилище закрыто (приватный режим): итог просто вернётся после обновления страницы */ }
}
