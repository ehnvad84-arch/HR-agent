// Экран «Доступ» (S1, S2, S3): пользователи, роли, «вручную», выгрузка сотрудников из 1С, одноразовый код
import assert from "node:assert/strict";
import test from "node:test";

import {
  ROLE_ORDER, canIssueCode, changesFor, codeAriaLabel, codeExpiryText, filterUsers, groupCode, loginStatus, newUserBody, newUserProblem, resultText, roleText,
  sortUsers, syncLine, unitOptions,
} from "../../web/js/screens/access-model.js";

const NOW = new Date(2026, 9, 30, 12, 0);
const at = (d, h, m = 0) => new Date(2026, 9, d, h, m).toISOString();
const person = (over = {}) => ({ id: 1, login: "ivanov", full_name: "Иванов Иван Иванович", position: "Начальник отдела главного механика", org_unit_id: 4,
  org_unit: "Отдел главного механика", role: "hiring_manager", manual: false, last_login_at: null, code_expires_at: null, is_active: true, has_password: false, ...over });

test("роли по-русски, в порядке выбора", () => {
  assert.deepEqual(ROLE_ORDER, ["recruiter", "head_recruiter", "hiring_manager", "director", "no_access"]);
  assert.equal(roleText("recruiter"), "специалист отдела персонала");
  assert.equal(roleText("head_recruiter"), "руководитель отдела персонала");
  assert.equal(roleText("hiring_manager"), "руководитель подразделения");
  assert.equal(roleText("director"), "директор");
  assert.equal(roleText("no_access"), "без доступа");
  assert.equal(roleText("agent"), "agent");
});

test("«Вход»: уволен, код до …, входил …, не входил", () => {
  assert.equal(loginStatus(person({ is_active: false, last_login_at: at(29, 9) }), NOW), "уволен");
  assert.equal(loginStatus(person({ code_expires_at: at(31, 14, 0) }), NOW), "код до 31.10 14:00");
  assert.equal(loginStatus(person({ last_login_at: at(29, 9, 5) }), NOW), "входил 29.10");
  assert.equal(loginStatus(person(), NOW), "не входил");
  // код уже истёк — это не «код до»; смотрим, входил ли
  assert.equal(loginStatus(person({ code_expires_at: at(29, 8), last_login_at: at(28, 10) }), NOW), "входил 28.10");
  assert.equal(loginStatus(person({ code_expires_at: at(29, 8) }), NOW), "не входил");
});

test("S1: последняя выгрузка одной строкой, ошибка — отдельно текстом сервера", () => {
  assert.equal(syncLine({ received_at: at(30, 6, 0), new: 3, changed: 5, fired: 1 }), "Последняя выгрузка 30.10 06:00 · новых 3, изменено 5, уволено 1");
  assert.equal(syncLine({ received_at: at(30, 6, 0), new: 0, changed: 0, fired: 0 }), "Последняя выгрузка 30.10 06:00 · новых 0, изменено 0, уволено 0");
  assert.equal(syncLine(null), "Выгрузок сотрудников из 1С ещё не было");
});

test("S1: итог выгрузки — тост", () => {
  assert.equal(resultText({ state: "applied", new: 3, changed: 5, fired: 1 }), "Выгрузка сотрудников применена: новых 3, изменено 5, уволено 1");
  assert.equal(resultText({ state: "same", new: 0, changed: 0, fired: 0 }), "Эта выгрузка уже применена");
  assert.equal(resultText({ state: "applied" }), "Выгрузка сотрудников применена: новых 0, изменено 0, уволено 0");
});

const USERS = [
  person({ id: 1, full_name: "Иванов Иван Иванович", login: "ivanov", role: "hiring_manager" }),
  person({ id: 2, full_name: "Петрова Анна", login: "petrova", position: "Специалист по подбору", org_unit: "Отдел управления персоналом", role: "recruiter" }),
  person({ id: 3, full_name: "Абрамов Пётр", login: "abramov", position: "Директор по производству", org_unit: "Основное подразделение", role: "director", manual: true }),
  person({ id: 4, full_name: "Волкова Ксения", login: "volkova", position: "Начальник ОУП", org_unit: "Отдел управления персоналом", role: "head_recruiter" }),
  person({ id: 5, full_name: "Яковлев Олег", login: "yakovlev", position: "Слесарь", org_unit: "Цех сборки", role: "no_access" }),
];

test("поиск по ФИО, логину, должности и подразделению; без регистра и «ё/е»", () => {
  assert.deepEqual(filterUsers(USERS, { q: "ПЕТР" }).map((u) => u.id), [2, 3]);   // «Петрова» и «Абрамов Пётр»
  assert.deepEqual(filterUsers(USERS, { q: "volkova" }).map((u) => u.id), [4]);
  assert.deepEqual(filterUsers(USERS, { q: "слесарь" }).map((u) => u.id), [5]);
  assert.deepEqual(filterUsers(USERS, { q: "управления персоналом" }).map((u) => u.id), [2, 4]);
  assert.deepEqual(filterUsers(USERS, { q: "  " }).map((u) => u.id), [1, 2, 3, 4, 5]);
  assert.deepEqual(filterUsers(USERS, { q: "нетакого" }), []);
});

