// Общие части интерфейса: уведомление, окно <dialog>, состояния «загрузка / пусто / ошибка», подписанные поля.
// Тексты из API сюда попадают только как текст (см. dom.js).
import { ApiError } from "./api.js";
import { h, mount, uid } from "./dom.js";

// ------------------------------------------------------------------ уведомления (П:926–929)

let toastTimer;
/** Короткое уведомление внизу экрана; объявляется скринридеру (aria-live="polite" на #toast). */
export function toast(text, { ms = 2600 } = {}) {
  const el = document.getElementById("toast");
  if (!el) return;
  clearTimeout(toastTimer);
  el.classList.remove("show");
  el.textContent = "";
  // пустая пауза нужна, чтобы повтор того же текста тоже объявился
  toastTimer = setTimeout(() => {
    el.textContent = text;
    el.classList.add("show");
    toastTimer = setTimeout(() => el.classList.remove("show"), ms);
  }, 30);
}

/** Сообщение только для скринридера (например «Скопировано»), без видимого уведомления. */
export function announce(text) {
  const el = document.getElementById("live");
  if (!el) return;
  el.textContent = "";
  setTimeout(() => { el.textContent = text; }, 30);
}

// ------------------------------------------------------------------ состояния экрана

/** Текст ошибки для человека: у ApiError он уже по-русски, остальное — наша ошибка, подробности в консоль. */
export function errorText(error) {
  if (error instanceof ApiError) return error.message;
  console.error(error);
  return "Что-то пошло не так. Обновите страницу и повторите";
}

/** Красная карточка на месте блока: «Не удалось загрузить» + причина + «Повторить». */
export function errorCard(error, { onRetry, title = "Не удалось загрузить" } = {}) {
  return h("div.card.errorcard", { role: "alert" },
    h("div.t1", title),
    h("div.t2", errorText(error)),
    onRetry ? h("div.btns", h("button.b", { type: "button", onClick: onRetry }, "Повторить")) : null);
}

/** Пустое состояние: текст и, при желании, кнопки/ссылки. */
export function emptyCard(text, ...actions) {
  return h("div.card.empty", h("div.t2", text), actions.length ? h("div.btns", actions) : null);
}

export const noAccess = () => emptyCard("Нет доступа к этому разделу");

export const loadingLine = () => h("p.loading", "Загружаю…");

/**
 * Загрузить данные в блок: aria-busy сразу, «Загружаю…» через 300 мс, ошибка — карточкой с «Повторить».
 *   mountAsync(box, () => api.get("/api/today", {signal: ctx.signal}), (data) => [...узлы...], {signal})
 * render возвращает узлы блока. Если блок успели перерисовать или ушли с экрана (signal), ответ отбрасывается.
 */
export async function mountAsync(box, load, render, { signal, delayMs = 300 } = {}) {
  const ticket = Symbol("load");
  box.ticket = ticket;
  const stale = () => box.ticket !== ticket || (signal && signal.aborted);
  box.setAttribute("aria-busy", "true");
  const timer = setTimeout(() => { if (!stale()) mount(box, loadingLine()); }, delayMs);
  try {
    const data = await load();
    if (stale()) return;
    clearTimeout(timer);
    mount(box, render(data));
    box.removeAttribute("aria-busy");
  } catch (error) {
    clearTimeout(timer);
    if (stale() || (error && error.name === "AbortError")) return;
    mount(box, errorCard(error, { onRetry: () => mountAsync(box, load, render, { signal, delayMs }) }));
    box.removeAttribute("aria-busy");
  }
}

// ------------------------------------------------------------------ файлы

/** Сохранить Blob как файл (выгрузка досье в Word): адрес живёт только до конца клика. */
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

// ------------------------------------------------------------------ ошибка под кнопкой (в окне и в форме)

/**
 * Строка ошибки под кнопкой: role="alert" объявляет текст сразу, окно не закрывается, введённое остаётся.
 *   const status = errorLine(); … status.show(error); status.clear();
 */
export function errorLine() {
  const el = h("p.formerror", { role: "alert" });
  return {
    el,
    show(error) { el.textContent = typeof error === "string" ? error : errorText(error); },
    clear() { el.textContent = ""; },
  };
}

// ------------------------------------------------------------------ поля с подписью

/**
 * Поле с видимой подписью: {root, input, error(text)}. kind: "text" | "password" | "textarea".
 * Ошибка проверки (422) показывается у поля и связана с ним через aria-describedby.
 */
