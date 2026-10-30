// «Схема» (S34): живые цифры базы и последние прогоны фоновых задач из ops.job_run
import assert from "node:assert/strict";
import test from "node:test";

import {
  JOBS, STALE_HOURS, countTiles, durationText, hasRunning, jobRows, jobTitle, profilesLabel, runChanged, runInfo, statFacts, zonesTitle,
} from "../../web/js/screens/schema-model.js";

const NOW = new Date(2026, 9, 29, 15, 0, 0);
const at = (d, h, m, s = 0) => new Date(2026, 9, d, h, m, s).toISOString();   // время в часовом поясе, где идёт проверка

const ZERO = { seen: 0, new: 0, changed: 0, persons: 0, alerts: 0, skipped: 0, errors: 0, pages: 0, limit_reached: false, note: null, extra: {} };

test("задачи — двенадцать, по-русски, в порядке пути данных", () => {
  assert.deepEqual(JOBS.map(([code]) => code), ["collect_hh", "import_files", "mark_gone", "score", "alerts", "collect_social", "photos",
    "digest", "apply_digest", "digest_mail", "weekly_report", "rescore_profile"]);
  assert.deepEqual(JOBS.map(([, title]) => title), ["Сбор с hh", "Выгрузки из кабинетов", "Проверка пропавших", "Оценка и разборы", "Сигналы",
    "Следы в сети", "Фото", "Недельная сводка", "Применение решений", "Утренний дайджест", "Недельный отчёт", "Пересчёт по профилю"]);
  assert.equal(jobTitle("rescore_profile"), "Пересчёт по профилю");
  assert.equal(jobTitle("новая_задача"), "новая_задача");
});

test("сколько длилась задача", () => {
  assert.equal(durationText(at(29, 13, 0, 0), at(29, 13, 0, 0)), "меньше секунды");
  assert.equal(durationText(at(29, 13, 0, 0), at(29, 13, 0, 2)), "2 с");
  assert.equal(durationText(at(29, 13, 0, 0), at(29, 13, 1, 30)), "1 мин 30 с");
  assert.equal(durationText(at(29, 13, 0, 0), at(29, 13, 12, 0)), "12 мин");
  assert.equal(durationText(at(29, 13, 0, 0), at(29, 14, 5, 10)), "1 ч 5 мин");
  assert.equal(durationText(at(29, 13, 0, 0), at(29, 15, 0, 0)), "2 ч");
  assert.equal(durationText(at(29, 13, 0, 5), at(29, 13, 0, 0)), "");
  assert.equal(durationText(null, at(29, 13, 0, 0)), "");
  assert.equal(durationText("не дата", "тоже"), "");
});

test("цифры прогона словами: только ненулевые, в порядке конвейера", () => {
  const stats = { ...ZERO, new: 99, seen: 100, pages: 90, alerts: 8, changed: 1, persons: 55 };
  assert.deepEqual(statFacts("collect_hh", stats), ["просмотрено 100", "новых 99", "изменилось 1", "карточек 55", "сигналов 8", "страниц 90"]);
  assert.deepEqual(statFacts("collect_hh", { ...stats, skipped: 3, errors: 2 }).slice(-2), ["пропущено 3", "ошибок 2"]);
  assert.deepEqual(statFacts("alerts", ZERO), []);
  assert.deepEqual(statFacts("alerts", null), []);
  assert.deepEqual(statFacts("alerts", {}), []);
});

test("у отдельных задач — свои слова: цифры не должны вводить в заблуждение", () => {
  assert.deepEqual(statFacts("rescore_profile", { ...ZERO, seen: 57, skipped: 142, persons: 2 }),
    ["пересчитано людей 57", "новых карточек 2", "наблюдений без карточки 142"]);
  assert.deepEqual(statFacts("photos", { ...ZERO, seen: 55, new: 44 }), ["просмотрено 55", "загружено фото 44"]);
  assert.deepEqual(statFacts("digest", { ...ZERO, seen: 3, new: 1 }), ["сообщений в чате 3", "предложений 1"]);
  assert.deepEqual(statFacts("apply_digest", { ...ZERO, seen: 1, changed: 1 }), ["принятых предложений 1", "применено 1"]);
  assert.deepEqual(statFacts("weekly_report", { ...ZERO, changed: 1 }), ["писем отправлено 1"]);
  assert.deepEqual(statFacts("digest_mail", { ...ZERO, changed: 1 }), ["писем отправлено 1"]);
  assert.deepEqual(statFacts("import_files", { ...ZERO, skipped: 2 }), ["файлов пропущено 2"]);
});

