// Вход (S1) и повторный вход поверх экрана, когда сессия закончилась (S2).
import { api } from "./api.js";
import { h, mount } from "./dom.js";
import { errorLine, field, sheet } from "./ui.js";

/**
 * Форма входа: логин, пароль, «Войти». Ошибка сервера («Неверный логин или пароль», блокировка, нет связи) —
 * текстом под кнопкой; логин остаётся, при неверном пароле очищается только пароль.
 * onSuccess(data) вызывается после успешного POST /api/login.
 */
export function loginForm({ onSuccess }) {
  const login = field({ label: "Логин", name: "login", autocomplete: "username", required: true });
  const password = field({ label: "Пароль", kind: "password", name: "password", autocomplete: "current-password", required: true });
  const status = errorLine();
  const submit = h("button.b.fill", { type: "submit" }, "Войти");
  const form = h("form.form", { novalidate: true }, login.root, password.root, submit, status.el);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    status.clear();
    const name = login.input.value.trim();
    if (!name || !password.input.value) {
      status.show("Введите логин и пароль");
      (name ? password.input : login.input).focus();
      return;
    }
    submit.disabled = true;
    form.setAttribute("aria-busy", "true");
    try {
      const data = await api.login(name, password.input.value);
      password.input.value = "";
      await onSuccess(data);
    } catch (error) {
      status.show(error);
      if (error.status === 401) {
        password.input.value = "";
        password.input.focus();
      }
    } finally {
      submit.disabled = false;
      form.removeAttribute("aria-busy");
    }
  });
  return { el: form, focus: () => login.input.focus() };
}

/** Страница входа (до первого входа и после «Выйти»): знак, логин, пароль. */
export function renderLoginPage(box, { onSuccess }) {
  const form = loginForm({ onSuccess });
  mount(box,
    h("div.loginwrap",
      h("div.card.logincard",
        h("div.brand", h("div.mark", { "aria-hidden": "true" }, "Р"), h("h1.h1", "Подбор")),
        h("div.t2", "Рабочее место отдела персонала"),
        form.el,
        h("p.hint", "Пароль меняется командой ", h("code", "hr.cli")))));
  return form;
}

let pending = null;
/**
 * Окно входа поверх текущего экрана для api (401). Возвращает промис, который завершается после входа.
 * Закрыть окно без входа нельзя (Esc не работает): иначе запрос, который ждёт, завис бы.
 */
export function requestLogin() {
  if (pending) return pending;
  pending = new Promise((resolve, reject) => {
    const win = sheet({ title: "Сессия закончилась", hint: "Войдите снова — всё, что вы ввели, останется на месте.",
      width: "420px", dismissible: false, className: "relogin" });
    const form = loginForm({ onSuccess: () => { win.close("ok"); resolve(); } });
    win.body.append(form.el);
    win.show(form.el.querySelector("input"));
    win.closed.then((result) => {
      pending = null;
      // окно закрыли без входа (выход из системы): ждущие запросы снимаются тихо, как отмена
      if (result !== "ok") reject(new DOMException("Вход отменён", "AbortError"));
    });
  });
  return pending;
}
