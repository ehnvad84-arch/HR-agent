// Карточка с действиями — общая часть экранов «Сегодня» и «Вакансии». Всё из API выводится только текстом.
import { h } from "./dom.js";
import { errorLine } from "./ui.js";

/**
 * Карточка с действиями: тело (.cardbody) и под ним итог «✓ что сделано».
 * layout({btns, status}) собирает тело и решает, где стоят кнопки и строка ошибки.
 * Кнопки с data-act блокируются на время запроса и после успеха; «Открыть досье» — переход, не действие.
 */
export function actionCard(layout, buttons) {
  const status = errorLine();
  const list = buttons.filter(Boolean);
  const parts = { btns: list.length ? h("div.btns", list) : null, status: status.el };
  const result = h("p.result", { role: "status", tabindex: -1 });
  const el = h("div.card", h("div.cardbody", layout(parts)), result);
  const acting = () => [...el.querySelectorAll("button[data-act]")];
  return {
    el,
    status,
    busy(on) { acting().forEach((b) => { b.disabled = on; }); },
    /** Итог без изменения карточки: пометка под кнопками. */
    note(text) { result.textContent = `✓ ${text}`; },
    /** Итог действия: карточка гаснет, кнопки действий остаются выключенными, фокус — на итоге. */
    finish(text) {
      acting().forEach((b) => { b.disabled = true; });
      el.classList.add("done");
      result.textContent = `✓ ${text}`;
      result.focus();
    },
  };
}

export const actBtn = (cls, text, onClick) => h("button.b", { class: cls, type: "button", dataset: { act: 1 }, onClick }, text);

/** Выполнить запрос от кнопки карточки: ошибка — под кнопками, карточка остаётся как была. */
export async function perform(card, request, onDone) {
  card.status.clear();
  card.busy(true);
  try {
    await onDone(await request());
  } catch (error) {
    if (error && error.name === "AbortError") return;
    card.status.show(error);
    card.busy(false);
  }
}
