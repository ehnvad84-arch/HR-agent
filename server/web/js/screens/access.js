// «Доступ» (S1–S3; руководитель отдела персонала, право access.manage): кто может войти и с какой ролью. Выгрузка сотрудников из 1С,
// ручное заведение, смена роли и подразделения, одноразовый код первого входа. Всё из API выводится только текстом (h(), textContent).
import { COPY_FALLBACK, copyText, selectText } from "../clipboard.js";
import { h, mount, uid } from "../dom.js";
import { openImportSheet } from "../import-sheet.js";
import { chip } from "../people.js";
import { confirmSheet, emptyCard, errorLine, field, loadingLine, mountAsync, sheet, toast } from "../ui.js";
import { orgFileError } from "./org-model.js";
import {
  ROLE_ORDER, canIssueCode, changesFor, codeAriaLabel, codeExpiryText, filterUsers, groupCode, loginStatus, newUserBody, newUserProblem, resultText, roleText,
  sortUsers, syncLine, unitOptions,
} from "./access-model.js";

const roleSelect = (id, value, { includeNoAccess = true } = {}) => {
  const select = h("select", { id }, ROLE_ORDER.filter((role) => includeNoAccess || role !== "no_access").map((role) => h("option", { value: role }, roleText(role))));
  select.value = value;
  return select;
};
const unitSelect = (id, units, value) => {
  const select = h("select", { id }, [h("option", { value: "" }, "— не указано —"), ...unitOptions(units).map((o) => h("option", { value: String(o.id) }, o.label))]);
  select.value = value === null || value === undefined ? "" : String(value);
  return select;
};
const labelled = (label, control, id) => h("div.field", h("label", { for: id }, label), control);

// ------------------------------------------------------------------ код первого входа (S3)

/** Окно с кодом: он виден один раз, читается по знакам; «Скопировать» кладёт в буфер код без пробелов. */
function codeSheet(user, reply) {
  const win = sheet({ title: `Код для входа: ${user.full_name}`, width: "460px" });
  const text = h("div.codebig", { "aria-label": codeAriaLabel(reply.code), role: "text" }, groupCode(reply.code));
  const status = h("span.hint", { role: "status" });
  const copy = h("button.b.fill", { type: "button", onClick: async () => {
    if (await copyText(reply.code)) {
      status.textContent = "Скопировано";
    } else {
      selectText(text);
      status.textContent = COPY_FALLBACK;
    }
  } }, "Скопировать");
  win.body.append(text, h("div.btns", copy, status), h("p.t2", { style: "margin:12px 0 0" }, codeExpiryText(reply.expires_at)),
    h("p.hint", { style: "margin:6px 0 0" }, "Код виден один раз. Передайте человеку лично: не по почте и не в мессенджере. Он вводит логин и этот код, потом придумывает пароль."),
    h("div.sheetfoot.end", h("button.b", { type: "button", onClick: () => win.close("ok") }, "Готово")));
  win.show(copy);
}

/** «Выдать код»: если у человека уже есть код или пароль — сначала предупреждаем, что прежнее перестанет действовать. */
async function issueCode({ api, signal, user, onDone }) {
  const hasCode = loginStatus(user).startsWith("код до");
  if (hasCode || user.has_password) {
    const agreed = await confirmSheet({ title: "Выдать новый код?", confirmText: "Выдать код",
      hint: [hasCode ? "Прежний код перестанет действовать." : null, user.has_password ? "У человека уже есть пароль: по новому коду он задаст его заново." : null].filter(Boolean).join(" ") });
    if (!agreed) return;
  }
  try {
    const reply = await api.post(`/api/access/user/${encodeURIComponent(user.id)}/code`, {}, { signal });
    codeSheet(user, reply);
    onDone();
  } catch (error) {
    if (error && error.name === "AbortError") return;
    toast(error && error.message ? error.message : "Не удалось выдать код");   // 403 цель — не тот, 409 неактивен или без роли
  }
}

// ------------------------------------------------------------------ окна «Доступ: ФИО» и «Добавить вручную» (S2)

