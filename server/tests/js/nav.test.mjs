// Вкладки и шапка по роли из /api/me; «Доступ не выдан»; пустой кабинет без подразделения (S4, S17)
import assert from "node:assert/strict";
import test from "node:test";

import { SCREENS } from "../../web/js/screens/index.js";
import {
  NO_ACCESS_TEXT, NO_UNIT_TEXT, ROLE_TITLES, activeTab, hasFullBase, headerLine, isCabinet, navFor, noUnit,
} from "../../web/js/nav-model.js";

const user = (role, extra = {}) => ({ id: 1, login: "x", full_name: "Иванов И. И.", role, ...extra });
const can = (...perms) => (p) => perms.includes(p);

test("роли по-русски — как в экране «Доступ»", () => {
  assert.deepEqual(ROLE_TITLES, { recruiter: "специалист отдела персонала", head_recruiter: "руководитель отдела персонала",
    hiring_manager: "руководитель подразделения", director: "директор", no_access: "без доступа" });
});

test("специалист: вкладки как раньше, «Доступа» нет", () => {
  const nav = navFor(user("recruiter"), can("base.read", "base.all"));
  assert.equal(nav.kind, "staff");
  assert.deepEqual(nav.tabs, ["today", "base", "vacancies", "profiles", "learn", "logs", "funnel", "arch"]);
  assert.deepEqual(nav.titles, {});
});

test("руководитель отдела персонала: то же и «Доступ» после «Журнала» — по праву access.manage", () => {
  const nav = navFor(user("head_recruiter"), can("base.read", "base.all", "access.manage"));
  assert.deepEqual(nav.tabs, ["today", "base", "vacancies", "profiles", "learn", "logs", "access", "funnel", "arch"]);
  // право решает, а не название роли
  assert.equal(navFor(user("head_recruiter"), can("base.read")).tabs.includes("access"), false);
  assert.equal(navFor(user("recruiter"), can("base.read", "access.manage")).tabs.includes("access"), true);
  // специалисту — урезанный «Доступ» (только выдача кода): право invite.issue
  assert.equal(navFor(user("recruiter"), can("base.read", "invite.issue")).tabs.includes("access"), true);
  assert.equal(navFor(user("recruiter"), can("base.read")).tabs.includes("access"), false);
});

test("руководитель подразделения: Сегодня · Мои вакансии · Мои должности · База · Обучение агента · Схема (справка)", () => {
  const nav = navFor(user("hiring_manager"), can("base.read"));
  assert.equal(nav.kind, "cabinet");
  assert.deepEqual(nav.tabs, ["today", "vacancies", "profiles", "base", "learn", "arch"]);
  assert.deepEqual(nav.titles, { vacancies: "Мои вакансии", profiles: "Мои должности" });
});

test("директор — как руководитель, даже с правом на журнал", () => {
  const nav = navFor(user("director"), can("base.read", "report.read", "org.read"));
  assert.equal(nav.kind, "cabinet");
  assert.deepEqual(nav.tabs, ["today", "vacancies", "profiles", "base", "learn", "arch"]);
  assert.equal(nav.tabs.includes("logs"), false);
});

test("S4: без роли и без входа вкладок нет", () => {
  assert.equal(navFor(user("no_access"), can()).kind, "none");
  assert.deepEqual(navFor(user("no_access"), can()).tabs, []);
  assert.equal(navFor(null, can()).kind, "none");
});

test("досье и воронка у руководителя — без вкладки: подсвечивается список, откуда пришли", () => {
  const cabinet = navFor(user("hiring_manager"), can("base.read"));
  assert.equal(activeTab("today", cabinet), "today");
  assert.equal(activeTab("person", cabinet), "base");
  assert.equal(activeTab("funnel", cabinet), "vacancies");
  assert.equal(activeTab("logs", cabinet), null);
  const staff = navFor(user("recruiter"), can("base.read"));
  assert.equal(activeTab("person", staff), "base");
  assert.equal(activeTab("funnel", staff), "funnel");
  assert.equal(activeTab("access", staff), null);   // вкладки нет
});

test("шапка: подразделение руководителя и «Вся компания» у директора", () => {
  assert.equal(headerLine(user("hiring_manager", { org_unit: "Отдел главного механика" }), { all: false, units: [4, 5], reason: null }), "Отдел главного механика и вложенные");
  assert.equal(headerLine(user("hiring_manager"), { all: false, units: [4], reason: null, unit_title: "Цех сборки" }), "Цех сборки и вложенные");
  assert.equal(headerLine(user("hiring_manager"), { all: false, units: [4], reason: null }), "Своё подразделение и вложенные");
  assert.equal(headerLine(user("director"), { all: false, units: [1], reason: null }), "Вся компания");
  assert.equal(headerLine(user("director"), null), "Вся компания");
  assert.equal(headerLine(user("recruiter"), { all: true, units: [], reason: null }), "");   // у специалиста подписи нет
  assert.equal(headerLine(user("hiring_manager"), { all: false, units: [], reason: "no_unit" }), "");   // подразделения нет — подписывать нечем
});

test("S17: руководитель без подразделения — пустой кабинет с текстом", () => {
  assert.equal(NO_UNIT_TEXT, "Подразделение не указано — обратитесь в отдел персонала");
  assert.equal(noUnit(user("hiring_manager"), { all: false, units: [], reason: "no_unit" }), true);
  assert.equal(noUnit(user("hiring_manager"), { all: false, units: [4], reason: null }), false);
  assert.equal(noUnit(user("hiring_manager"), null), false);
  assert.equal(noUnit(user("recruiter"), { all: true, units: [], reason: "no_unit" }), false);   // у отдела персонала кабинета нет
});

test("S4: текст страницы без доступа", () => {
  assert.equal(NO_ACCESS_TEXT, "Доступ не выдан, обратитесь в отдел персонала");
});

test("полная база и ход задач — у отдела персонала (право base.all, а пока сервер его не знает — по роли)", () => {
  assert.equal(hasFullBase(user("recruiter"), can("base.all")), true);
  assert.equal(hasFullBase(user("hiring_manager"), can("base.read")), false);
  assert.equal(hasFullBase(user("director"), can("base.read", "report.read")), false);
  assert.equal(hasFullBase(user("recruiter"), can("base.read")), true);   // сервер без base.all: отдел персонала по роли
  assert.equal(hasFullBase(user("head_recruiter"), can()), true);
  assert.equal(hasFullBase(null, can("base.all")), true);
});

test("у каждой вкладки любой роли есть экран", () => {
  for (const nav of [navFor(user("recruiter"), can("access.manage")), navFor(user("hiring_manager"), can()), navFor(user("director"), can())]) {
    for (const name of nav.tabs) assert.ok(SCREENS[name], name);
  }
});

test("кабинет — руководитель подразделения и директор; отдел персонала — нет", () => {
  assert.equal(isCabinet(user("hiring_manager")), true);
  assert.equal(isCabinet(user("director")), true);
  assert.equal(isCabinet(user("recruiter")), false);
  assert.equal(isCabinet(user("head_recruiter")), false);
  assert.equal(isCabinet(null), false);
});
