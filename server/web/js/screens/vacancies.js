// «Вакансии» (S21–S23, S38; П:342–370): открытые и закрытые, «Показать подходящих», воронка, закрытие с комментарием,
// «Открыть заново», «Новая вакансия». Данные — GET /api/vacancies. Всё из API выводится только текстом.
// Блоки «Заявка из 1С» и «Публикация на hh» из прототипа не показываются: заявки и публикация — отдельные фичи.
import { actBtn, actionCard, perform } from "../cards.js";
import { openCommentSheet } from "../decisions.js";
import { h, mount, uid } from "../dom.js";
import { isCabinet } from "../nav-model.js";
import { chip } from "../people.js";
import { emptyCard, errorLine, field, mountAsync, sheet, toast } from "../ui.js";
import { renderList as renderManagerList } from "./manager-vacancies.js";
import {
  CLOSE_HINT, CLOSE_REASONS, CLOSE_TOAST, REOPEN_TOAST, buildVacancy, closeResult, closedText, isStarving, openText,
  MANAGER_HINT, profileChoices, staffChoices, vacancyTitle,
} from "./vacancies-model.js";

const CLOSE_PLACEHOLDER = "Чем закрыта потребность, кто вышел на работу, что стоит учесть агенту в следующий раз по этой должности.";

// ------------------------------------------------------------------ новая вакансия (S21)

/**
 * Окно «Новая вакансия»: профиль из базы должностей (только утверждённые), название, ставки, срок, ответственный HR (по умолчанию я)
 * и руководитель-заказчик (подставляется по должности; не нашёлся — пусто с подсказкой «Выберите вручную»). Возвращает Promise<id | null>.
 */
