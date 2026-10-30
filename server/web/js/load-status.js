// Полоса хода загрузки резюме hh под вкладками на всех экранах (S48). Только у роли с правом collect.run (загрузка тратит лимит hh).
// Опрашивает GET /api/hh/collect/status: 3 с пока идёт загрузка, раз в минуту иначе (виден и прогон по расписанию в 18:00).
// После «Загрузить» окно закрывается, человек остаётся на своём экране, а полоса сразу пишет «Запускаю загрузку…».
// Всё из API выводится только текстом (h(), textContent).
import { h, mount } from "./dom.js";
import { barView, pollDelay, readDismissed, writeDismissed } from "./load-status-model.js";
import { hashFor } from "./router.js";
import { announce } from "./ui.js";

const PATH = "/api/hh/collect/status";

function browserStorage() {
  try {
    return globalThis.localStorage || null;
  } catch {
    return null;
  }
}

export function createLoadStatus({ now = () => Date.now(), storage = browserStorage() } = {}) {
  let api = null;
  let box = null;
  let timer = null;
  let last = null;          // последний ответ сервера
  let starting = null;      // {since, baselineId}: только что нажали «Загрузить»
  let dismissed = null;     // id итога, который закрыли «×»
  let kind = null;          // что показано сейчас: для объявления смены состояния
  let active = false;
  let ticket = 0;

  const draw = () => {
    const view = barView(last, { now: now(), dismissedId: dismissed, starting });
    if (starting && (!view || view.kind !== "starting")) starting = null;   // прогон появился (или ждать надоело): «Запускаю…» больше не нужно
    if (!view) {
      kind = null;
      mount(box);
      return view;
    }
    // во время хода текст меняется каждые 3 с: скринридеру об этом не сообщаем, только о начале и конце
    box.setAttribute("aria-live", view.kind === "running" ? "off" : "polite");
    if (view.kind !== kind) {
      kind = view.kind;
      if (view.kind === "running") announce(view.text);
    }
    const progress = view.kind === "running" || view.kind === "starting"
      ? h("div.lsprog", { role: "progressbar", "aria-label": "Ход загрузки", "aria-valuemin": 0, "aria-valuemax": 100,
        "aria-valuenow": view.percent === null ? null : view.percent, class: view.percent === null ? "indeterminate" : null },
      h("i", { style: view.percent === null ? null : { width: `${view.percent}%` } })) : null;
    mount(box, h("div.lsbar", { class: view.kind, "data-tip": "load-status" },
      h("div.lsrow", h("span.lstext", view.text),
        view.link ? h("a.link", { href: hashFor(view.link.route) }, view.link.text) : null,
        view.dismissible ? h("button.lsclose", { type: "button", "aria-label": "Скрыть сообщение о загрузке", "data-tip": "load-status-close", onClick: () => {
          dismissed = String(view.runId);
          writeDismissed(storage, view.runId);
          draw();
        } }, "×") : null),
      progress, view.note ? h("div.lsnote", view.note) : null));
    return view;
  };

  async function tick() {
    clearTimeout(timer);
    if (!active) return;
    if (typeof document !== "undefined" && document.hidden && !starting) {   // вкладка не на виду: спросим, когда вернутся
      timer = setTimeout(tick, pollDelay(null));
      return;
    }
    const mine = ++ticket;
    try {
      const reply = await api.get(PATH, { noAuth: true, noCsrfRetry: true });
      if (!active || mine !== ticket) return;
      last = reply;
    } catch (error) {
      if (!active) return;
      if (error && error.status === 403) { detach(); return; }   // право сняли: полосы больше нет
      // сеть моргнула или сессия кончилась (окно входа покажут сами экраны): полоса остаётся прежней, повторим в свой срок
    }
    const view = draw();
    timer = setTimeout(tick, pollDelay(view));
  }

  function attach({ api: client, session, box: element }) {
    api = client;
    box = element;
    if (!session.can("collect.run")) {
      detach();
      return;
    }
    active = true;
    dismissed = readDismissed(storage);
    tick();
  }

  function detach() {
    active = false;
    clearTimeout(timer);
    ticket += 1;
    last = null;
    starting = null;
    kind = null;
    if (box) mount(box);
  }

  /** Человек нажал «Загрузить» и сервер принял запуск: сразу «Запускаю загрузку…» и частый опрос, пока не появится прогон. */
  function started() {
    if (!active) return;
    starting = { since: now(), baselineId: last && last.run ? last.run.id : null };
    draw();
    tick();
  }

  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", () => { if (active && !document.hidden) tick(); });
  }
  return { attach, detach, started };
}

export const loadStatus = createLoadStatus();
