// «Вакансии»: тексты карточек, закрытие с комментарием, форма новой вакансии (S21–S23, S38)
import assert from "node:assert/strict";
import test from "node:test";

import { minFromError } from "../../web/js/decisions-model.js";
import {
  CLOSE_HINT, CLOSE_REASONS, buildVacancy, closeResult, closedText, daysBetween, isStarving, openText, originText,
  profileChoices, vacancyTitle,
} from "../../web/js/screens/vacancies-model.js";

const NOW = new Date(2026, 9, 29);
const OPEN = { id: 1, title: "Ведущий инженер-электронщик", headcount: 1, opened_at: "2026-10-11", target_date: null, state: "open",
  origin: "recruiter", norm_days: 45, open_days: 18, in_work: 8 };

test("заголовок карточки: название и число ставок словами", () => {
  assert.equal(vacancyTitle({ ...OPEN, headcount: 1 }), "Ведущий инженер-электронщик · 1 ставка");
  assert.equal(vacancyTitle({ ...OPEN, title: "Менеджер", headcount: 3 }), "Менеджер · 3 ставки");
  assert.equal(vacancyTitle({ ...OPEN, headcount: 5 }), "Ведущий инженер-электронщик · 5 ставок");
});

test("открытая вакансия: сколько дней из нормы, сколько в работе, откуда", () => {
  assert.equal(openText(OPEN, NOW), "Открыта 18 дней из 45 · в работе 8 человек · источник: завёл рекрутер");
  assert.equal(openText({ ...OPEN, open_days: 1, in_work: 1 }, NOW), "Открыта 1 день из 45 · в работе 1 человек · источник: завёл рекрутер");
  assert.equal(openText({ ...OPEN, open_days: 0, in_work: 2 }, NOW), "Открыта 0 дней из 45 · в работе 2 человека · источник: завёл рекрутер");
  assert.equal(openText({ ...OPEN, target_date: "2026-11-15" }, NOW),
    "Открыта 18 дней из 45 · в работе 8 человек · срок до 15 ноября · источник: завёл рекрутер");
});

test("источники вакансии по-русски", () => {
  assert.equal(originText("recruiter"), "завёл рекрутер");
  assert.equal(originText("request_1c"), "заявка из 1С");
  assert.equal(originText("hh_publication"), "публикация на hh");
  assert.equal(originText("что-то"), "что-то");
});

test("вакансия «голодает», когда открыта дольше нормы", () => {
  assert.equal(isStarving({ open_days: 46, norm_days: 45 }), true);
  assert.equal(isStarving({ open_days: 45, norm_days: 45 }), false);
  assert.equal(isStarving({ open_days: 3, norm_days: null }), false);
});

test("дни между датами", () => {
  assert.equal(daysBetween("2026-09-01", "2026-09-27"), 26);
  assert.equal(daysBetween("2026-10-29", "2026-10-29"), 0);
  assert.equal(daysBetween(null, "2026-10-29"), null);
});

test("закрытая вакансия: дата, срок работы и что подбор продолжается", () => {
  assert.equal(closedText({ opened_at: "2026-09-01", closed_at: "2026-09-27" }, NOW),
    "Закрыта 27 сентября, была открыта 26 дней. Подбор по позиции продолжается: профиль активен, наблюдение не остановлено.");
  assert.equal(closedText({ opened_at: "2026-09-01", closed_at: null }, NOW), "Закрыта. Подбор по позиции продолжается: профиль активен, наблюдение не остановлено.");
});

test("подсказки окна закрытия — из прототипа, со словами про наблюдение", () => {
  assert.ok(CLOSE_HINT.includes("наблюдение по должности продолжится"));
  assert.deepEqual(CLOSE_REASONS, ["человек вышел на работу", "потребность закрыта внутренним переводом", "заявка отозвана заказчиком",
    "ставка заморожена бюджетом", "требования изменились, нужен другой профиль"]);
  assert.equal(closeResult(230), "Вакансия закрыта, комментарий на 230 знаков записан. Подбор по должности продолжается: профиль активен, наблюдение не остановлено.");
});

test("422 на закрытии: число знаков берётся из текста сервера «Проверьте: комментарий не короче 150 знаков»", () => {
  assert.equal(minFromError({ status: 422, code: "validation", message: "Проверьте: комментарий не короче 150 знаков" }, 200), 150);
  assert.equal(minFromError({ code: "comment_too_short", message: "Комментарий не короче 250 знаков" }, 200), 250);
  assert.equal(minFromError({ status: 409, code: "conflict", message: "Нельзя: вакансия уже закрыта" }, 200), 200);
});

test("профили для новой вакансии: только утверждённые, по подразделению и названию", () => {
  const items = [
    { id: 3, title: "Инженер ОТК", dept: "ОТК", state: "approved" },
    { id: 1, title: "Ведущий инженер", dept: "Инженерные разработки", state: "approved" },
    { id: 2, title: "Черновик из ДИ", dept: "Продажи", state: "draft" },
    { id: 4, title: "Ждёт", dept: "Продажи", state: "pending" },
    { id: 5, title: "Аналитик", dept: "Инженерные разработки", state: "approved" },
  ];
  assert.deepEqual(profileChoices(items).map((p) => [p.id, p.label]), [
    [5, "Аналитик — Инженерные разработки"], [1, "Ведущий инженер — Инженерные разработки"], [3, "Инженер ОТК — ОТК"]]);
  assert.deepEqual(profileChoices([]), []);
  assert.deepEqual(profileChoices(null), []);
});

test("S21: форма новой вакансии собирает тело запроса и проверяет поля", () => {
  assert.deepEqual(buildVacancy({ profileId: "4", title: "  Инженер ОТК  ", headcount: "2", targetDate: "2026-12-01" }),
    { ok: true, payload: { role_profile_id: 4, title: "Инженер ОТК", headcount: 2, target_date: "2026-12-01" } });
  assert.deepEqual(buildVacancy({ profileId: "4", title: "Инженер", headcount: "1", targetDate: "" }),
    { ok: true, payload: { role_profile_id: 4, title: "Инженер", headcount: 1 } });   // без срока поле не уходит
  assert.equal(buildVacancy({ profileId: "", title: "Инженер", headcount: "1" }).error, "Выберите профиль должности");
  assert.equal(buildVacancy({ profileId: "4", title: "   ", headcount: "1" }).error, "Введите название вакансии");
  assert.equal(buildVacancy({ profileId: "4", title: "А", headcount: "0" }).error, "Ставок должно быть не меньше одной");
  assert.equal(buildVacancy({ profileId: "4", title: "А", headcount: "1.5" }).error, "Ставок должно быть не меньше одной");
  assert.equal(buildVacancy({ profileId: "4", title: "А", headcount: "" }).error, "Ставок должно быть не меньше одной");
});
