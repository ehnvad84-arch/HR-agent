// Полоса хода загрузки резюме hh на всех экранах (S48): тексты, прогресс, итог, «Запускаю…», опрос, «×»
import assert from "node:assert/strict";
import test from "node:test";

import {
  DISMISS_KEY, DONE_TTL_MS, POLL_IDLE_MS, POLL_RUNNING_MS, POLL_STARTING_MS, STARTING_TIMEOUT_MS, barView, pollDelay, readDismissed, writeDismissed,
} from "../../web/js/load-status-model.js";

const NOW = Date.UTC(2026, 9, 30, 15, 0, 0);
const iso = (msAgo) => new Date(NOW - msAgo).toISOString();
const STATS = { seen: 311, new: 120, changed: 14, skipped: 0, errors: 0, alerts: 3, note: null };
const running = (progress, id = 7) => ({ running: true, run: { id, started_at: iso(60000), finished_at: null, ok: null, error: null, stats: {}, progress } });
const finished = (over = {}, id = 7, ago = 60000) => ({ running: false, run: { id, started_at: iso(ago + 600000), finished_at: iso(ago), ok: true, error: null, stats: STATS, progress: {}, ...over } });

test("интервалы опроса: во время хода — 3 с, после запуска — 1,5 с, иначе раз в минуту (видно и прогон в 18:00)", () => {
  assert.equal(POLL_RUNNING_MS, 3000);
  assert.equal(POLL_STARTING_MS, 1500);
  assert.equal(POLL_IDLE_MS, 60000);
  assert.equal(DONE_TTL_MS, 30 * 60 * 1000);
  assert.equal(STARTING_TIMEOUT_MS, 30000);
  assert.equal(pollDelay(barView(running({}), { now: NOW })), POLL_RUNNING_MS);
  assert.equal(pollDelay(barView(null, { now: NOW, starting: { since: NOW, baselineId: null } })), POLL_STARTING_MS);
  assert.equal(pollDelay(barView(finished(), { now: NOW })), POLL_IDLE_MS);
  assert.equal(pollDelay(null), POLL_IDLE_MS);
});

test("нет прогона — полосы нет", () => {
  assert.equal(barView(null, { now: NOW }), null);
  assert.equal(barView({ running: false, run: null }, { now: NOW }), null);
  assert.equal(barView({}, { now: NOW }), null);
});

test("S48: идёт поиск — сколько скачано, какая должность по счёту, полоса-прогресс", () => {
  const view = barView(running({ stage: "search", full_done: 120, full_total: 311, profile_index: 3, profiles: 12, profile_title: "Менеджер проектных продаж", seen: 400 }), { now: NOW });
  assert.equal(view.kind, "running");
  assert.equal(view.text, "Загрузка резюме HH.ru: скачано 120 из 311 полных резюме · должность 3 из 12 «Менеджер проектных продаж»");
  assert.equal(view.percent, 39);
  assert.equal(view.link, null);
  assert.equal(view.dismissible, false);
});

test("S48: идёт поиск по одной должности — без «должность K из L», если портретов нет в прогрессе", () => {
  const view = barView(running({ stage: "search", full_done: 5, full_total: 40, profiles: 0, seen: 9 }), { now: NOW });
  assert.equal(view.text, "Загрузка резюме HH.ru: скачано 5 из 40 полных резюме");
  const one = barView(running({ stage: "search", full_done: 0, full_total: 40, profile_index: 1, profiles: 1, profile_title: "Руководитель ОТК" }), { now: NOW });
  assert.equal(one.text, "Загрузка резюме HH.ru: скачано 0 из 40 полных резюме · должность 1 из 1 «Руководитель ОТК»");
  assert.equal(one.percent, 0);
  const noTitle = barView(running({ stage: "search", full_done: 1, full_total: 4, profile_index: 2, profiles: 5, profile_title: "" }), { now: NOW });
  assert.equal(noTitle.text, "Загрузка резюме HH.ru: скачано 1 из 4 полных резюме · должность 2 из 5");
});

test("S48: полоса не выходит за 100%, нечётные числа округляются", () => {
  assert.equal(barView(running({ stage: "search", full_done: 311, full_total: 311 }), { now: NOW }).percent, 100);
  assert.equal(barView(running({ stage: "search", full_done: 320, full_total: 311 }), { now: NOW }).percent, 100);
  assert.equal(barView(running({ stage: "search", full_done: 1, full_total: 3 }), { now: NOW }).percent, 33);
});

test("S48: этап «фото» и начало прогона", () => {
  const photos = barView(running({ stage: "photos", full_done: 311, full_total: 311 }), { now: NOW });
  assert.equal(photos.text, "Загрузка резюме HH.ru: загружаю фото");
  assert.equal(photos.percent, null);   // фото — отдельный этап без своей меры
  const start = barView(running({ stage: "start" }), { now: NOW });
  assert.equal(start.text, "Загрузка резюме HH.ru: готовлюсь к поиску");
  assert.equal(start.percent, null);
});

