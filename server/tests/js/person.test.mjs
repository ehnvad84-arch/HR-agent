// «Досье»: разбор ответа /api/person/{id}/dossier без DOM (S6–S10)
import assert from "node:assert/strict";
import test from "node:test";

import { dateTimeLabel, monthYear, moneyText, yearsText } from "../../web/js/format.js";
import {
  bestScore, confidenceInfo, decisionTitle, diffLines, eventDetail, eventTitle, explanationBlocks, explainTargets,
  featureRows, headerTitle, periodText, personState, socialInfo, timeline,
} from "../../web/js/screens/person-model.js";

const norm = (s) => s.replace(/[  ]/g, " ");

test("S6: признаки — «сколько из скольких», крупные веса сверху, служебные и нулевые скрыты", () => {
  const rows = featureRows({
    geo: { max: 10, label: "География", detail: "Краснодар", points: 10 },
    _meta: { max: 5, label: "служебное", detail: "", points: 1 },
    zero: { max: 0, label: "Без веса", detail: "", points: 0 },
    keywords: { max: 25, label: "Ключевые слова", detail: "Altium", points: 17 },
    salary: { max: 10, label: "Ожидания против вилки", detail: "200 000 ₽", points: 5 },
  });
  assert.deepEqual(rows.map((r) => r.label), ["Ключевые слова", "География", "Ожидания против вилки"]);
  assert.deepEqual(rows.map((r) => [r.points, r.max]), [[17, 25], [10, 10], [5, 10]]);
  assert.deepEqual(rows.map((r) => r.low), [true, false, true]);   // ниже 75 % — оранжевая полоса
  assert.deepEqual(rows.map((r) => r.pct), [68, 100, 50]);
});

test("признаки приходят и списком, и пустыми", () => {
  assert.equal(featureRows([{ label: "A", points: 3, max: 4, detail: "d" }])[0].pct, 75);
  assert.deepEqual(featureRows(null), []);
  assert.deepEqual(featureRows({}), []);
});

test("разбор: три строки шаблона с подписями; без подписи — просто текст", () => {
  assert.deepEqual(explanationBlocks("Сильнее всего: опыт — 9 лет.\nЧего не хватает: ожидания.\nСпросить: Что входит в доход?"), [
    { label: "Сильнее всего", text: "опыт — 9 лет." },
    { label: "Чего не хватает", text: "ожидания." },
    { label: "Спросить", text: "Что входит в доход?" },
  ]);
  assert.deepEqual(explanationBlocks("Явных пробелов по профилю не видно."), [{ label: null, text: "Явных пробелов по профилю не видно." }]);
  assert.deepEqual(explanationBlocks(""), []);
  assert.deepEqual(explanationBlocks(null), []);
});

test("S7: модель просим только там, где разбор ещё по шаблону", () => {
  const matches = [
    { role_profile_id: 1, model: "template" },
    { role_profile_id: 2, model: "gigachat/explain_score@v1" },
    { role_profile_id: 3, model: null },
    { role_profile_id: 4, model: "template" },
  ];
  assert.deepEqual(explainTargets(matches).map((m) => m.role_profile_id), [1, 4]);
});

test("лучший балл: из карточки или из оценок", () => {
  assert.equal(bestScore({ best_score: 88 }, []), 88);
  assert.equal(bestScore({ best_score: null }, [{ score: 70 }, { score: 82 }]), 82);
  assert.equal(bestScore({}, []), null);
});

test("заголовок: имя и ярлык, а без имени — один ярлык", () => {
  assert.equal(headerTitle({ name: "Иванов Иван", label: "Инженер, Краснодар" }), "Иванов Иван — Инженер, Краснодар");
  assert.equal(headerTitle({ name: null, label: "Инженер, Краснодар" }), "Инженер, Краснодар");
  assert.equal(headerTitle({}), "Без имени");
  // у кандидата без имени в name лежит тот же ярлык: не «ярлык — ярлык»
  assert.equal(headerTitle({ name: "Инженер, Томск", label: "Инженер, Томск" }), "Инженер, Томск");
});

test("состояние человека: кандидат, веду особо, наблюдение", () => {
  assert.deepEqual(personState({ watch: "observed" }, null), { text: "наблюдение", tone: "b" });
  assert.deepEqual(personState({ watch: "watched" }, null), { text: "веду особо", tone: "v" });
  assert.deepEqual(personState({ watch: "dropped" }, null), { text: "не наш: вне выдачи", tone: "r" });
  assert.deepEqual(personState({ watch: "observed" }, { status: "first_message_sent" }), { text: "кандидат: Написали", tone: "g" });
});

