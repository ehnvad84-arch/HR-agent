// «Воронка» (S24, S36; П:466–484): этапы вакансии с людьми, перевод между этапами кнопкой и мышью, встреча и отказ.
// Данные — GET /api/vacancy/{id}/funnel. Всё из API выводится только текстом (h(), textContent).
import { h, mount, uid } from "../dom.js";
import { dayLabel, timeLabel } from "../format.js";
import { isCabinet } from "../nav-model.js";
import { chip, face, personHref, personName } from "../people.js";
import { emptyCard, errorLine, field, mountAsync, sheet, toast } from "../ui.js";
import {
  buildMove, dayText, defaultTarget, funnelTotals, meetingStage, moveOptions, needsWindow, pickVacancy, reasonMode, stageSummary,
  statusLabel,
} from "./funnel-model.js";
import { renderPage as renderManagerPage } from "./manager-vacancies.js";
import { closedText, openText } from "./vacancies-model.js";

// ------------------------------------------------------------------ окно «Перевести»

/**
 * Окно перевода кандидата: этап или итог, для встреч — дата и время, для отказов — причина.
 * Ошибка сервера (422 и др.) — под кнопкой, окно открыто. Возвращает Promise<{moved, status, when}>.
 */
function moveSheet({ api, signal, vacancyId, person, current, stages, preselect }) {
  const options = moveOptions(stages, current);
  const win = sheet({ title: `Перевести: ${personName(person)}`, width: "480px",
    hint: "Каждый перевод пишется в историю статусов с автором и временем." });
  const selectId = uid("stage");
  const select = h("select", { id: selectId },
    h("optgroup", { label: "Этапы воронки" }, options.stages.map((o) => h("option", { value: o.value }, o.label))),
    h("optgroup", { label: "Итог" }, options.final.map((o) => h("option", { value: o.value }, o.label))));
  select.value = preselect || defaultTarget(stages, current);

  const when = field({ label: "Дата и время встречи (необязательно)", kind: "datetime-local", name: "scheduled_at" });
  const reason = field({ label: "Причина отказа", kind: "textarea", name: "reason" });
  const whenBox = when.root;
  const reasonBox = reason.root;
  const status = errorLine();
  const save = h("button.b.fill", { type: "submit" }, "Перевести");

  const sync = () => {
    const target = select.value;
    whenBox.hidden = !meetingStage(target);
    const mode = reasonMode(target);
    reasonBox.hidden = mode === "none";
    reason.root.querySelector("label").textContent = mode === "required" ? "Причина отказа" : "Комментарий (необязательно)";
    reason.input.required = mode === "required";
  };
  select.addEventListener("change", sync);
  sync();

  const form = h("form", { novalidate: true }, h("div.form",
    h("div.field", h("label", { for: selectId }, "Куда перевести"), select), whenBox, reasonBox),
  status.el, h("div.sheetfoot.end", h("button.b", { type: "button", onClick: () => win.close("cancel") }, "Отмена"), save));
  let outcome = { moved: false };
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    status.clear();
    const built = buildMove({ vacancyId, toStatus: select.value, comment: reason.input.value, when: when.input.value });
    if (!built.ok) {
      status.show(built.error);
      return;
    }
    save.disabled = true;
    try {
      await api.post(`/api/candidate/${encodeURIComponent(person.candidate_id)}/status`, built.payload, { signal });
      outcome = { moved: true, status: select.value, when: built.payload.scheduled_at || "" };
      win.close("moved");
    } catch (error) {
      if (error && error.name === "AbortError") { win.close("cancel"); return; }
      status.show(error);   // 422 «Неизвестный этап воронки» и т. п. — в окне, введённое остаётся
      save.disabled = false;
    }
  });
  win.body.append(form);
  win.show(select);
  return win.closed.then((result) => (result === "moved" ? outcome : { moved: false }));
}

// ------------------------------------------------------------------ другая вакансия