test("фильтры по роли и «только руководители»", () => {
  assert.deepEqual(filterUsers(USERS, { role: "recruiter" }).map((u) => u.id), [2]);
  assert.deepEqual(filterUsers(USERS, { role: "" }).length, 5);
  assert.deepEqual(filterUsers(USERS, { managersOnly: true }).map((u) => u.id), [1, 3, 4]);   // руководители подразделений, директор и руководитель отдела персонала
  assert.deepEqual(filterUsers(USERS, { managersOnly: true, q: "иванов" }).map((u) => u.id), [1]);
});

test("список — по алфавиту ФИО", () => {
  assert.deepEqual(sortUsers(USERS).map((u) => u.full_name), ["Абрамов Пётр", "Волкова Ксения", "Иванов Иван Иванович", "Петрова Анна", "Яковлев Олег"]);
  assert.equal(USERS[0].id, 1);   // исходный массив не меняется
});

test("подразделения для выбора — деревом с отступами, в порядке 1С, пустые тоже", () => {
  const units = [{ id: 1, parent_id: null, title: "Основное подразделение", order: 1 }, { id: 2, parent_id: 1, title: "Производство", order: 2 },
    { id: 3, parent_id: 2, title: "Цех сборки", order: 3 }, { id: 4, parent_id: 1, title: "Отдел персонала", order: 4 }, { id: 5, parent_id: null, title: "Склад", order: 5 }];
  assert.deepEqual(unitOptions(units).map((o) => [o.id, o.depth, o.label]), [
    [1, 0, "Основное подразделение"], [2, 1, "  Производство"], [3, 2, "    Цех сборки"], [4, 1, "  Отдел персонала"], [5, 0, "Склад"]]);
  assert.deepEqual(unitOptions(null), []);
  assert.equal(unitOptions([{ id: 9, parent_id: 77, title: "Сирота", order: 1 }]).length, 1);   // родитель неизвестен — верхний уровень
});

test("S2: в запрос уходит только изменённое; «вернуть правило 1С» — manual: false", () => {
  const user = person({ role: "hiring_manager", org_unit_id: 4, manual: false });
  assert.deepEqual(changesFor(user, { role: "hiring_manager", org_unit_id: "4", reset: false }), {});
  assert.deepEqual(changesFor(user, { role: "recruiter", org_unit_id: "4", reset: false }), { role: "recruiter" });
  assert.deepEqual(changesFor(user, { role: "hiring_manager", org_unit_id: "7", reset: false }), { org_unit_id: 7 });
  assert.deepEqual(changesFor(user, { role: "director", org_unit_id: "7", reset: false }), { role: "director", org_unit_id: 7 });
  assert.deepEqual(changesFor(person({ org_unit_id: null }), { role: "hiring_manager", org_unit_id: "", reset: false }), {});
  assert.deepEqual(changesFor(user, { role: "hiring_manager", org_unit_id: "", reset: false }), { org_unit_id: null });
  assert.deepEqual(changesFor(person({ manual: true }), { role: "hiring_manager", org_unit_id: "4", reset: true }), { manual: false });
  assert.deepEqual(changesFor(person({ manual: false }), { role: "hiring_manager", org_unit_id: "4", reset: true }), {});   // возвращать нечего
});

test("S2: новый человек — логин и ФИО обязательны, тело запроса", () => {
  const form = { login: " petrov ", full_name: " Петров Пётр ", position: " Начальник цеха ", org_unit_id: "3", role: "hiring_manager" };
  assert.equal(newUserProblem(form), null);
  assert.deepEqual(newUserBody(form), { login: "petrov", full_name: "Петров Пётр", position: "Начальник цеха", org_unit_id: 3, role: "hiring_manager" });
  assert.deepEqual(newUserBody({ ...form, org_unit_id: "", position: "" }), { login: "petrov", full_name: "Петров Пётр", position: "", org_unit_id: null, role: "hiring_manager" });
  assert.equal(newUserProblem({ ...form, login: "  " }), "Введите логин");
  assert.equal(newUserProblem({ ...form, login: "иван ов" }), "Логин — латиницей, цифрами, точкой, дефисом и подчёркиванием");
  assert.equal(newUserProblem({ ...form, full_name: "" }), "Введите ФИО");
  assert.equal(newUserProblem({ ...form, role: "" }), "Выберите роль");
});

test("S3: код — когда его можно выдать", () => {
  assert.deepEqual(canIssueCode(person()), { ok: true, reason: "" });
  assert.deepEqual(canIssueCode(person({ is_active: false })), { ok: false, reason: "Человек уволен: код выдать нельзя" });
  assert.deepEqual(canIssueCode(person({ role: "no_access" })), { ok: false, reason: "У человека нет доступа: сначала выберите роль" });
});

test("S3: код читается по знакам, показывается группами, срок — «до 02.11 14:00»", () => {
  assert.equal(codeAriaLabel("K7M2Q9XW4P"), "K 7 M 2 Q 9 X W 4 P");
  assert.equal(groupCode("K7M2Q9XW4P"), "K7M2Q 9XW4P");
  assert.equal(groupCode("ABC"), "ABC");
  assert.equal(codeExpiryText(new Date(2026, 10, 2, 14, 0).toISOString()), "Действует до 02.11 14:00");
});