test("векторы поиска и предел страниц", () => {
  assert.deepEqual(statFacts("score", { ...ZERO, extra: { vectors: 5, vector_errors: 2 } }), ["векторов 5", "ошибок векторов 2"]);
  assert.deepEqual(statFacts("score", { ...ZERO, extra: { vectors: 0, vector_errors: 0 } }), []);
  assert.deepEqual(statFacts("collect_hh", { ...ZERO, seen: 10, limit_reached: true }), ["просмотрено 10", "достигнут предел страниц"]);
  assert.deepEqual(statFacts("collect_hh", { ...ZERO, seen: 10, limit_reached: true, note: "достигнут предел 110 страниц за прогон" }), ["просмотрено 10"]);
});

const ROW = { job: "collect_hh", started_at: at(29, 13, 18, 27), finished_at: at(29, 13, 19, 57), ok: true, stats: { ...ZERO, new: 99, seen: 100, pages: 90 }, error: null };

test("S34: готовый прогон — когда, сколько длился, итог, цифры", () => {
  const run = runInfo(ROW, NOW);
  assert.equal(run.job, "collect_hh");
  assert.equal(run.title, "Сбор с hh");
  assert.equal(run.never, false);
  assert.equal(run.status, "ok");
  assert.equal(run.statusText, "готово");
  assert.equal(run.tone, "g");
  assert.equal(run.when, "сегодня, 13:18");
  assert.equal(run.duration, "1 мин 30 с");
  assert.deepEqual(run.facts, ["просмотрено 100", "новых 99", "страниц 90"]);
  assert.equal(run.note, "");
  assert.equal(run.error, "");
});

test("S34: прогон без цифр — пометка вместо пустоты; note — отдельно с заглавной", () => {
  const quiet = runInfo({ ...ROW, job: "alerts", stats: ZERO }, NOW);
  assert.deepEqual(quiet.facts, []);
  assert.equal(quiet.empty, "без изменений");
  const withNote = runInfo({ ...ROW, job: "score", stats: { ...ZERO, note: "модель не настроена — разбор по шаблону" } }, NOW);
  assert.equal(withNote.note, "Модель не настроена — разбор по шаблону");
  assert.equal(withNote.empty, "");   // пустоту объясняет note
});

test("S34: ошибка — красный итог и текст ошибки", () => {
  const run = runInfo({ ...ROW, ok: false, error: "HhAuthError" }, NOW);
  assert.equal(run.status, "error");
  assert.equal(run.statusText, "ошибка");
  assert.equal(run.tone, "r");
  assert.equal(run.error, "HhAuthError");
});

test("S34: прогон готов, но часть записей не принята — итог «готово», замечание видно", () => {
  const run = runInfo({ ...ROW, error: "3 записей не принято" }, NOW);
  assert.equal(run.status, "ok");
  assert.equal(run.error, "3 записей не принято");
});

test("площадка показала проверку — «остановлена», а не просто ошибка", () => {
  const run = runInfo({ ...ROW, ok: false, error: "площадка показала проверку", stats: { ...ZERO, blocked: true } }, NOW);
  assert.equal(run.status, "blocked");
  assert.equal(run.statusText, "остановлена");
  assert.equal(run.tone, "o");
});

test("ход: задача идёт — длительность с начала до сейчас, цифр ещё нет", () => {
  const run = runInfo({ job: "rescore_profile", started_at: at(29, 14, 56, 30), finished_at: null, ok: null, stats: {}, error: null }, NOW);
  assert.equal(run.status, "running");
  assert.equal(run.statusText, "идёт");
  assert.equal(run.tone, "b");
  assert.equal(run.duration, "идёт 3 мин 30 с");
  assert.deepEqual(run.facts, []);
  assert.equal(run.empty, "Идёт сейчас, экран обновляется сам");
});