test("S48: у старого прогона прогресса нет — просто «идёт»", () => {
  assert.equal(barView(running({}), { now: NOW }).text, "Загрузка резюме HH.ru идёт");
  assert.equal(barView(running(undefined), { now: NOW }).text, "Загрузка резюме HH.ru идёт");
  assert.equal(barView(running(null), { now: NOW }).percent, null);
  const onlySeen = barView(running({ stage: "search", seen: 42 }), { now: NOW });
  assert.equal(onlySeen.text, "Загрузка резюме HH.ru: просмотрено 42");
  assert.equal(onlySeen.percent, null);
  assert.equal(barView({ running: true, run: null }, { now: NOW }).text, "Загрузка резюме HH.ru идёт");
});

test("S48: итог — просмотрено, новых, изменилось и ссылка в «Базу»", () => {
  const view = barView(finished(), { now: NOW });
  assert.equal(view.kind, "done");
  assert.equal(view.text, "Готово: просмотрено 311, новых 120, изменилось 14");
  assert.deepEqual(view.link, { text: "Открыть Базу", route: "base" });
  assert.equal(view.dismissible, true);
  assert.equal(view.runId, 7);
  assert.equal(view.percent, null);
  assert.equal(view.note, "");
});

test("S48: примечание прогона — мелко под итогом", () => {
  const view = barView(finished({ stats: { ...STATS, note: "достигнут предел 110 страниц за прогон" } }), { now: NOW });
  assert.equal(view.note, "Достигнут предел 110 страниц за прогон");
});

test("S48: остановлена — причина, ссылки нет", () => {
  const view = barView(finished({ ok: false, error: "лимит просмотров hh исчерпан" }), { now: NOW });
  assert.equal(view.kind, "failed");
  assert.equal(view.text, "Загрузка остановлена: лимит просмотров hh исчерпан");
  assert.equal(view.link, null);
  assert.equal(view.dismissible, true);
  assert.equal(barView(finished({ ok: false, error: null }), { now: NOW }).text, "Загрузка остановлена: причина не указана");
});

test("S48: итог висит, пока не нажали «×» или 30 минут после окончания", () => {
  assert.equal(barView(finished({}, 7, 29 * 60000), { now: NOW }).kind, "done");
  assert.equal(barView(finished({}, 7, 31 * 60000), { now: NOW }), null);
  assert.equal(barView(finished({}, 7), { now: NOW, dismissedId: "7" }), null);
  assert.equal(barView(finished({}, 7), { now: NOW, dismissedId: 7 }), null);
  assert.equal(barView(finished({}, 8), { now: NOW, dismissedId: "7" }).kind, "done");   // закрыли прежний итог, новый покажем
  assert.equal(barView(finished({ ok: false, error: "x" }, 7), { now: NOW, dismissedId: "7" }), null);
  assert.equal(barView(running({ stage: "search", full_done: 1, full_total: 2 }, 7), { now: NOW, dismissedId: "7" }).kind, "running");   // идущее не прячется
});

test("S48: после запуска — «Запускаю загрузку…», пока не появится прогон новее прежнего", () => {
  const starting = { since: NOW - 2000, baselineId: 7 };
  const view = barView(finished({}, 7), { now: NOW, starting });
  assert.equal(view.kind, "starting");
  assert.equal(view.text, "Запускаю загрузку…");
  assert.equal(view.dismissible, false);
  assert.equal(barView(null, { now: NOW, starting: { since: NOW - 2000, baselineId: null } }).kind, "starting");
  // прогон с новым номером появился — показываем его ход
  assert.equal(barView(running({ stage: "start" }, 8), { now: NOW, starting }).kind, "running");
  // прогон успел закончиться между опросами — сразу итог
  assert.equal(barView(finished({}, 8, 1000), { now: NOW, starting }).kind, "done");
  // старый итог (закрытый «×») не мешает «Запускаю…»
  assert.equal(barView(finished({}, 7), { now: NOW, starting, dismissedId: "7" }).kind, "starting");
});

test("S48: прогон так и не появился — через 30 с «Запускаю…» пропадает, показывается то, что есть", () => {
  const stale = { since: NOW - STARTING_TIMEOUT_MS - 1, baselineId: 7 };
  assert.equal(barView(finished({}, 7, 31 * 60000), { now: NOW, starting: stale }), null);
  assert.equal(barView(finished({}, 7), { now: NOW, starting: stale }).kind, "done");
});

test("«×» запоминается в localStorage; сбой хранилища ничего не ломает", () => {
  const store = new Map();
  const storage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)) };
  assert.equal(readDismissed(storage), null);
  writeDismissed(storage, 12);
  assert.equal(store.get(DISMISS_KEY), "12");
  assert.equal(readDismissed(storage), "12");
  const broken = { getItem() { throw new Error("закрыто"); }, setItem() { throw new Error("закрыто"); } };
  assert.equal(readDismissed(broken), null);
  assert.doesNotThrow(() => writeDismissed(broken, 3));
  assert.equal(readDismissed(null), null);
  assert.doesNotThrow(() => writeDismissed(undefined, 3));
});
