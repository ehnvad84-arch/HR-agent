// Оболочка рабочего места: вход, вкладки с адресом в хэше, переход между экранами.
// Сами экраны — js/screens/*.js (реестр в screens/index.js).
import { api } from "./js/api.js";
import { $, mount } from "./js/dom.js";
import { loadStatus } from "./js/load-status.js";
import { renderLoginPage, requestLogin } from "./js/login.js";
import { startRouter } from "./js/router.js";
import { SCREENS } from "./js/screens/index.js";
import { session } from "./js/session.js";
import { buildTabs, renderUser } from "./js/shell.js";
import { initTheme } from "./js/theme.js";
import { installTooltips } from "./js/tooltip.js";
import { closeAllSheets, errorCard, errorText, noAccess, toast } from "./js/ui.js";

const el = {
  boot: $("#boot"), loginPage: $("#login-page"), app: $("#app"), view: $("#view"),
  who: $("#who"), ava: $("#ava"), logout: $("#logout"),
};
const titles = Object.fromEntries(Object.entries(SCREENS).map(([name, screen]) => [name, screen.title]));

let router = null;
let screenAbort = null;
const lastParams = {};   // «Досье» и «Воронка» помнят последний открытый объект, пока вкладку не покинули насовсем

const tabs = buildTabs($("#tabs"), titles, {
  onSelect: (name) => router && router.go(name, lastParams[name] || {}),
});

// ------------------------------------------------------------------ экраны

function renderRoute(route) {
  if (!session.user) return;   // страница входа открыта: чужие адреса в хэше ничего не грузят
  if (screenAbort) screenAbort.abort();
  closeAllSheets({ onlyDismissible: true });   // окно решения не должно пережить уход с экрана, к которому оно относилось
  const controller = new AbortController();
  screenAbort = controller;
  const screen = SCREENS[route.name];

  tabs.setActive(route.name);
  document.title = `${screen.title} — Подбор`;
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
  el.boot.hidden = true;
  el.loginPage.hidden = true;
  el.app.hidden = false;
  renderUser(session.user, { whoEl: el.who, avaEl: el.ava });
  loadStatus.attach({ api, session, box: $("#loadstatus") });   // полоса хода загрузки резюме — только с правом settings.edit
  if (router) router.refresh();
  else router = startRouter(renderRoute);
  el.view.focus({ preventScroll: true });
}

function showLogin() {
  el.boot.hidden = true;
  el.app.hidden = true;
  document.title = "Вход — Подбор";
  const form = renderLoginPage(el.loginPage, { onSuccess: enterApp });
  el.loginPage.hidden = false;
  form.focus();
}

/** Выход: с экрана уходят все данные о людях, открытые окна закрываются, адрес сбрасывается. */
function leaveApp() {
  loadStatus.detach();
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
    loadStatus.attach({ api, session, box: $("#loadstatus") });   // права другого человека могут отличаться
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
    el.boot.hidden = false;
    el.boot.removeAttribute("aria-busy");
    mount(el.boot, errorCard(error, { title: "Рабочее место не открылось", onRetry: () => { mount(el.boot, "Загружаю…"); start(); } }));
  }
}

initTheme($("#theme"));
installTooltips();
api.setReauth(requestLogin);
start();