function newVacancySheet({ api, signal, session }) {
  const win = sheet({ title: "Новая вакансия", width: "520px",
    hint: "Должность берётся из базы должностей: вакансия работает по её утверждённому профилю. Агент сразу покажет подходящих из накопленной базы." });
  const body = h("div", h("p.loading", "Загружаю профили…"));
  win.body.append(body);
  win.show();

  Promise.all([api.get("/api/profiles", { signal }), api.get("/api/staff", { query: { kind: "hr" }, signal }),
    api.get("/api/staff", { query: { kind: "managers" }, signal })]).then(([reply, hrReply, managersReply]) => {
    const choices = profileChoices(reply.items);
    if (!choices.length) {
      mount(body, h("p.t2", "Утверждённых профилей нет: сначала утвердите профиль в «Базе должностей»."),
        h("div.sheetfoot.end", h("button.b", { type: "button", onClick: () => win.close("cancel") }, "Закрыть")));
      return;
    }
    const profileId = uid("profile");
    const profile = h("select", { id: profileId, required: true },
      h("option", { value: "" }, "Выберите профиль…"), choices.map((c) => h("option", { value: c.id }, c.label)));
    const title = field({ label: "Название вакансии", name: "title", required: true });
    const headcount = field({ label: "Ставок", kind: "number", name: "headcount", value: "1", required: true });
    headcount.input.min = "1";
    const date = field({ label: "Срок закрытия (необязательно)", kind: "date", name: "target_date" });
    const hr = staffChoices(hrReply.items, session.user ? session.user.id : null);
    const managers = staffChoices(managersReply.items);
    const recruiterId = uid("recruiter");
    const recruiter = h("select", { id: recruiterId, required: true },
      h("option", { value: "" }, "Выберите ответственного…"), hr.options.map((o) => h("option", { value: o.id, selected: o.id === hr.selected }, o.label)));
    const managerId = uid("manager");
    const manager = h("select", { id: managerId, required: true, "aria-describedby": `${managerId}-hint` },
      h("option", { value: "" }, "Выберите руководителя…"), managers.options.map((o) => h("option", { value: o.id }, o.label)));
    const managerHint = h("p.hint", { id: `${managerId}-hint`, style: "margin:6px 0 0", role: "status" },
      "Заполнится по должности: руководитель её подразделения.");
    let managerTouched = false;   // руководителя выбрали руками: смена должности его не перетирает
    manager.addEventListener("change", () => { managerTouched = true; managerHint.textContent = ""; });
    const suggestManager = async () => {
      if (managerTouched || !profile.value) return;
      managerHint.textContent = "";
      let found = null;
      try {
        found = await api.get("/api/vacancy/defaults", { query: { role_profile_id: profile.value }, signal });
      } catch (error) {
        if (error && error.name === "AbortError") return;
      }
      if (managerTouched) return;
      const known = found && managers.options.some((o) => o.id === found.hiring_manager_id);
      manager.value = known ? String(found.hiring_manager_id) : "";
      managerHint.textContent = known ? `По подразделению должности: ${found.hiring_manager}` : MANAGER_HINT;
    };
    const status = errorLine();
    const save = h("button.b.fill", { type: "submit" }, "Создать вакансию");
    let autoTitle = "";   // название, подставленное из профиля: пока человек его не правил, оно следует за выбором
    profile.addEventListener("change", () => {
      const chosen = choices.find((c) => String(c.id) === profile.value);
      if (!title.input.value.trim() || title.input.value === autoTitle) {
        autoTitle = chosen ? chosen.title : "";
        title.input.value = autoTitle;
      }
      suggestManager();
    });
    const form = h("form", { novalidate: true }, h("div.form",
      h("div.field", h("label", { for: profileId }, "Профиль должности"), profile), title.root,
      h("div.two", headcount.root, date.root),
      h("div.field", h("label", { for: recruiterId }, "Ответственный HR"), recruiter),
      h("div.field", h("label", { for: managerId }, "Руководитель-заказчик"), manager, managerHint)), status.el,
    h("div.sheetfoot.end", h("button.b", { type: "button", onClick: () => win.close("cancel") }, "Отмена"), save));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      status.clear();
      const built = buildVacancy({ profileId: profile.value, title: title.input.value, headcount: headcount.input.value, targetDate: date.input.value,
        recruiterId: recruiter.value, hiringManagerId: manager.value });
      if (!built.ok) {
        status.show(built.error);
        return;
      }
      save.disabled = true;
      try {
        const created = await api.post("/api/vacancy", built.payload, { signal });
        win.created = created.id;
        win.close("created");
      } catch (error) {
        if (error && error.name === "AbortError") { win.close("cancel"); return; }
        status.show(error);   // 422 «Проверьте: профиль не утверждён» и т. п. — в окне, введённое остаётся
        save.disabled = false;
      }
    });
    mount(body, form);
    profile.focus();
  }).catch((error) => {
    if (error && error.name === "AbortError") return;
    const status = errorLine();
    status.show(error);
    mount(body, status.el, h("div.sheetfoot.end", h("button.b", { type: "button", onClick: () => win.close("cancel") }, "Закрыть")));
  });
  return win.closed.then((outcome) => (outcome === "created" ? win.created : null));
}

// ------------------------------------------------------------------ карточки

function openCard(v, env) {
  const go = (name, params, query) => () => env.go(name, params, query);
  const close = async () => {
    const result = await openCommentSheet({
      title: `Закрыть вакансию: ${v.title}`, hint: CLOSE_HINT, reasons: CLOSE_REASONS, saveLabel: "Закрыть вакансию",
      placeholder: CLOSE_PLACEHOLDER,
      send: (text) => env.api.post(`/api/vacancy/${encodeURIComponent(v.id)}/close`, { comment: text }, { signal: env.signal }),
    });
    if (!result.saved) return;
    env.notes.set(v.id, closeResult(result.length));
    toast(CLOSE_TOAST);
    await env.refresh();
  };
  const chips = [isStarving(v) ? chip("дольше нормы", "o") : null, v.state === "on_hold" ? chip("на паузе") : null].filter(Boolean);
  const people = [v.recruiter ? `Ответственный HR: ${v.recruiter}` : null, v.hiring_manager ? `Заказчик: ${v.hiring_manager}` : null].filter(Boolean).join(" · ");
  const card = actionCard(({ btns, status }) => [h("div.t1", vacancyTitle(v)), h("div.t2", openText(v)), people ? h("div.t2", people) : null,
    chips.length ? h("div.chips", chips) : null, btns, status], [
    h("button.b.fill", { type: "button", onClick: go("base", {}, { vacancy: v.id }) }, "Показать подходящих"),
    h("button.b.tint", { type: "button", onClick: go("funnel", { id: v.id }) }, "Воронка"),
    env.canEdit ? actBtn("danger", "Закрыть вакансию", close) : null,
  ]);
  return card.el;
}

