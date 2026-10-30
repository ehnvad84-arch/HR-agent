// Оболочка рабочего места: вход, вкладки с адресом в хэше, переход между экранами.
// Сами экраны — js/screens/*.js (реестр в screens/index.js).
import { api } from "./js/api.js";
import { $, h, mount } from "./js/dom.js";
import { loadStatus } from "./js/load-status.js";
import { notify } from "./js/notify.js";
import { renderLoginPage, requestLogin } from "./js/login.js";
import { NO_ACCESS_TEXT, NO_UNIT_TEXT, activeTab, headerLine, navFor, noUnit } from "./js/nav-model.js";
import { startRouter } from "./js/router.js";
import { SCREENS } from "./js/screens/index.js";
import { session } from "./js/session.js";
import { buildTabs, renderUser } from "./js/shell.js";
import { initTheme } from "./js/theme.js";
import { installTooltips } from "./js/tooltip.js";
import { closeAllSheets, emptyCard, errorCard, errorText, noAccess, toast } from "./js/ui.js";

const el = {
  boot: $("#boot"), loginPage: $("#login-page"), noAccess: $("#noaccess-page"), app: $("#app"), view: $("#view"),
  who: $("#who"), unit: $("#unit"), ava: $("#ava"), logout: $("#logout"),
};
const titles = Object.fromEntries(Object.entries(SCREENS).map(([name, screen]) => [name, screen.title]));

let router = null;
let screenAbort = null;
const lastParams = {};   // «Досье» и «Воронка» помнят последний открытый объект, пока вкладку не покинули насовсем

let nav = navFor(null, () => false);   // вкладки по роли из /api/me: строятся при входе (buildNav)
let tabs = { setActive() {}, setBadge() {} };

/** Вкладки под роль: специалист, руководитель отдела персонала («Доступ»), руководитель подразделения и директор (кабинет). */
function buildNav() {
  nav = navFor(session.user, (permission) => session.can(permission));
  tabs = buildTabs($("#tabs"), { ...titles, ...nav.titles }, {
    order: nav.tabs,
    onSelect: (name) => router && router.go(name, lastParams[name] || {}),
  });
  const line = headerLine(session.user, session.scope);
  el.unit.textContent = line;
  el.unit.hidden = !line;
}

// ------------------------------------------------------------------ экраны

function renderRoute(route) {
  if (!session.user) return;   // страница входа открыта: чужие адреса в хэше ничего не грузят
  if (screenAbort) screenAbort.abort();
  closeAllSheets({ onlyDismissible: true });   // окно решения не должно пережить уход с экрана, к которому оно относилось
  const controller = new AbortController();
  screenAbort = controller;
  const screen = SCREENS[route.name];

  tabs.setActive(activeTab(route.name, nav));
  document.title = `${nav.titles[route.name] || screen.title} — Подбор`;
  el.view.setAttribute("aria-labelledby", `tab_${route.name}`);
  const hadFocus = el.view.contains(document.activeElement);
  mount(el.view);
  el.view.removeAttribute("aria-busy");
  window.scrollTo(0, 0);
  if (hadFocus) el.view.focus({ preventScroll: true });
  if ((route.name === "person" || route.name === "funnel") && route.params.id) lastParams[route.name] = { id: route.params.id };

  if (screen.perm && !session.can(screen.perm)) {
    mount(el.view, noAccess());
    return;
  }
  if (noUnit(session.user, session.scope) && route.name !== "arch") {   // S17: у руководителя нет подразделения — кабинет пуст («Схема» — только справка)
    mount(el.view, emptyCard(NO_UNIT_TEXT));
    return;
  }
  const ctx = {
    route, params: route.params, query: route.query, signal: controller.signal, api, session,
    go: (name, params, query, options) => router.go(name, params, query, options),
    setBadge: tabs.setBadge,
  };
  Promise.resolve()
    .then(() => screen.render(el.view, ctx))
    .catch((error) => {
      if (controller.signal.aborted || (error && error.name === "AbortError")) return;
      mount(el.view, errorCard(error, { onRetry: () => renderRoute(route) }));
    });
}

