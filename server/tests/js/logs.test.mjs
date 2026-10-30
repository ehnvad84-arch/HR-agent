// «Журнал и отчёт»: журнал изменений, недельный отчёт, получатели (S32, S33, S40)
import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_DAYS, KINDS, PERMISSION_TITLES, PRESET_DAYS, RECIPIENTS_NOTE, defaultPeriod, describeChange, filterByKind, kindCounts, kindLabel,
  periodError, periodFor, periodLabel, recipientsText, reportView,
} from "../../web/js/screens/logs-model.js";

const NOW = new Date(2026, 9, 29, 15, 0);
const local = (d, h, m) => new Date(2026, 9, d, h, m).toISOString();   // время в часовом поясе, где идёт проверка

test("виды изменений — шесть, по-русски, как в отчёте сервера", () => {
  assert.deepEqual(KINDS.map(([code]) => code), ["threshold", "weight", "profile", "template", "source", "permission"]);
  assert.deepEqual(KINDS.map(([, label]) => label), ["Пороги", "Веса", "Профили должностей", "Шаблоны", "Источники и пределы", "Права"]);
  assert.equal(kindLabel("source"), "Источники и пределы");
  assert.equal(kindLabel("неведомое"), "неведомое");
});

test("S32: по умолчанию журнал за 30 дней, считая сегодняшний", () => {
  assert.equal(DEFAULT_DAYS, 30);
  assert.deepEqual(defaultPeriod(NOW), { from: "2026-09-30", to: "2026-10-29" });
  assert.deepEqual(defaultPeriod(new Date(2026, 2, 5)), { from: "2026-02-04", to: "2026-03-05" });
});

test("быстрый выбор периода: 7, 30 и 90 дней", () => {
  assert.deepEqual(PRESET_DAYS, [7, 30, 90]);
  assert.deepEqual(periodFor(7, NOW), { from: "2026-10-23", to: "2026-10-29" });   // как в недельном отчёте
  assert.deepEqual(periodFor(90, NOW), { from: "2026-08-01", to: "2026-10-29" });
  assert.deepEqual(periodFor(30, NOW), defaultPeriod(NOW));
});

test("период: обе даты, начало не позже конца", () => {
  assert.equal(periodError("2026-10-01", "2026-10-29"), "");
  assert.equal(periodError("2026-10-29", "2026-10-29"), "");
  assert.equal(periodError("", "2026-10-29"), "Укажите обе даты периода");
  assert.equal(periodError("2026-10-01", ""), "Укажите обе даты периода");
  assert.equal(periodError("не дата", "2026-10-29"), "Укажите обе даты периода");
  assert.equal(periodError("2026-10-30", "2026-10-29"), "Начало периода позже его конца");
});

test("подпись периода: «с 29 сентября по 29 октября»", () => {
  assert.equal(periodLabel("2026-09-29", "2026-10-29", NOW), "с 29 сентября по 29 октября");
  assert.equal(periodLabel("2025-12-01", "2026-01-31", NOW), "с 1 декабря 2025 по 31 января");
});

const ITEMS = [
  { id: 9, kind: "profile", applied_at: local(29, 14, 0), note: "утверждена версия 0.4 профиля «Ведущий инженер-электронщик»", author: "Приходько А.",
    proposal_id: null, before: { id: 5, state: "pending" }, after: { id: 5, state: "approved", version: "0.4" } },
  { id: 8, kind: "template", applied_at: local(29, 9, 0), note: "сводка: Пересмотреть тон писем для ОТК", author: "Приходько А.", proposal_id: 1,
    before: null, after: { accepted: "Пересмотреть тон писем для ОТК" } },
  { id: 7, kind: "profile", applied_at: local(28, 11, 0), note: "правка профиля «Ведущий инженер-электронщик», ждёт утверждения", author: "Волкова К.",
    proposal_id: null, before: { id: 4, version: "0.3" }, after: { id: 5, version: "0.4", state: "pending" } },
  { id: 6, kind: "source", applied_at: local(27, 10, 0), note: "пробный сбор", author: null, proposal_id: null,
    before: { crawl_max_pages: 110 }, after: { crawl_max_pages: "100" } },
];

