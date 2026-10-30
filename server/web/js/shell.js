// Шапка и вкладки (П:287–300): имя пользователя, «Выйти», вкладки с переходом стрелками.
import { h, initials, mount } from "./dom.js";
import { TAB_ORDER } from "./router.js";

/** Вкладки как role="tablist": одна вкладка в порядке Tab, остальные — стрелками, Home/End — крайние. */
export function buildTabs(box, titles, { onSelect, order = TAB_ORDER }) {
  const buttons = new Map();
  for (const name of order) {
    const badge = h("span.badge", { id: `bdg_${name}` });
    const button = h("button", {
      type: "button", role: "tab", id: `tab_${name}`, tabindex: -1, "aria-selected": false, "aria-controls": "view",
      dataset: { tab: name }, onClick: () => onSelect(name),
    }, titles[name], badge);
    buttons.set(name, { button, badge });
  }
  mount(box, [...buttons.values()].map((b) => b.button));

  box.addEventListener("keydown", (event) => {
    const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
    const index = order.indexOf(document.activeElement && document.activeElement.dataset.tab);
    if (index < 0 || (!step && event.key !== "Home" && event.key !== "End")) return;
    event.preventDefault();
    let next = index;
    if (event.key === "Home") next = 0;
    else if (event.key === "End") next = order.length - 1;
    else next = (index + step + order.length) % order.length;
    const name = order[next];
    buttons.get(name).button.focus();
    onSelect(name);
  });

  return {
    setActive(name) {
      for (const [tab, { button }] of buttons) {
        const on = tab === name;
        button.setAttribute("aria-selected", String(on));
        button.setAttribute("tabindex", on ? "0" : "-1");
        button.classList.toggle("on", on);
      }
    },
    /** Число или короткий текст рядом с названием вкладки; пусто — убрать. */
    setBadge(name, text) {
      const entry = buttons.get(name);
      if (entry) entry.badge.textContent = text === null || text === undefined ? "" : String(text);
    },
  };
}

/** ФИО (если нет — логин) и кружок с инициалами; логин — в подсказке. */
export function renderUser(user, { whoEl, avaEl }) {
  const name = (user && (user.full_name || user.login)) || "";
  whoEl.textContent = name;
  whoEl.title = user ? `Логин: ${user.login}` : "";
  avaEl.textContent = initials(name);
}