function closedCard(v, env) {
  const reopen = () => perform(card, () => env.api.post(`/api/vacancy/${encodeURIComponent(v.id)}/reopen`, {}, { signal: env.signal }),
    async () => {
      env.notes.set(v.id, "Вакансия открыта заново: воронка и накопленные кандидаты сохранены, запись в журнале доступа");
      toast(REOPEN_TOAST);
      await env.refresh();
    });
  const card = actionCard(({ btns, status }) => [h("div.t1", vacancyTitle(v)), h("div.chips", { style: "margin:6px 0 0" }, chip("закрыта")),
    h("div.t2", closedText(v)),
    v.close_comment ? h("details", { style: "margin-top:6px" }, h("summary.link", "Комментарий при закрытии"),
      h("div.t2.lines", v.close_comment)) : null, btns, status], [
    h("button.b.tint", { type: "button", onClick: () => env.go("base", {}, { vacancy: v.id }) }, "Кто накопился по позиции"),
    env.canEdit ? actBtn("", "Открыть заново", reopen) : null,
  ]);
  return card.el;
}

/** Итог прошлого действия показывается на карточке, которая перерисовалась (закрыта или открыта заново). */
function withNote(node, id, env) {
  const text = env.notes.get(id);
  if (!text) return node;
  const result = node.querySelector(".result");
  result.textContent = `✓ ${text}`;
  env.focusAfter = result;
  return node;
}

// ------------------------------------------------------------------ экран

export function view(data, env) {
  const open = data.open || [];
  const closed = [...(data.closed || [])].sort((a, b) => String(b.closed_at).localeCompare(String(a.closed_at)) || b.id - a.id);
  const newButton = env.canEdit ? h("button.b.fill", { type: "button", onClick: () => env.create() }, "Новая вакансия") : null;
  return [
    h("div.sectionbar", h("h2.sechead", "Открытые вакансии"), newButton),
    h("div.stack", open.length ? open.map((v) => withNote(openCard(v, env), v.id, env))
      : emptyCard("Открытых вакансий нет", newButton && h("button.b.fill", { type: "button", onClick: () => env.create() }, "Новая вакансия"))),
    h("h2.sechead", "Закрытые вакансии"),
    closed.length ? h("div.stack", closed.map((v) => withNote(closedCard(v, env), v.id, env))) : h("p.hint", { style: "margin:0 6px" }, "Закрытых вакансий пока нет."),
    h("h2.sechead", "Откуда вакансия попадает в систему"),
    h("div.card", h("div.t1", "Рекрутер заводит сам"),
      h("div.t2", "Должность из базы должностей, число ставок, срок. Агент сразу показывает подходящих из накопленной базы.")),
  ];
}

export function render(box, ctx) {
  if (isCabinet(ctx.session.user)) return renderManagerList(box, ctx);   // «Мои вакансии»: без «Новой вакансии» и «Закрыть»
  const { api, signal, session } = ctx;
  const env = { api, signal, go: ctx.go, canEdit: session.can("vacancy.edit"), notes: new Map(), focusAfter: null };
  const draw = (data) => {
    ctx.setBadge("vacancies", data.open.length || "");
    env.focusAfter = null;
    const nodes = view(data, env);
    queueMicrotask(() => {
      if (env.focusAfter) env.focusAfter.focus({ preventScroll: false });
      env.notes.clear();
    });
    return nodes;
  };
  env.refresh = async () => {
    try {
      const data = await api.get("/api/vacancies", { signal });
      if (signal.aborted) return false;
      mount(box, draw(data));
      return true;
    } catch (error) {
      if (error && error.name === "AbortError") return false;
      toast(error && error.message ? error.message : "Не удалось обновить список");
      return false;
    }
  };
  env.create = async () => {
    const id = await newVacancySheet({ api, signal, session: ctx.session });
    if (id === null || id === undefined) return;
    env.notes.set(id, "Вакансия заведена: «Показать подходящих» откроет людей из накопленной базы");
    toast("Вакансия заведена");
    await env.refresh();
  };
  return mountAsync(box, () => api.get("/api/vacancies", { signal }), draw, { signal });
}