test("S32: количество записей по видам — только те, что есть, «Все» первой", () => {
  assert.deepEqual(kindCounts(ITEMS), [
    { kind: "all", label: "Все", count: 4 },
    { kind: "profile", label: "Профили должностей", count: 2 },
    { kind: "template", label: "Шаблоны", count: 1 },
    { kind: "source", label: "Источники и пределы", count: 1 },
  ]);
  assert.deepEqual(kindCounts([]), [{ kind: "all", label: "Все", count: 0 }]);
});

test("S32: фильтр по виду; «Все» и неизвестный вид — без фильтра", () => {
  assert.deepEqual(filterByKind(ITEMS, "profile").map((i) => i.id), [9, 7]);
  assert.deepEqual(filterByKind(ITEMS, "permission"), []);
  assert.equal(filterByKind(ITEMS, "all").length, 4);
  assert.equal(filterByKind(ITEMS, "").length, 4);
});

test("строка журнала: время, что сделано, кто, вид; автора нет — «система»", () => {
  const line = describeChange(ITEMS[0], { now: NOW });
  assert.equal(line.when, "сегодня, 14:00");
  assert.equal(line.note, "утверждена версия 0.4 профиля «Ведущий инженер-электронщик»");
  assert.equal(line.author, "Приходько А.");
  assert.equal(line.kind, "profile");
  assert.equal(line.kindLabel, "Профили должностей");
  assert.equal(describeChange(ITEMS[3], { now: NOW }).author, "система");
  assert.equal(describeChange(ITEMS[3], { now: NOW }).when, "27 октября, 10:00");
});

test("порог: было → стало словами, без ключей", () => {
  const line = describeChange({ id: 1, kind: "threshold", applied_at: local(29, 9, 0), note: "сводка: порог", author: "А",
    before: { setting: "watch_threshold", value: 60 }, after: { setting: "watch_threshold", value: 65 } }, { now: NOW });
  assert.equal(line.summary, "Порог наблюдения: было 60 → стало 65");
  assert.deepEqual(line.details, []);
  const unknown = describeChange({ id: 1, kind: "threshold", applied_at: local(29, 9, 0), note: "", author: "А",
    before: { setting: "новая_настройка", value: 1 }, after: { setting: "новая_настройка", value: 2 } }, { now: NOW });
  assert.equal(unknown.summary, "Новая_настройка: было 1 → стало 2");
});

test("вес: признак и профиль по названию, прежнее значение из прежних весов", () => {
  const item = { id: 2, kind: "weight", applied_at: local(29, 9, 0), note: "сводка: вес", author: "А",
    before: { id: 5, weights: { open_code: 8, geo: 10 } }, after: { id: 6, profile_code: "hw_lead", feature: "open_code", value: 12 } };
  assert.equal(describeChange(item, { now: NOW, profileTitles: { hw_lead: "Ведущий инженер-электронщик" } }).summary,
    "Вес «Открытый код» в профиле «Ведущий инженер-электронщик»: было 8 → стало 12");
  assert.equal(describeChange(item, { now: NOW }).summary, "Вес «Открытый код» в профиле «hw_lead»: было 8 → стало 12");
  const fresh = { ...item, before: { id: 5, weights: {} } };
  assert.equal(describeChange(fresh, { now: NOW }).summary, "Вес «Открытый код» в профиле «hw_lead»: был не задан → стал 12");
});

test("профиль: утверждение — смена состояния версии", () => {
  const line = describeChange(ITEMS[0], { now: NOW });
  assert.equal(line.summary, "Версия 0.4: ждёт утверждения → действует");
  assert.deepEqual(line.details, []);
});

test("профиль: правка — что изменено коротко, подробности раскрываются", () => {
  const item = { id: 7, kind: "profile", applied_at: local(28, 11, 0), note: "правка профиля", author: "Волкова К.",
    before: { id: 4, version: "0.3", salary_min: 150000, keywords: ["схемотехника"], weights: { open_code: 8, geo: 10 } },
    after: { id: 5, version: "0.4", state: "pending", salary_min: 180000, keywords: ["схемотехника", "FPGA"], weights: { open_code: 12, geo: 10 } } };
  const line = describeChange(item, { now: NOW });
  assert.equal(line.summary, "Версия 0.3 → 0.4, ждёт утверждения. Изменено: вилка от, веса, ключевые слова");
  assert.deepEqual(line.details, [
    "Вилка от: 150 000 ₽ → 180 000 ₽",
    "Вес «Открытый код»: 8 → 12",
    "Ключевые слова: схемотехника → схемотехника, FPGA",
  ]);
});

