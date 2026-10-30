// Вход (S1) и повторный вход поверх экрана, когда сессия закончилась (S2).
import { api } from "./api.js";
import { h, mount } from "./dom.js";
import { BAD_CODE_TEXT, normalizeCode, passwordProblem } from "./login-model.js";
import { errorLine, field, sheet } from "./ui.js";

/**
 * Форма входа: логин, пароль, «Войти». Ошибка сервера («Неверный логин или пароль», блокировка, нет связи) —
 * текстом под кнопкой; логин остаётся, при неверном пароле очищается только пароль.
 * onSuccess(data) вызывается после успешного POST /api/login.
 */
export function loginForm({ onSuccess, onNoAccess = null }) {
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
      if (error.status === 403 && error.code === "no_access" && onNoAccess) {   // роли нет или человек уволен: страница «Доступ не выдан»
        password.input.value = "";
        onNoAccess();
        return;
      }
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

/**
 * Первый вход по одноразовому коду (S3): логин, код из отдела персонала, новый пароль дважды. Проверка пароля — до отправки, у своего поля.
 * Код не подошёл по любой причине (неверный, истёк, использован, нет такого логина) — один текст; поле кода очищается, логин остаётся.
 */
export function codeForm({ onSuccess, onNoAccess = null }) {
  const login = field({ label: "Логин", name: "login", autocomplete: "username", required: true });
  const code = field({ label: "Код из отдела персонала", name: "code", autocomplete: "one-time-code", required: true });
  const password = field({ label: "Новый пароль", kind: "password", name: "new-password", autocomplete: "new-password", required: true, hint: "Не короче 8 знаков" });
  const repeat = field({ label: "Повторите пароль", kind: "password", name: "repeat-password", autocomplete: "new-password", required: true });
  code.input.setAttribute("spellcheck", "false");
  code.input.setAttribute("autocapitalize", "off");
  code.input.setAttribute("autocorrect", "off");
  const status = errorLine();
  const show = h("button.b", { type: "button", "aria-pressed": "false", "data-tip": "login-show", style: "justify-self:start" }, "Показать");
  show.addEventListener("click", () => {
    const on = show.getAttribute("aria-pressed") !== "true";
    show.setAttribute("aria-pressed", String(on));
    for (const f of [password, repeat]) f.input.type = on ? "text" : "password";
  });
  const submit = h("button.b.fill", { type: "submit" }, "Сохранить и войти");
  const form = h("form.form", { novalidate: true }, login.root, code.root, password.root, repeat.root, show, submit, status.el);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    status.clear();
    for (const f of [login, code, password, repeat]) f.error("");
    const name = login.input.value.trim();
    const typed = normalizeCode(code.input.value);
    if (!name) { login.error("Введите логин"); login.input.focus(); return; }
    if (!typed) { code.error("Введите код из отдела персонала"); code.input.focus(); return; }
    const problem = passwordProblem(password.input.value, repeat.input.value);
    if (problem) {
      const target = problem.field === "repeat" ? repeat : password;
      target.error(problem.text);
      target.input.focus();
      return;
    }
    submit.disabled = true;
    form.setAttribute("aria-busy", "true");
    try {
      const data = await api.loginWithCode(name, typed, password.input.value);
      password.input.value = "";
      repeat.input.value = "";
      code.input.value = "";
      await onSuccess(data);
    } catch (error) {
      if (error.status === 403 && error.code === "no_access" && onNoAccess) {
        onNoAccess();
        return;
      }
      if (error.status === 401) {
        status.show(BAD_CODE_TEXT);   // причину не называем: перебирать нечего
        code.input.value = "";
        code.input.focus();
      } else {
        status.show(error);   // 429 — блокировка, как у пароля; 422 — проверка пароля сервером; нет связи
      }
    } finally {
      submit.disabled = false;
      form.removeAttribute("aria-busy");
    }
  });
  return { el: form, focus: () => login.input.focus() };
}

/** Страница входа (до первого входа и после «Выйти»): знак, форма пароля; под ней «У меня есть код» — форма первого входа по коду. */
export function renderLoginPage(box, { onSuccess, onNoAccess = null }) {
  const passwordForm = loginForm({ onSuccess, onNoAccess });
  let byCode = null;
  const body = h("div");
  const toggle = h("button.link", { type: "button", style: "margin-top:12px", "data-tip": "login-code" });
  let current = null;
  const draw = (mode) => {
    current = mode;
    if (mode === "code" && !byCode) byCode = codeForm({ onSuccess, onNoAccess });
    const form = mode === "code" ? byCode : passwordForm;
    toggle.textContent = mode === "code" ? "Войти по паролю" : "У меня есть код";
    toggle.setAttribute("data-tip", mode === "code" ? "login-password" : "login-code");
    mount(body, form.el, toggle, mode === "code"
      ? h("p.hint", "Код выдаёт отдел персонала: он действует несколько дней и подходит один раз. По нему вы придумываете свой пароль.")
      : h("p.hint", "Пароль меняется командой ", h("code", "hr.cli")));
  };
  toggle.addEventListener("click", () => {
    draw(current === "code" ? "password" : "code");
    (current === "code" ? byCode : passwordForm).focus();
  });
  draw("password");
  mount(box,
    h("div.loginwrap",
      h("div.card.logincard",
        h("div.brand", h("div.mark", { "aria-hidden": "true" }, "Р"), h("h1.h1", "Подбор")),
        h("div.t2", "Рабочее место отдела персонала"),
        body)));
  return { focus: () => (current === "code" ? byCode : passwordForm).focus() };
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