function chooseVacancy({ open, current, onPick }) {
  const win = sheet({ title: "Другая вакансия", width: "520px", hint: "Выберите вакансию: воронка откроется по ней." });
  const rows = open.map((v) => h("button.li", { type: "button", "data-tip": "vacancy-pick", onClick: () => { win.close("picked"); onPick(v.id); } },
    h("div", h("div", { style: "font-weight:600" }, v.title, v.id === current ? " · сейчас" : ""),
      h("div.sub", `${openText(v)}`))));
  win.body.append(open.length ? h("div.list", { style: "box-shadow:none" }, rows) : h("p.t2", "Открытых вакансий нет."),
    h("div.sheetfoot.end", h("button.b", { type: "button", onClick: () => win.close("cancel") }, "Закрыть")));
  win.show(rows[0] || undefined);
}

// ------------------------------------------------------------------ экран

function notFound(ctx) {
  return emptyCard("Вакансия не найдена", h("a.link", { href: "#/vacancies" }, "К «Вакансиям»"));
}

export function render(box, ctx) {
  if (isCabinet(ctx.session.user)) return renderManagerPage(box, ctx);   // страница вакансии руководителя: подходящие и воронка только для чтения
  const { api, signal, session } = ctx;
  const id = ctx.params.id;
  if (id && !/^\d+$/.test(id)) return mount(box, notFound(ctx));
  const canMove = session.can("person.take");
  let vacancyId = id;
  let lists = { open: [], closed: [] };
  let dragged = null;
  const status = errorLine();

  const loadFunnel = async () => {
    try {
      return await api.get(`/api/vacancy/${encodeURIComponent(vacancyId)}/funnel`, { signal });
    } catch (error) {
      if (error && error.status === 404) return null;
      throw error;
    }
  };
  const load = async () => {
    lists = await api.get("/api/vacancies", { signal });
    if (!vacancyId) {
      const first = pickVacancy(lists.open);
      if (!first) return { none: true };
      vacancyId = String(first.id);
      ctx.go("funnel", { id: vacancyId }, {}, { replace: true });   // адрес показывает, какая вакансия открыта; экран перерисуется
      return { redirect: true };
    }
    return { funnel: await loadFunnel() };
  };

  const refresh = async () => {
    try {
      const funnel = await loadFunnel();
      if (signal.aborted) return;
      mount(box, view(funnel));
    } catch (error) {
      if (error && error.name === "AbortError") return;
      status.show(error);
    }
  };

  /** Перевод: ошибка — в строке над воронкой; успех — уведомление и новая воронка. */
  const afterMove = async (stages, person, result) => {
    const label = statusLabel(stages, result.status);
    toast(result.when ? `Переведён: ${label}. Встреча ${dayLabel(result.when)}, ${timeLabel(result.when)} — видна в «Сегодня и завтра»`
      : `Переведён: ${label}`);
    await refresh();
  };
  const openMove = async (stages, person, current, preselect) => {
    status.clear();
    const result = await moveSheet({ api, signal, vacancyId: Number(vacancyId), person, current, stages, preselect });
    if (result.moved) await afterMove(stages, person, result);
  };
  const dropOn = async (stages, stage) => {
    const drag = dragged;
    dragged = null;
    if (!drag || drag.from === stage.status) return;
    if (needsWindow(stage.status)) {
      await openMove(stages, drag.person, drag.from, stage.status);
      return;
    }
    status.clear();
    try {
      await api.post(`/api/candidate/${encodeURIComponent(drag.person.candidate_id)}/status`,
        { vacancy_id: Number(vacancyId), to_status: stage.status }, { signal });
      await afterMove(stages, drag.person, { status: stage.status, when: "" });
    } catch (error) {
      if (error && error.name === "AbortError") return;
      status.show(error);
    }
  };

  function personCard(person, stage, stages) {
    return h("div.mini", {
      draggable: canMove ? "true" : null,
      onDragstart: (event) => {
        dragged = { person, from: stage.status };
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", String(person.candidate_id));
      },
      onDragend: () => { dragged = null; box.querySelectorAll(".droptarget").forEach((el) => el.classList.remove("droptarget")); },
    },
    h("a.open", { href: personHref(person.person_id), title: "Открыть досье", "data-tip": "Открыть досье" },
      h("div.namewrap", face(person, { small: true }), h("b", personName(person))),
      h("span", dayText(person.days))),
    canMove ? h("button.b.sm", { type: "button", onClick: () => openMove(stages, person, stage.status) }, "Перевести") : null);
  }

  function stageEl(stage, stages, total) {
    const summary = stageSummary(stage, total);
    const headingId = uid("stage");
    const el = h("section.stage", { class: summary.empty ? "empty" : null, "aria-labelledby": headingId, dataset: { status: stage.status },
      onDragover: (event) => {
        if (!dragged || dragged.from === stage.status) return;
        event.preventDefault();
        el.classList.add("droptarget");
      },
      onDragleave: () => el.classList.remove("droptarget"),
      onDrop: (event) => {
        event.preventDefault();
        el.classList.remove("droptarget");
        dropOn(stages, stage);
      } },
    h("div.top", h("h2.nm", { id: headingId }, stage.title), h("span.cnt", summary.countText),
      h("span.meter", { "aria-hidden": "true" }, h("i", { style: `width:${summary.pct}%` })), h("span.days", summary.avgText)),
    stage.people.length ? h("div.cards", stage.people.map((p) => personCard(p, stage, stages))) : null);
    return el;
  }

  function view(funnel) {
    if (!funnel) return notFound(ctx);
    const { vacancy, stages } = funnel;
    const total = funnelTotals(stages);
    const item = [...lists.open, ...lists.closed].find((v) => v.id === vacancy.id);
    const text = item ? (item.state === "closed" || item.state === "cancelled" ? closedText(item) : openText(item))
      : `Ставок: ${vacancy.headcount} · в работе ${total}`;
    return [
      h("div.card", { style: "margin-bottom:14px" }, h("div.row",
        h("div.grow", h("h2.t1", vacancy.title), h("div.t2", text),
          vacancy.state !== "open" ? h("div.chips", chip(vacancy.state === "closed" ? "закрыта" : vacancy.state === "on_hold" ? "на паузе" : String(vacancy.state))) : null),
        h("div.btns", { style: "margin:0" },
          h("button.b.tint", { type: "button", onClick: () => ctx.go("base", {}, { vacancy: vacancy.id }) }, "Показать подходящих"),
          h("button.b", { type: "button", onClick: () => chooseVacancy({ open: lists.open, current: vacancy.id,
            onPick: (pick) => ctx.go("funnel", { id: pick }) }) }, "Другая вакансия"))),
      status.el),
      h("div", stages.map((s) => stageEl(s, stages, total))),
      total === 0 ? h("p.hint", { style: "margin:10px 6px" }, "В воронке пока никого: «Сохранить» на «Сегодня» или в «Досье» ставит людей на этап «Отобран агентом».") : null,
      funnel.closed ? h("p.hint", { style: "margin:10px 6px" }, `Завершили путь по вакансии: ${funnel.closed} чел. (вышли на работу, отказ). В этапах они не показываются.`) : null,
      h("p.hint", { style: "margin:10px 6px" },
        "Этапы идут сверху вниз, ширина полосы — доля людей на этапе. Карточку можно перетащить на другой этап или нажать «Перевести» (для встреч — с датой, для отказов — с причиной); каждое перемещение пишется в историю статусов с автором и временем."),
    ];
  }

  return mountAsync(box, load, (data) => {
    if (data.redirect) return null;
    if (data.none) {
      return emptyCard("Открытых вакансий нет", h("a.link", { href: "#/vacancies" }, "Перейти к «Вакансиям»"));
    }
    return view(data.funnel);
  }, { signal });
}