test("профиль: условия-объекты и пустые значения читаются словами", () => {
  const item = { id: 7, kind: "profile", applied_at: local(28, 11, 0), note: "правка", author: "А",
    before: { id: 4, version: "1.0", salary_max: null, geo: "krasnodar", must_have: [{ text: "5 лет опыта" }] },
    after: { id: 5, version: "1.1", state: "pending", salary_max: 250000, geo: "remote_ru", must_have: [{ text: "5 лет опыта" }, { text: "Знание CAN" }] } };
  assert.deepEqual(describeChange(item, { now: NOW }).details, [
    "География: Краснодар → Удалённо по России",
    "Вилка до: не задана → 250 000 ₽",
    "Обязательные условия: 5 лет опыта → 5 лет опыта; Знание CAN",
  ]);
});

test("профиль: черновик из новой должностной инструкции", () => {
  const item = { id: 3, kind: "profile", applied_at: local(28, 11, 0), note: "новая ДИ", author: "А",
    before: { id: 4, version: "1.1" }, after: { id: 8, version: "1.2", state: "draft", job_doc_id: 3 } };
  assert.equal(describeChange(item, { now: NOW }).summary, "Черновая версия 1.2 из новой должностной инструкции (прежняя 1.1)");
});

test("профиль: добавлен новый", () => {
  const item = { id: 3, kind: "profile", applied_at: local(28, 11, 0), note: "пробный сбор", author: null,
    before: null, after: { code: "hw_lead", title: "Ведущий инженер-электронщик", version: "0.1", keywords: ["схемотехника", "Altium"] } };
  const line = describeChange(item, { now: NOW });
  assert.equal(line.summary, "Добавлен профиль «Ведущий инженер-электронщик», версия 0.1");
  assert.deepEqual(line.details, ["Ключевые слова: схемотехника, Altium"]);
});

test("шаблон: решение по сводке без автоматической правки — без пересказа", () => {
  const line = describeChange(ITEMS[1], { now: NOW });
  assert.equal(line.summary, null);
  assert.deepEqual(line.details, []);
});

test("права: сколько добавлено, по ролям — в раскрывающемся блоке", () => {
  const item = { id: 5, kind: "permission", applied_at: local(20, 9, 0), note: "миграция 002_workplace", author: null, before: null,
    after: { add: ["recruiter:contact.open", "recruiter:vacancy.edit", "head_recruiter:contact.open", "head_recruiter:feedback.write"] } };
  const line = describeChange(item, { now: NOW });
  assert.equal(line.summary, "Добавлено прав: 4");
  assert.deepEqual(line.details, [
    "Добавлено — Рекрутер: открывать контакт, править вакансии",
    "Добавлено — Руководитель отдела персонала: открывать контакт, писать в чат обучения",
  ]);
  const both = describeChange({ ...item, after: { add: ["recruiter:contact.open"], remove: ["director:report.read", "director:org.read"] } }, { now: NOW });
  assert.equal(both.summary, "Добавлено прав: 1, убрано: 2");
  assert.equal(both.details[1], "Убрано — Директор: читать журнал и отчёт, читать оргструктуру");
  assert.equal(PERMISSION_TITLES["contact.open"], "открывать контакт");
});

test("источники: одно значение — коротко; тип значения (110 и «110») не считается правкой", () => {
  assert.equal(describeChange(ITEMS[3], { now: NOW }).summary, "Страниц за прогон: было 110 → стало 100");
  const same = describeChange({ id: 1, kind: "source", applied_at: local(27, 10, 0), note: "пробный сбор", author: null,
    before: { crawl_max_pages: 110, hh_full_resume_per_run: 100 }, after: { crawl_max_pages: "110", hh_full_resume_per_run: "100" } }, { now: NOW });
  assert.equal(same.summary, "Заданы пределы: страниц за прогон — 110; полных резюме hh за прогон — 100");
  assert.deepEqual(same.details, []);
});

test("источники: несколько значений — число и строки в раскрывающемся блоке", () => {
  const line = describeChange({ id: 1, kind: "source", applied_at: local(27, 10, 0), note: "п", author: null,
    before: { crawl_max_pages: 2000, crawl_per_profile_pages: 5 }, after: { crawl_max_pages: 110, crawl_per_profile_pages: 1 } }, { now: NOW });
  assert.equal(line.summary, "Изменено значений: 2");
  assert.deepEqual(line.details, ["Страниц за прогон: 2000 → 110", "Страниц на профиль: 5 → 1"]);
});