test("лента: события и версии резюме вперемешку, свежие сверху", () => {
  const items = timeline({
    events: [
      { kind: "new", detail: { score: 80, source: "hh" }, happened_at: "2026-10-29T13:19:00+00:00" },
      { kind: "opened_for_search", detail: {}, happened_at: "2026-11-02T06:32:00+00:00" },
    ],
    versions: [
      { captured_at: "2026-10-30T09:00:00+00:00", diff: { skills: { added: ["Git"], removed: [] } } },
      { captured_at: "2026-10-29T13:19:00+00:00", diff: null },
    ],
  });
  // при одинаковом времени событие стоит выше версии резюме
  assert.deepEqual(items.map((i) => i.title), [
    "Открыл резюме для поиска", "Резюме изменилось", "Попал в поле зрения агента", "Сохранена первая версия резюме",
  ]);
  assert.equal(items[1].detail, "Навыки: добавил Git");
  assert.ok(items.some((i) => i.title === "Попал в поле зрения агента" && i.detail === "балл 80"));
  assert.ok(items.some((i) => i.title === "Сохранена первая версия резюме" && i.detail === null));
  assert.ok(items[0].at > items[1].at && items[1].at > items[2].at);
});

test("лента: пустая — пустой список", () => {
  assert.deepEqual(timeline({ events: [], versions: [] }), []);
  assert.deepEqual(timeline({}), []);
});

test("названия событий и решений по-русски, незнакомое не ломает", () => {
  assert.equal(eventTitle("employer_changed"), "Сменил работодателя");
  assert.equal(eventTitle("zzz"), "Событие");
  assert.equal(eventDetail({ kind: "new", detail: { score: 77 } }), "балл 77");
  assert.equal(eventDetail({ kind: "salary_changed", detail: { from: 150000, to: 190000 } }), "150 000 → 190 000");
  assert.equal(eventDetail({ kind: "opened_for_search", detail: {} }), null);
  assert.equal(eventDetail({ kind: "x", detail: null }), null);
  assert.equal(decisionTitle("postpone"), "Отложил");
  assert.equal(decisionTitle("reject"), "Отметил «не наш»");
  assert.equal(decisionTitle("q"), "q");
});

test("изменения резюме: навыки, переезд, деньги, текст", () => {
  const lines = diffLines({
    skills: { added: ["Figma", "Git"], removed: ["Perl"] },
    relocation_ready: { to: true, from: null },
    salary_expect: { from: 150000, to: 190000 },
    title: { from: "Инженер", to: "Ведущий инженер" },
  }).map(norm);
  assert.deepEqual(lines, [
    "Навыки: добавил Figma, Git; убрал Perl",
    "Готовность к переезду: да",
    "Ожидания по зарплате: 150 000 ₽ → 190 000 ₽",
    "Должность: Инженер → Ведущий инженер",
  ]);
  assert.deepEqual(diffLines({ skills: { added: ["a", "b", "c", "d", "e", "f", "g"], removed: [] } }),
    ["Навыки: добавил a, b, c, d, e и ещё 2"]);
  assert.deepEqual(diffLines(null), []);
  assert.deepEqual(diffLines({ неведомое: { from: 1, to: 2 } }), ["неведомое: 1 → 2"]);
});

test("следы в сети: название, значок и слово уверенности", () => {
  assert.deepEqual(socialInfo("github"), { title: "GitHub", ico: "gh", abbr: "GH" });
  assert.deepEqual(socialInfo("habr"), { title: "Хабр Карьера", ico: "ha", abbr: "ХК" });
  assert.deepEqual(socialInfo("linkedin"), { title: "LinkedIn", ico: "in", abbr: "in" });
  assert.deepEqual(socialInfo("что-то"), { title: "Другое", ico: "ot", abbr: "•" });
  assert.deepEqual(confidenceInfo("confirmed"), { text: "подтверждено", tone: "g" });
  assert.deepEqual(confidenceInfo("probable"), { text: "вероятно", tone: "o" });
  assert.deepEqual(confidenceInfo("rejected"), { text: "отклонено", tone: "r" });
});

test("места работы: период по месяцам", () => {
  assert.equal(periodText("2024-01-01", "2026-04-01"), "январь 2024 — апрель 2026");
  assert.equal(periodText("2026-01-01", null), "январь 2026 — по настоящее время");
  assert.equal(periodText(null, null), "");
  assert.equal(periodText("2019", "2021-06-15"), "2019 — июнь 2021");
});

test("числа и даты: годы опыта, деньги, дата с временем", () => {
  assert.equal(yearsText(9), "9 лет");
  assert.equal(yearsText(1), "1 год");
  assert.equal(yearsText(3), "3 года");
  assert.equal(yearsText(21.8), "21,8 года");
  assert.equal(yearsText(null), "");
  assert.equal(norm(moneyText(190000)), "190 000 ₽");
  assert.equal(moneyText(null), "");
  assert.equal(monthYear(new Date(2026, 9, 1)), "октябрь 2026");
  assert.equal(dateTimeLabel(new Date(2026, 9, 29, 13, 5), new Date(2026, 9, 30)), "29 октября, 13:05");
  assert.equal(dateTimeLabel(new Date(2025, 11, 31, 9, 0), new Date(2026, 9, 30)), "31 декабря 2025, 09:00");
  assert.equal(dateTimeLabel("не дата"), "");
});