function enterApp() {
  if (navFor(session.user, (permission) => session.can(permission)).kind === "none") {   // роль без прав (S4)
    showNoAccess();
    return;
  }
  el.boot.hidden = true;
  el.loginPage.hidden = true;
  el.noAccess.hidden = true;
  el.app.hidden = false;
  buildNav();
  renderUser(session.user, { whoEl: el.who, avaEl: el.ava });
  loadStatus.attach({ api, session, box: $("#loadstatus") });   // полоса хода загрузки резюме — только с правом collect.run
  notify.attach({ api, session, slot: $("#bell"), alarmSlot: $("#alarm") });   // колокольчик и красная полоса тревоги — только отделу персонала
  if (router) router.refresh();
  else router = startRouter(renderRoute);
  el.view.focus({ preventScroll: true });
}

/** S4: роли нет или человек уволен — «Доступ не выдан»; данных на экране нет. */
function showNoAccess() {
  if (screenAbort) screenAbort.abort();
  loadStatus.detach();
  notify.detach();
  mount(el.view);
  closeAllSheets();
  el.boot.hidden = true;
  el.app.hidden = true;
  el.loginPage.hidden = true;
  document.title = "Доступ не выдан — Подбор";
  mount(el.noAccess, h("div.loginwrap", h("div.card.logincard", { role: "alert" },
    h("div.brand", h("div.mark", { "aria-hidden": "true" }, "Р"), h("h1.h1", "Подбор")),
    h("p.t1", { style: "margin:14px 0" }, NO_ACCESS_TEXT),
    h("button.b.fill", { type: "button", onClick: async (event) => {
      event.currentTarget.disabled = true;
      try { await api.logout(); } catch { /* сессии могло и не быть: просто возвращаемся ко входу */ }
      session.clear();
      leaveApp();
    } }, "Выйти"))));
  el.noAccess.hidden = false;
}

function showLogin() {
  el.boot.hidden = true;
  el.app.hidden = true;
  el.noAccess.hidden = true;
  document.title = "Вход — Подбор";
  const form = renderLoginPage(el.loginPage, { onSuccess: enterApp, onNoAccess: showNoAccess });
  el.loginPage.hidden = false;
  form.focus();
}

/** Выход: с экрана уходят все данные о людях, открытые окна закрываются, адрес сбрасывается. */
function leaveApp() {
  loadStatus.detach();
  notify.detach();
  if (screenAbort) screenAbort.abort();
  mount(el.view);
  closeAllSheets();
  for (const key of Object.keys(lastParams)) delete lastParams[key];
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  showLogin();
}

el.logout.addEventListener("click", async () => {
  el.logout.disabled = true;
  try {
    await api.logout();
    leaveApp();
  } catch (error) {
    toast(`Не удалось выйти: ${errorText(error)}`);
  } finally {
    el.logout.disabled = false;
  }
});

$("#skip").addEventListener("click", () => el.view.focus());

// другой человек вошёл в окне повторного входа: экран перерисовывается под него
session.subscribe((user, previous) => {
  if (!user) return;
  renderUser(user, { whoEl: el.who, avaEl: el.ava });
  if (previous && previous.id !== user.id) {
    buildNav();   // роль другого человека — другие вкладки
    loadStatus.attach({ api, session, box: $("#loadstatus") });   // права другого человека могут отличаться
    notify.attach({ api, session, slot: $("#bell"), alarmSlot: $("#alarm") });
    if (router) router.refresh();
  }
});

// забытое необработанное обещание (например, кнопка без try/catch) не должно молчать (S35)
window.addEventListener("unhandledrejection", (event) => {
  event.preventDefault();
  if (event.reason && event.reason.name === "AbortError") return;
  toast(errorText(event.reason));
});

// ------------------------------------------------------------------ старт

async function start() {
  try {
    await api.me();
    enterApp();
  } catch (error) {
    if (error.status === 401) {
      showLogin();
      return;
    }
    if (error.status === 403 && error.code === "no_access") {
      showNoAccess();
      return;
    }
    el.boot.hidden = false;
    el.boot.removeAttribute("aria-busy");
    mount(el.boot, errorCard(error, { title: "Рабочее место не открылось", onRetry: () => { mount(el.boot, "Загружаю…"); start(); } }));
  }
}

initTheme($("#theme"));
installTooltips();
api.setReauth(requestLogin);
api.setNoAccess(showNoAccess);   // 403 «no_access» посреди работы: доступ отозвали
start();