function userSheet({ api, signal, user, units, onDone, canIssue }) {
  const win = sheet({ title: `Доступ: ${user.full_name}`, width: "520px",
    hint: `${user.login}${user.position ? ` · ${user.position}` : ""}${user.org_unit ? ` · ${user.org_unit}` : ""}` });
  const status = errorLine();
  const roleId = uid("role");
  const unitId = uid("unit");
  const role = roleSelect(roleId, user.role);
  const unit = unitSelect(unitId, units, user.org_unit_id);
  const save = h("button.b.fill", { type: "submit" }, "Сохранить");
  const form = () => ({ role: role.value, org_unit_id: unit.value, reset: false });
  const update = () => { save.disabled = !Object.keys(changesFor(user, form())).length; };
  role.addEventListener("change", update);
  unit.addEventListener("change", update);

  const send = async (changes, doneText) => {
    status.clear();
    save.disabled = true;
    try {
      await api.post(`/api/access/user/${encodeURIComponent(user.id)}`, changes, { signal });
      win.close("ok");
      toast(doneText);
      onDone();
    } catch (error) {
      if (error && error.name === "AbortError") { win.close("cancel"); return; }
      status.show(error);
      update();
    }
  };
  const reset = user.manual ? h("p", { style: "margin:10px 0 0" }, h("button.link", { type: "button", onClick: () => send({ manual: false }, "Роль снова по правилу 1С") }, "Вернуть правило 1С")) : null;
  const code = canIssue ? h("button.b", { type: "button", onClick: async () => { win.close("code"); await issueCode({ api, signal, user, onDone }); } }, "Выдать код") : null;
  const issueState = canIssueCode(user);
  if (code && !issueState.ok) code.setAttribute("aria-disabled", "true");

  win.body.append(h("form", { novalidate: true, onSubmit: (event) => {
    event.preventDefault();
    const changes = changesFor(user, form());
    if (Object.keys(changes).length) send(changes, "Доступ изменён: действует сразу");
  } },
  h("div.form", labelled("Роль", role, roleId), labelled("Подразделение", unit, unitId),
    h("p.hint", { style: "margin:0" }, `Подразделение по 1С: ${user.org_unit || "не указано"}. Действует сразу, запишется в журнал изменений.${user.manual ? " Роль задана вручную: выгрузка её не перетрёт." : ""}`)),
  reset, status.el,
  h("div.sheetfoot", code ? h("span", { style: "margin-right:auto" }, code, issueState.ok ? null : h("span.hint", { style: "margin-left:8px" }, issueState.reason)) : null,
    h("button.b", { type: "button", onClick: () => win.close("cancel") }, "Отмена"), save)));
  if (code && !issueState.ok) {
    code.addEventListener("click", (event) => { event.stopImmediatePropagation(); toast(issueState.reason); }, true);
  }
  update();
  win.show(role);
}

function newUserSheet({ api, signal, units, onDone }) {
  const win = sheet({ title: "Добавить вручную", width: "520px", hint: "Для тех, кого ещё нет в выгрузке из 1С. Подразделение выгрузка потом возьмёт из 1С, а роль останется вашей." });
  const status = errorLine();
  const login = field({ label: "Логин", name: "login", autocomplete: "off", required: true });
  const name = field({ label: "ФИО", name: "full_name", autocomplete: "off", required: true });
  const position = field({ label: "Должность", name: "position", autocomplete: "off" });
  const roleId = uid("role");
  const unitId = uid("unit");
  const role = roleSelect(roleId, "hiring_manager", { includeNoAccess: false });
  const unit = unitSelect(unitId, units, null);
  const save = h("button.b.fill", { type: "submit" }, "Добавить");
  const form = h("form", { novalidate: true, onSubmit: async (event) => {
    event.preventDefault();
    status.clear();
    const values = { login: login.input.value, full_name: name.input.value, position: position.input.value, org_unit_id: unit.value, role: role.value };
    const problem = newUserProblem(values);
    if (problem) { status.show(problem); return; }
    save.disabled = true;
    try {
      await api.post("/api/access/user", newUserBody(values), { signal });
      win.close("ok");
      toast(`Добавлен: ${values.full_name.trim()}. Теперь можно выдать код`);
      onDone();
    } catch (error) {
      if (error && error.name === "AbortError") { win.close("cancel"); return; }
      status.show(error);   // 409 «логин занят», 422 «роль не та»
      save.disabled = false;
    }
  } }, h("div.form", login.root, name.root, position.root, labelled("Подразделение", unit, unitId), labelled("Роль", role, roleId)),
  status.el, h("div.sheetfoot.end", h("button.b", { type: "button", onClick: () => win.close("cancel") }, "Отмена"), save));
  win.body.append(form);
  win.show(login.input);
}

// ------------------------------------------------------------------ экран