test("задача «идёт» давно: считаем прерванной, а не ждём вечно", () => {
  assert.equal(STALE_HOURS, 3);
  const run = runInfo({ job: "collect_hh", started_at: at(29, 8, 0), finished_at: null, ok: null, stats: {}, error: null }, NOW);
  assert.equal(run.status, "stale");
  assert.equal(run.statusText, "не завершилась");
  assert.equal(run.tone, "o");
  assert.match(run.error, /7 ч/);
  assert.equal(hasRunning([{ job: "collect_hh", started_at: at(29, 8, 0), finished_at: null, ok: null }], NOW), false);
});

test("S34: все задачи списком — незапускавшиеся помечены, незнакомые добавлены в конец", () => {
  const rows = jobRows([ROW, { ...ROW, job: "photos" }, { ...ROW, job: "тайная_задача" }], NOW);
  assert.equal(rows.length, 13);
  assert.deepEqual(rows.slice(0, 3).map((r) => r.job), ["collect_hh", "import_files", "mark_gone"]);
  assert.equal(rows[0].never, false);
  assert.equal(rows[1].never, true);
  assert.equal(rows[1].statusText, "ещё не запускалась");
  assert.equal(rows[1].title, "Выгрузки из кабинетов");
  assert.equal(rows[1].when, "");
  assert.equal(rows.find((r) => r.job === "photos").never, false);
  assert.equal(rows.at(-1).job, "тайная_задача");
  assert.equal(rows.at(-1).title, "тайная_задача");
  assert.equal(jobRows([], NOW).length, 12);
  assert.equal(jobRows(null, NOW).every((r) => r.never), true);
});

test("идёт ли что-то сейчас (для обновления экрана)", () => {
  const running = { job: "rescore_profile", started_at: at(29, 14, 59), finished_at: null, ok: null };
  assert.equal(hasRunning([ROW, running], NOW), true);
  assert.equal(hasRunning([ROW], NOW), false);
  assert.equal(hasRunning(null, NOW), false);
});

test("живые цифры базы: подписи и пробелы между тысячами", () => {
  const tiles = countTiles({ observations: 1240, persons: 55, candidates: 3, profiles: 2, signals: 8 });
  assert.deepEqual(tiles.map((t) => [t.key, t.label, t.value]), [
    ["observations", "Резюме в базе", "1 240"], ["persons", "Карточек людей", "55"], ["candidates", "Кандидатов", "3"],
    ["profiles", "Действующих профилей", "2"], ["signals", "Новых сигналов", "8"]]);
  assert.deepEqual(countTiles(null).map((t) => t.value), ["—", "—", "—", "—", "—"]);
});

test("надписи на самой схеме берут цифры из базы", () => {
  assert.equal(profilesLabel(83), "4. Оценка по 83 профилям");
  assert.equal(profilesLabel(1), "4. Оценка по 1 профилю");
  assert.equal(profilesLabel(21), "4. Оценка по 21 профилю");
  assert.equal(profilesLabel(2), "4. Оценка по 2 профилям");
  assert.equal(profilesLabel(undefined), "4. Оценка по профилям должностей");
  assert.equal(zonesTitle({ observations: 100, persons: 55, candidates: 1 }), "База на сервере компании — две зоны: 100 резюме, 55 карточек, 1 кандидат");
  assert.equal(zonesTitle({ observations: 1240, persons: 22, candidates: 3 }), "База на сервере компании — две зоны: 1 240 резюме, 22 карточки, 3 кандидата");
  assert.equal(zonesTitle(null), "База на сервере компании — две зоны");
});

test("после ручного запуска: появился ли новый прогон задачи (не по часам браузера, а по началу прогона)", () => {
  const old = { job: "collect_hh", started_at: at(29, 13, 0), finished_at: at(29, 13, 5), ok: true, stats: {} };
  const fresh = { job: "collect_hh", started_at: at(29, 18, 0), finished_at: null, ok: null, stats: {} };
  assert.equal(runChanged([old], "collect_hh", old.started_at), false);
  assert.equal(runChanged([fresh], "collect_hh", old.started_at), true);
  assert.equal(runChanged([], "collect_hh", old.started_at), false);
  assert.equal(runChanged([fresh], "collect_hh", null), true);   // раньше не запускалась — любой прогон новый
  assert.equal(runChanged([], "collect_hh", null), false);
  assert.equal(runChanged(null, "collect_hh", null), false);
});
