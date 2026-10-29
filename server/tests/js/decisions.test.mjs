// Окна решений: счётчик знаков, подсказки-причины, выбор вакансии для «В работу» (S11–S13)
import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_MIN, appendReason, canSave, countChars, counterText, minFromError, pickVacancies, profileOptions, resultText,
  takenText,
} from "../../web/js/decisions-model.js";

test("S13: считаем знаки без пробелов по краям, по символам, а не по кодовым единицам", () => {
  assert.equal(countChars("  привет  "), 6);
  assert.equal(countChars(""), 0);
  assert.equal(countChars(null), 0);
  assert.equal(countChars("😀😀"), 2);   // сервер считает символы Python, а не UTF-16
});

test("S13: «Сохранить» доступна с 200 знаков", () => {
  assert.equal(DEFAULT_MIN, 200);
  assert.equal(canSave("а".repeat(199), 200), false);
  assert.equal(canSave("а".repeat(200), 200), true);
  assert.equal(canSave(`  ${"а".repeat(199)}  `, 200), false);   // пробелы по краям не считаются
  assert.equal(canSave("а".repeat(40), 40), true);
});

test("счётчик: «N из 200 символов»", () => {
  assert.equal(counterText("абв", 200), "3 из 200 символов");
  assert.equal(counterText("", 200), "0 из 200 символов");
});

test("порог из ответа сервера: «Комментарий не короче 150 знаков»", () => {
  assert.equal(minFromError({ code: "comment_too_short", message: "Комментарий не короче 150 знаков" }, 200), 150);
  assert.equal(minFromError({ code: "comment_too_short", message: "Комментарий не короче знаков" }, 200), 200);
  assert.equal(minFromError({ code: "conflict", message: "Нельзя: 5 знаков" }, 200), 200);
  assert.equal(minFromError(null, 200), 200);
});

test("подсказка-причина добавляется фразой с большой буквы и точкой", () => {
  assert.equal(appendReason("", "нет профильного опыта"), "Нет профильного опыта. ");
  assert.equal(appendReason("Уже написано.", "частая смена работы"), "Уже написано. Частая смена работы. ");
  assert.equal(appendReason("  ", "ожидания выше вилки"), "Ожидания выше вилки. ");
});

const OPEN = [
  { id: 1, title: "Инженер А", role_code: "eng", state: "open", opened_at: "2026-10-01" },
  { id: 2, title: "Инженер Б", role_code: "eng", state: "open", opened_at: "2026-09-15" },
  { id: 3, title: "Продажи", role_code: "sales", state: "open", opened_at: "2026-08-01" },
  { id: 4, title: "Инженер на паузе", role_code: "eng", state: "on_hold", opened_at: "2026-07-01" },
  { id: 5, title: "Другое", role_code: "hr", state: "open", opened_at: "2026-07-01" },
];

test("S11: вакансии, куда можно поставить человека — открытые по кодам его профилей", () => {
  const matches = [{ role_code: "eng", score: 80 }, { role_code: "sales", score: 70 }];
  assert.deepEqual(pickVacancies(OPEN, matches).map((v) => v.id), [2, 1, 3]);   // лучший балл, затем давно открытые
});

test("S11: одна подходящая вакансия — выбора нет", () => {
  assert.deepEqual(pickVacancies(OPEN, [{ role_code: "sales", score: 75 }]).map((v) => v.id), [3]);
});

test("S11: подходящих нет (или вакансия на паузе) — резерв без вакансии", () => {
  assert.deepEqual(pickVacancies(OPEN, [{ role_code: "legal", score: 90 }]), []);
  assert.deepEqual(pickVacancies([OPEN[3]], [{ role_code: "eng", score: 90 }]), []);
  assert.deepEqual(pickVacancies([], [{ role_code: "eng", score: 90 }]), []);
  assert.deepEqual(pickVacancies(OPEN, []), []);
});

test("баллы с лучшим выигрывают: вакансия того кода, где балл выше, стоит первой", () => {
  const matches = [{ role_code: "eng", score: 60 }, { role_code: "sales", score: 90 }, { role_code: "eng", score: 65 }];
  assert.deepEqual(pickVacancies(OPEN, matches).map((v) => v.id), [3, 2, 1]);
});

test("итог «В работу»: воронка вакансии или резерв", () => {
  assert.equal(takenText({ title: "Инженер А" }), "Взят в работу: воронка «Инженер А», этап «Отобран агентом». Контакт не открыт");
  assert.equal(takenText(null), "Взят в работу в резерв: открытой вакансии по профилю нет, привязать к вакансии можно позже. Контакт не открыт");
});

test("итог решения и подпись кнопки по тексту прототипа", () => {
  assert.equal(resultText("reject", 230), "Помечен «категорически нет», комментарий на 230 символов записан и отправлен агенту");
  assert.equal(resultText("postpone", 205), "Отложен на три месяца, комментарий на 205 символов записан и отправлен агенту");
});

test("профили для выбора: по убыванию балла; для «категорически нет» добавляется «по всем профилям»", () => {
  const matches = [
    { role_profile_id: 5, role_title: "Инженер", score: 70 },
    { role_profile_id: 6, role_title: "Схемотехник", score: 88 },
  ];
  assert.deepEqual(profileOptions(matches, "postpone"), [
    { id: 6, label: "«Схемотехник» — балл 88" }, { id: 5, label: "«Инженер» — балл 70" },
  ]);
  const reject = profileOptions(matches, "reject");
  assert.equal(reject.length, 3);
  assert.deepEqual(reject[2], { id: null, label: "По всем профилям — человек уйдёт из выдачи совсем" });
  assert.deepEqual(profileOptions([], "reject"), [{ id: null, label: "По всем профилям — человек уйдёт из выдачи совсем" }]);
  assert.deepEqual(profileOptions([], "postpone"), []);
});
