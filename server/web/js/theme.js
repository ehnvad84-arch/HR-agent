// Переключатель День / Ночь / Авто (П:1837–1849). Выбор хранится в браузере; до загрузки модулей тему
// выставляет theme-init.js, чтобы страница не мигала.
import { h, mount } from "./dom.js";

const THEMES = [["light", "День"], ["dark", "Ночь"], ["auto", "Авто"]];

function current() {
  try {
    const saved = localStorage.getItem("hrTheme");
    return saved === "light" || saved === "dark" ? saved : "auto";
  } catch {
    return "auto";
  }
}

function apply(mode) {
  if (mode === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", mode);
  try { localStorage.setItem("hrTheme", mode); } catch { /* приватный режим: тема живёт до закрытия вкладки */ }
}

export function initTheme(box) {
  const buttons = THEMES.map(([mode, title]) =>
    h("button", { type: "button", dataset: { mode }, "aria-pressed": false, onClick: () => select(mode) }, title));
  function select(mode) {
    apply(mode);
    buttons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
  }
  mount(box, buttons);
  select(current());
}
