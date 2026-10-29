// Подсказки через секунду (S42): наведение мыши или фокус на кнопке, ссылке, вкладке, поле или элементе с data-tip.
// Текст берётся из словаря tips.js (tipFor): нет текста — нет подсказки. Прячется по уходу мыши, потере фокуса, Esc,
// клику и прокрутке. Одна установка на document; текст — только textContent.
import { tipFor } from "./tips.js";

const TARGETS = "button, a, [role=tab], [data-tip], input, select, textarea";
const DELAY_MS = 1000;
const GAP = 8;
const MARGIN = 8;

/**
 * Куда поставить подсказку: под элементом, а если внизу нет места — над ним; по горизонтали — вровень с левым краем
 * элемента, но в пределах окна. rect и size — {left, top, width, height}, view — {width, height}.
 */
export function placeTip(rect, size, view) {
  let top = rect.top + rect.height + GAP;
  let placement = "below";
  if (top + size.height > view.height - MARGIN) {
    const above = rect.top - GAP - size.height;
    if (above >= MARGIN) {
      top = above;
      placement = "above";
    } else if (rect.top > view.height - (rect.top + rect.height)) {   // не помещается ни там, ни там: где места больше
      top = Math.max(MARGIN, above);
      placement = "above";
    } else {
      top = Math.min(top, view.height - MARGIN - size.height);
    }
  }
  const left = Math.max(MARGIN, Math.min(rect.left, view.width - size.width - MARGIN));
  return { left, top, placement };
}

/** Ставит обработчики на document один раз. delay — для проверок; по умолчанию секунда. */
export function installTooltips(doc = document, { delay = DELAY_MS } = {}) {
  if (doc.tooltipsInstalled) return;
  doc.tooltipsInstalled = true;

  let target = null;       // элемент, над которым ждём или показываем подсказку
  let armedBy = null;      // "pointer" или "focus": мышь прячет только подсказки, которые вызвала сама
  let timer = null;
  let tip = null;          // показанная подсказка
  let watcher = null;
  let saved = null;        // {title, describedby} исходные значения атрибутов
  let counter = 0;

  function restore() {
    if (!target || !saved) return;
    if (saved.title !== null && !target.hasAttribute("title")) target.setAttribute("title", saved.title);
    if (saved.describedby === null) target.removeAttribute("aria-describedby");
    else target.setAttribute("aria-describedby", saved.describedby);
    saved = null;
  }

  function hide() {
    clearTimeout(timer);
    clearInterval(watcher);
    timer = null;
    watcher = null;
    if (tip) {
      if (typeof tip.hidePopover === "function" && tip.matches(":popover-open")) tip.hidePopover();
      tip.remove();
      tip = null;
    }
    restore();
    target = null;
    armedBy = null;
  }

  function show(element) {
    if (!element.isConnected) return;
    const text = tipFor(element);
    if (!text) return;
    tip = doc.createElement("div");
    tip.className = "tooltip";
    tip.id = `tip-${(counter += 1)}`;
    tip.setAttribute("role", "tooltip");
    tip.textContent = text;
    tip.style.visibility = "hidden";
    if ("popover" in tip) {   // верхний слой: подсказка видна и поверх модального окна <dialog>
      tip.setAttribute("popover", "manual");
      doc.body.append(tip);
      tip.showPopover();
    } else {
      (element.closest("dialog") || doc.body).append(tip);
    }
    const box = element.getBoundingClientRect();
    const own = tip.getBoundingClientRect();
    const pos = placeTip({ left: box.left, top: box.top, width: box.width, height: box.height },
      { width: own.width, height: own.height }, { width: doc.documentElement.clientWidth, height: doc.documentElement.clientHeight });
    tip.style.left = `${pos.left}px`;
    tip.style.top = `${pos.top}px`;
    tip.style.visibility = "visible";
    tip.dataset.placement = pos.placement;
    saved = saved || { title: element.getAttribute("title"), describedby: element.getAttribute("aria-describedby") };
    element.setAttribute("aria-describedby", [saved.describedby, tip.id].filter(Boolean).join(" "));
    watcher = setInterval(() => { if (!element.isConnected) hide(); }, 400);   // экран перерисовался: подсказка не остаётся висеть
  }

  /** Подсказка «запланирована» для элемента: системный title убирается сразу, чтобы не было двух подсказок. */
  function arm(element, by) {
    if (element === target) return;
    hide();
    if (!tipFor(element)) return;
    target = element;
    armedBy = by;
    const title = element.getAttribute("title");
    saved = { title, describedby: element.getAttribute("aria-describedby") };
    if (title !== null) element.removeAttribute("title");
    timer = setTimeout(() => show(element), delay);
  }

  doc.addEventListener("pointerover", (event) => {
    if (event.pointerType === "touch") return;
    const element = event.target.closest ? event.target.closest(TARGETS) : null;
    if (element) arm(element, "pointer");
    else if (armedBy === "pointer") hide();   // подсказку от фокуса не трогаем: окно, открываясь, само даёт pointerover
  });
  doc.addEventListener("pointerout", (event) => {
    if (!event.relatedTarget && armedBy === "pointer") hide();   // мышь ушла за пределы окна
  });
  doc.addEventListener("focusin", (event) => {
    const element = event.target.closest ? event.target.closest(TARGETS) : null;
    if (element) arm(element, "focus");
  });
  doc.addEventListener("focusout", hide);
  doc.addEventListener("pointerdown", hide);
  doc.addEventListener("click", (event) => {   // клик по самому элементу прячет подсказку; клик, после которого фокус ушёл в новое окно, её не отменяет
    if (target && target.contains(event.target)) hide();
  });
  // прокрутка прячет показанную подсказку; ожидающую не трогает: окно, открываясь, прокручивает поле в видимость
  doc.addEventListener("scroll", () => { if (tip) hide(); }, true);
  doc.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (tip) {
      event.preventDefault();    // первый Esc прячет подсказку, окно под ней остаётся; второй закроет окно
      event.stopPropagation();
    }
    if (tip || timer) hide();
  }, true);
}