export function render(box, ctx) {
  const { api, signal, session } = ctx;
  const canManage = session.can("access.manage");
  const canIssue = session.can("invite.issue") || canManage;
  let data = null;
  let units = [];
  const filters = { q: "", role: "", managersOnly: false };

  const load = async () => {
    const reply = await api.get("/api/access/users", { signal });
    // подразделения нужны только окнам выбора: нет права или сбой — окна работают без списка
    const org = await api.get("/api/org", { signal }).catch(() => null);
    units = org && org.units ? org.units : [];
    return reply;
  };

  const view = (reply) => {
    data = reply;
    const syncBox = h("p.t2", { style: "margin:10px 0 0" });
    const found = h("span.hint", { role: "status" });
    const tbody = h("tbody");
    const refresh = async () => {
      try {
        data = await api.get("/api/access/users", { signal });
        if (!signal.aborted) draw();
      } catch (error) {
        if (!(error && error.name === "AbortError")) toast(error && error.message ? error.message : "Не удалось обновить список");
      }
    };

    const search = h("input.search", { id: "access-q", type: "search", "data-tip": "access-search", placeholder: "Поиск: ФИО, логин, должность, подразделение", autocomplete: "off",
      onInput: () => { filters.q = search.value; drawRows(); } });
    const roleFilter = h("select.search", { "aria-label": "Роль", style: "flex:0 0 240px", onChange: () => { filters.role = roleFilter.value; drawRows(); } },
      h("option", { value: "" }, "Все роли"), ROLE_ORDER.map((role) => h("option", { value: role }, roleText(role))));
    const managers = h("input", { type: "checkbox", id: "access-managers", onChange: () => { filters.managersOnly = managers.checked; drawRows(); } });

    const drawRows = () => {
      const shown = sortUsers(filterUsers(data.items, filters));
      found.textContent = `${shown.length} из ${data.items.length} показано`;
      if (!shown.length) {
        mount(tbody, h("tr", h("td.hint", { colspan: 7 }, data.items.length ? "Ничего не найдено" : "Пользователей пока нет: возьмите выгрузку из 1С или добавьте человека вручную")));
        return;
      }
      mount(tbody, shown.map((u) => {
        // строка открывает окно правки роли и подразделения только у access.manage; у специалиста — одна кнопка «Выдать код»
        const open = () => { if (canManage) userSheet({ api, signal, user: u, units, onDone: refresh, canIssue }); };
        const code = canIssue ? h("button.b.tint.rowbtn", { type: "button", "data-tip": "access-code", "aria-label": `Выдать код: ${u.full_name}`,
          onClick: (event) => { event.stopPropagation(); issueCode({ api, signal, user: u, onDone: refresh }); } }, "Выдать код") : null;
        const issue = canIssueCode(u);
        if (code && !issue.ok) {
          code.setAttribute("aria-disabled", "true");
          code.classList.add("inactive");
          code.addEventListener("click", (event) => { event.stopImmediatePropagation(); event.stopPropagation(); toast(issue.reason); }, true);
        }
        return h("tr.trow", { class: u.is_active ? null : "never", tabindex: canManage ? 0 : null, style: canManage ? null : "cursor:default",
          onClick: (event) => { if (!event.target.closest("button, a")) open(); },
          onKeydown: (event) => { if (event.key === "Enter" && event.target === event.currentTarget) open(); } },
        h("td.name", canManage ? h("a.link", { href: "#/access", onClick: (event) => { event.preventDefault(); open(); } }, u.full_name) : u.full_name),
        h("td", h("span.runcode", { style: "font-size:13px" }, u.login)), h("td.hint", u.position || "—"), h("td.hint", u.org_unit || "—"),
        h("td", chip(roleText(u.role), u.role === "no_access" ? "" : "g"), " ", u.manual ? chip("вручную", "v", "access-manual") : null),
        h("td", loginStatus(u)), h("td", code));
      }));
    };
    const draw = () => {
      syncBox.textContent = canManage ? syncLine(data.sync) : "";
      drawRows();
    };

    const sync = canManage ? h("button.b.fill", { type: "button", "data-tip": "access-sync", onClick: () => openImportSheet({ api, signal, title: "Обновить из 1С",
      hint: "Сотрудники берутся из выгрузки 1С. Роль по правилу: отдел управления персоналом — специалисты, его руководитель — руководитель отдела персонала, «Руководитель / Начальник / Директор» — руководители, остальные — без доступа. Роль, которую вы задали вручную, выгрузка не перетирает; уволенные теряют доступ.",
      syncText: syncLine(data.sync), refreshPath: "/api/access/sync", uploadPath: "/api/access/upload", resultText, fileError: orgFileError, onDone: refresh }) }, "Обновить из 1С") : null;
    const add = canManage ? h("button.b", { type: "button", "data-tip": "access-add", onClick: () => newUserSheet({ api, signal, units, onDone: refresh }) }, "Добавить вручную") : null;
    draw();

    return [
      h("div.card", h("div.t1", "Доступ"),
        h("div.t2", canManage ? "Кто может войти в рабочее место и с какой ролью. Роли назначаются по выгрузке сотрудников из 1С; изменение, которое вы сделали вручную, действует сразу и запишется в журнал."
          : "Руководители подразделений и директор, которым можно выдать код первого входа. Код виден один раз: передайте его человеку лично."),
        h("div.btns", sync, add), syncBox),
      h("form.tools", { role: "search", style: "margin-top:14px", onSubmit: (event) => event.preventDefault() },
        h("label.sr-only", { for: "access-q" }, "Поиск по пользователям"), search, roleFilter,
        h("label", { for: "access-managers", style: "display:inline-flex;align-items:center;gap:6px;font-size:14px;cursor:pointer", "data-tip": "access-managers" }, managers, "только руководители"), found),
      h("div.tablewrap", { style: "max-height:none" },
        h("table.access", h("caption.sr-only", "Пользователи рабочего места и их роли"),
          h("thead", h("tr", ["ФИО", "Логин", "Должность", "Подразделение", "Роль", "Вход", "Действие"].map((title) => h("th", { scope: "col" }, title)))), tbody)),
      canManage ? h("p.hint", { style: "margin:10px 6px 0" }, "Нажмите на строку, чтобы изменить роль и подразделение. «Вручную» — роль задана вами, выгрузка из 1С её не меняет.") : null,
    ];
  };

  return mountAsync(box, load, view, { signal });
}
