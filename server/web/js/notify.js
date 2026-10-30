// Сообщения о загрузках (S20): колокольчик в шапке и красная полоса тревоги под вкладками. Только у отдела персонала (handoff.review).
// Опрашивает GET /api/notifications раз в минуту, без окна входа при 401 (как полоса хода загрузки). «Разбираюсь» снимает полосу у всех
// и записывает имя; в списке у такого сообщения — «Разбирается: …». Всё из API выводится только текстом (h(), textContent).
import { h, mount } from "./dom.js";
import { whenLabel } from "./format.js";
import { chip } from "./people.js";
import { ACK_LABEL, EMPTY_TEXT, POLL_MS, alarmView, bellView, itemView, unreadIds } from "./notify-model.js";
import { errorText, toast } from "./ui.js";

const PATH = "/api/notifications";
const TAKEN_SHOWN_MS = 15000;   // после «Разбираюсь» нажавший ещё недолго видит спокойную полосу с именем

const bellIcon = () => h("svg", { viewBox: "0 0 24 24", width: 20, height: 20, fill: "none", stroke: "currentColor", "stroke-width": 1.8,
  "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false" },
h("svg:path", { d: "M6 9a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6" }), h("svg:path", { d: "M10 19a2 2 0 0 0 4 0" }));

export function createNotify() {
  let api = null;
  let slot = null;
  let alarmBox = null;
  let timer = null;
  let last = null;        // последний ответ сервера
  let localAck = null;    // {by, title}: я только что нажал «Разбираюсь»
  let ackTimer = null;
  let shown = null;       // ключ показанной полосы: «alarm:6» — чтобы скринридер не слышал её заново при каждом опросе
  let open = false;
  let active = false;
  let ticket = 0;
  let bell = null;
  let badge = null;
  let panel = null;
  let onKey = null;
  let onDocClick = null;

  // ---------------------------------------------------------------- красная полоса
  function drawAlarm() {
    const view = alarmView(last, localAck);
    const key = view ? `${view.kind}:${view.id}` : null;
    if (key === shown) return;
    shown = key;
    if (!view) { mount(alarmBox); return; }
    mount(alarmBox, h("div.alarmbar", { class: view.kind, role: view.kind === "alarm" ? "alert" : "status", "data-tip": "alarm-bar" },
      h("div.alrow", h("b", view.title), h("span.alsub", view.text),
        view.canAck ? h("button.b.alack", { type: "button", "data-tip": ACK_LABEL, onClick: () => acknowledge(view.id, view.title) }, ACK_LABEL) : null,
        h("button.link", { type: "button", "data-tip": "Все сообщения", onClick: () => togglePanel(true) }, "Все сообщения"))));
  }

  async function acknowledge(id, title) {
    try {
      const reply = await api.post(`/api/notification/${encodeURIComponent(id)}/ack`, {});
      localAck = { by: reply.by || "вы", title };
      clearTimeout(ackTimer);
      ackTimer = setTimeout(() => { localAck = null; shown = null; drawAlarm(); }, TAKEN_SHOWN_MS);
      toast("Записано: тревогу разбираете вы");
      await refresh();
    } catch (error) {
      if (error && error.name === "AbortError") return;
      toast(errorText(error));
    }
  }

  // ---------------------------------------------------------------- колокольчик и список
  function drawBell() {
    const view = bellView(last);
    bell.setAttribute("aria-label", view.label);
    bell.classList.toggle("alarm", view.alarm);
    badge.textContent = view.badge;
    badge.hidden = !view.badge;
  }

  function itemNode(item) {
    const view = itemView(item);
    return h("li.bellitem", { class: view.unread ? "unread" : null },
      h("div.bhead", h("b", view.title), view.unread ? chip("новое", "b") : null),
      h("div.t2", `${view.status.charAt(0).toUpperCase()}${view.status.slice(1)} · ${whenLabel(view.at)}`),
      view.text ? h("div.t2", { style: "color:var(--ink2)" }, view.text) : null,
      h("div.btns", { style: "margin-top:6px" },
        chip(view.status, view.tone),
        view.ackText ? chip(view.ackText, "o") : null,
        view.canAck ? h("button.b", { type: "button", "data-tip": ACK_LABEL, onClick: () => acknowledge(view.id, view.title) }, ACK_LABEL) : null));
  }

  function drawPanel() {
    if (!panel) return;
    const items = (last && last.items) || [];
    mount(panel,
      h("div.bellhead", h("h2.t1", { style: "font-size:16px;margin:0" }, "Загрузки резюме"),
        h("button.link", { type: "button", "data-tip": "bell-close", onClick: () => togglePanel(false) }, "Закрыть")),
      items.length ? h("ul.bellist", items.map(itemNode)) : h("p.t2", { style: "margin:8px 0 0" }, EMPTY_TEXT));
  }

  function togglePanel(on) {
    if (on === open) return;
    open = on;
    bell.setAttribute("aria-expanded", String(open));
    if (!open) {
      panel.remove();
      panel = null;
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("click", onDocClick, true);
      bell.focus();
      return;
    }
    panel = h("div.bellpanel", { role: "dialog", "aria-label": "Сообщения о загрузках", tabindex: -1 });
    slot.append(panel);
    drawPanel();
    panel.focus();
    onKey = (event) => { if (event.key === "Escape") togglePanel(false); };
    onDocClick = (event) => { if (!slot.contains(event.target) && !alarmBox.contains(event.target)) togglePanel(false); };
    document.addEventListener("keydown", onKey);
    document.addEventListener("click", onDocClick, true);
    markRead();
  }

  /** Список открыт — человек увидел сообщения: отмечаем их прочитанными, число на значке падает сразу. */
  async function markRead() {
    const ids = unreadIds(last);
    if (!ids.length) return;
    await Promise.all(ids.map((id) => api.post(`/api/notification/${encodeURIComponent(id)}/read`, {}).catch(() => null)));
    if (!active) return;
    await refresh();
  }

  // ---------------------------------------------------------------- опрос
  async function refresh() {
    const mine = ++ticket;
    try {
      const reply = await api.get(PATH, { noAuth: true, noCsrfRetry: true });
      if (!active || mine !== ticket) return;
      last = reply;
    } catch (error) {
      if (!active) return;
      if (error && error.status === 403) { detach(); return; }   // право сняли: колокольчика больше нет
      // сеть моргнула или сессия кончилась (окно входа покажут экраны): остаётся прежнее, повторим в свой срок
    }
    drawBell();
    drawAlarm();
    drawPanel();
  }

  async function tick() {
    clearTimeout(timer);
    if (!active) return;
    if (!(typeof document !== "undefined" && document.hidden)) await refresh();
    if (active) timer = setTimeout(tick, POLL_MS);
  }

  function attach({ api: client, session, slot: bellSlot, alarmSlot }) {
    detach();
    api = client;
    slot = bellSlot;
    alarmBox = alarmSlot;
    if (!session.can("handoff.review")) return;
    active = true;
    badge = h("span.bellbadge", { hidden: true, "aria-hidden": "true" });
    bell = h("button.bell", { type: "button", "aria-haspopup": "dialog", "aria-expanded": "false", "data-tip": "bell", onClick: () => togglePanel(!open) }, bellIcon(), badge);
    mount(slot, bell);
    drawBell();
    tick();
  }

  function detach() {
    active = false;
    clearTimeout(timer);
    clearTimeout(ackTimer);
    ticket += 1;
    last = null;
    localAck = null;
    shown = null;
    if (open) {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("click", onDocClick, true);
    }
    open = false;
    panel = null;
    if (slot) mount(slot);
    if (alarmBox) mount(alarmBox);
  }

  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", () => { if (active && !document.hidden) tick(); });
  }
  return { attach, detach };
}

export const notify = createNotify();