export function field({ label, kind = "text", name, value = "", autocomplete, required = false, placeholder, hint, describedBy, tip } = {}) {
  const id = uid(name || "field");
  const errId = `${id}-err`;
  // describedBy — id других подсказок поля (например счётчика знаков): читаются вместе с текстом ошибки
  const common = { id, name, autocomplete, required, placeholder, "aria-describedby": [describedBy, errId].filter(Boolean).join(" "),
    "aria-invalid": false, "data-tip": tip };
  const input = kind === "textarea"
    ? h("textarea", { ...common }, value)
    : h("input", { ...common, type: kind, value });
  const err = h("p.fielderr", { id: errId });
  const root = h("div.field", h("label", { for: id }, label), input, hint ? h("div.hint", hint) : null, err);
  return {
    root,
    input,
    error(text) {
      err.textContent = text || "";
      input.setAttribute("aria-invalid", text ? "true" : "false");
    },
  };
}

// ------------------------------------------------------------------ окно <dialog> (П:549–586)

const openSheets = new Set();

function onBackdrop(dialog, event) {
  const box = dialog.getBoundingClientRect();
  return event.target === dialog &&
    (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom);
}

/**
 * Окно-лист поверх экрана. Фокус внутри окна, Esc и клик по фону закрывают (если dismissible),
 * после закрытия фокус возвращается на кнопку, с которой окно открыли.
 *   const win = sheet({title: "Решение", hint: "…"});
 *   win.body.append(…);  win.show(firstFieldOrNull);  …  win.close("saved");  await win.closed → returnValue
 */
export function sheet({ title, hint, width, dismissible = true, className = "" } = {}) {
  const opener = document.activeElement;
  const titleId = uid("sheet-title");
  const body = h("div.sheetbody");
  const dialog = h("dialog.sheet", { class: className, "aria-labelledby": titleId, style: width ? { maxWidth: width } : null },
    h("div.grab", { "aria-hidden": "true" }),
    h("h3", { id: titleId }, title),
    hint ? h("div.hint", hint) : null,
    body);

  let pressedOnBackdrop = false;
  dialog.addEventListener("pointerdown", (event) => { pressedOnBackdrop = onBackdrop(dialog, event); });
  dialog.addEventListener("click", (event) => {
    if (dismissible && pressedOnBackdrop && onBackdrop(dialog, event)) dialog.close();
    pressedOnBackdrop = false;
  });
  if (!dismissible) dialog.addEventListener("cancel", (event) => event.preventDefault());

  let closing = false;   // закрыли мы сами (close()), а не браузер по Esc
  const closed = new Promise((resolve) => {
    dialog.addEventListener("close", () => {
      // Chrome закрывает окно по повторному Esc, даже если cancel отменён; окно, которое закрывать нельзя, открываем снова
      if (!dismissible && !closing) {
        dialog.showModal();
        return;
      }
      const result = dialog.returnValue;
      openSheets.delete(handle);
      dialog.remove();
      if (opener && opener.isConnected && typeof opener.focus === "function") opener.focus();
      resolve(result);
    });
  });
  const handle = {
    el: dialog,
    body,
    closed,
    dismissible,
    /** Открыть как модальное окно; focusEl — что подсветить сразу (по умолчанию первый элемент ввода). */
    show(focusEl) {
      document.body.appendChild(dialog);
      openSheets.add(handle);
      dialog.showModal();
      const target = focusEl || dialog.querySelector("input, textarea, select, button");
      if (target) target.focus();
    },
    close(value = "") {
      closing = true;
      dialog.close(value);
    },
  };
  return handle;
}

/**
 * Окно «да / нет». Фокус — на «Отмена»: случайный Enter ничего не подтверждает. Возвращает Promise<boolean>.
 */
export function confirmSheet({ title, hint, confirmText, confirmTip, cancelText = "Отмена", width = "440px" }) {
  const win = sheet({ title, hint, width });
  const cancel = h("button.b", { type: "button", "data-tip": cancelText, onClick: () => win.close("cancel") }, cancelText);
  const ok = h("button.b.fill", { type: "button", "data-tip": confirmTip || confirmText, onClick: () => win.close("ok") }, confirmText);
  win.body.append(h("div.sheetfoot.end", cancel, ok));
  win.show(cancel);
  return win.closed.then((outcome) => outcome === "ok");
}

/**
 * Закрыть открытые окна: при выходе — все (на экране не должно остаться ничего о людях); при смене экрана
 * («Назад» в браузере) — только те, что можно закрыть Esc, окно повторного входа остаётся.
 */
export function closeAllSheets({ onlyDismissible = false } = {}) {
  [...openSheets].filter((win) => !onlyDismissible || win.dismissible).forEach((win) => win.close("closed"));
}