test("незнакомая форма записи: ничего не теряется, но без сырого JSON", () => {
  const line = describeChange({ id: 1, kind: "template", applied_at: local(27, 10, 0), note: "п", author: "А",
    before: { тон: "строгий" }, after: { тон: "мягкий", длина: 3 } }, { now: NOW });
  assert.equal(line.summary, null);
  assert.deepEqual(line.details, ["Было: тон — строгий", "Стало: тон — мягкий; длина — 3"]);
  const empty = describeChange({ id: 1, kind: "template", applied_at: local(27, 10, 0), note: "п", author: "А", before: null, after: null }, { now: NOW });
  assert.deepEqual(empty.details, []);
});

const REPORT = {
  id: 4, period_from: "2026-10-22", period_to: "2026-10-29", changes: 5, recipients: ["ehnvad84@example.ru"], sent_at: local(29, 13, 5),
  body: { groups: [
    { kind: "profile", title: "Профили должностей", count: 3, items: [
      { at: local(29, 14, 0), note: "утверждена версия 0.4", author: "Приходько А." }, { at: local(28, 11, 0), note: "правка профиля", author: null },
      { at: local(27, 11, 0), note: "ещё правка", author: "Волкова К." }] },
    { kind: "source", title: "Источники и пределы", count: 1, items: [{ at: local(27, 10, 0), note: "пробный сбор", author: null }] },
    { kind: "threshold", title: "Пороги", count: 1, items: [{ at: local(26, 10, 0), note: "порог", author: "А" }] },
  ] },
};

test("S33: отчёт — период, число изменений, группы с записями, отметка об отправке", () => {
  const view = reportView(REPORT, NOW);
  assert.equal(view.hasReport, true);
  assert.equal(view.title, "Отчёт за 22–29 октября");
  assert.equal(view.summary, "5 изменений");
  assert.deepEqual(view.groups.map((g) => [g.kind, g.title, g.countText]), [
    ["profile", "Профили должностей", "3 изменения"], ["source", "Источники и пределы", "1 изменение"], ["threshold", "Пороги", "1 изменение"]]);
  assert.deepEqual(view.groups[0].items[1], { when: "вчера, 11:00", note: "правка профиля", author: "система" });
  assert.equal(view.sent, "Отправлен сегодня, 13:05 на ehnvad84@example.ru");
  assert.equal(view.sentTone, "g");
});

test("S33: отчёт не отправлен, получателей несколько", () => {
  const view = reportView({ ...REPORT, sent_at: null, recipients: [] }, NOW);
  assert.equal(view.sent, "Ещё не отправлен");
  assert.equal(view.sentTone, "o");
  const sent = reportView({ ...REPORT, recipients: ["a@x.ru", "b@x.ru"] }, NOW);
  assert.equal(sent.sent, "Отправлен сегодня, 13:05 на a@x.ru, b@x.ru");
});

test("S33: отчёта ещё не было и отчёт без изменений", () => {
  const none = reportView({ changes: 0, body: { groups: [] }, sent_at: null }, NOW);
  assert.equal(none.hasReport, false);
  assert.equal(none.title, "Отчёта пока нет");
  assert.equal(none.summary, "Отчёт ещё не формировался. Он собирается по пятницам в 17:00 или кнопкой «Сформировать отчёт сейчас»");
  assert.equal(none.sent, "");
  assert.deepEqual(none.groups, []);
  assert.equal(reportView(null, NOW).hasReport, false);
  const quiet = reportView({ id: 5, period_from: "2026-10-22", period_to: "2026-10-29", changes: 0, recipients: [], sent_at: null, body: { groups: [] } }, NOW);
  assert.equal(quiet.hasReport, true);
  assert.equal(quiet.summary, "За неделю изменений не было");
  assert.deepEqual(quiet.groups, []);
});

test("S40: получатели — только чтение, изменение в .env", () => {
  assert.equal(recipientsText(["a@x.ru", "b@x.ru"]), "a@x.ru, b@x.ru");
  assert.equal(recipientsText([]), "Адреса не заданы");
  assert.equal(recipientsText(null), "Адреса не заданы");
  assert.match(RECIPIENTS_NOTE, /\.env/);
  assert.match(RECIPIENTS_NOTE, /MAIL_TO/);
});
