// «Воронка» вакансии (S6–S10, S14, S16): вкладки «Кандидаты / Отчёт / Этапы», доска по этапам набора, окно «Итог этапа»,
// отмена последней отметки, пауза. Данные — GET /api/vacancy/{id}/funnel, …/funnel-report, …/stage-sets.
// Всё из API выводится только текстом (h(), textContent).
import { h, mount, uid } from "../dom.js";
import { funnelTable } from "../funnel-table.js";
import { isCabinet } from "../nav-model.js";
import { chip, face, personHref, personName } from "../people.js";
import { hashFor } from "../router.js";
import { emptyCard, errorLine, mountAsync, sheet, toast } from "../ui.js";
import {
  VIEWS, canDropOn, canUndo, dayText, funnelTotals, lastMarkText, pickVacancy, stageSummary, undoToast, viewOf,
} from "./funnel-model.js";
import { hintText, periodText } from "./funnel-report-model.js";
import { renderPage as renderManagerPage } from "./manager-vacancies.js";
import { markToast, closePrompt } from "./outcome-model.js";
import { outcomeSheet, undoSheet } from "./outcome-sheet.js";
import { stagesTab } from "./stages-view.js";
import { closeVacancy, togglePause } from "./vacancy-actions.js";
import { closedText, openText, pauseLabel, stageChips } from "./vacancies-model.js";

const PANEL_ID = "vacancy-panel";

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

function notFound() {
  return emptyCard("Вакансия не найдена", h("a.link", { href: "#/vacancies" }, "К «Вакансиям»"));
}

export function render(box, ctx) {
  if (isCabinet(ctx.session.user)) return renderManagerPage(box, ctx);   // страница вакансии руководителя: подходящие и воронка только для чтения
  const { api, signal, session } = ctx;
  const id = ctx.params.id;
  if (id && !/^\d+$/.test(id)) return mount(box, notFound());
  const canMove = session.can("person.take");
  const canEdit = session.can("vacancy.edit");
  const canApprove = session.can("stage.approve");
  const userId = session.user && session.user.id;
  let vacancyId = id;
  let view = viewOf(ctx.query);
  let lists = { open: [], closed: [] };
  let dragged = null;
  let notice = null;   // {text, undo: person|null, close: bool}: итог последнего действия на доске
  let reasonsPromise = null;
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
  /** Причины исхода — из справочника (GET /api/stages), один раз за экран. */
  const reasons = () => {
    if (!reasonsPromise) {
      reasonsPromise = api.get("/api/stages", { signal }).then((lib) => lib.reasons || []).catch((error) => { reasonsPromise = null; throw error; });
    }
    return reasonsPromise;
  };

  /** Перерисовать экран после действия: заголовок и вкладка — по свежим данным, без «Загружаю…». */
  const refresh = async () => {
    try {
      lists = await api.get("/api/vacancies", { signal });
      const funnel = await loadFunnel();
      if (signal.aborted) return;
      mount(box, funnel ? screen(funnel) : notFound());
    } catch (error) {
      if (error && error.name === "AbortError") return;
      status.show(error);
    }
  };

  // ---------------------------------------------------------------- действия на доске

  const openOutcome = async (funnel, person, slot, preselect) => {
    status.clear();
    notice = null;
    let list;
    try {
      list = await reasons();
    } catch (error) {
      if (error && error.name === "AbortError") return;
      status.show(error);
      return;
    }
    const item = [...lists.open, ...lists.closed].find((v) => v.id === funnel.vacancy.id);
    const result = await outcomeSheet({ api, signal, vacancyId: funnel.vacancy.id, person, slot, stages: funnel.stages, reasons: list,
      hiringManager: item && item.hiring_manager ? item.hiring_manager : "", preselect });
    if (!result.saved) return;
    const name = personName(person);
    const text = markToast({ name, outcome: result.outcome, nextTitle: result.nextTitle });
    toast(text);
    notice = { text, undo: result.outcome !== "passed" || !result.nextTitle ? person : null,
      close: result.offerClose && canEdit ? funnel.vacancy.headcount : 0 };
    await refresh();
  };

  const openUndo = async (funnel, person) => {
    status.clear();
    notice = null;
    const result = await undoSheet({ api, signal, vacancyId: funnel.vacancy.id, person });
    if (!result.saved) return;
    const stage = funnel.stages.find((s) => s.slot === result.slot);
    toast(undoToast(personName(person), stage ? stage.title : ""));
    await refresh();
  };

  const takePick = async (funnel, pick, button) => {
    status.clear();
    notice = null;
    button.disabled = true;
    try {
      await api.post(`/api/person/${encodeURIComponent(pick.person_id)}/take`, { vacancy_id: funnel.vacancy.id }, { signal });
      toast(`Взят в работу: ${personName(pick)}. Контакт не открыт — он откроется отдельно, с подтверждением`);
      await refresh();
    } catch (error) {
      if (error && error.name === "AbortError") return;
      status.show(error);
      button.disabled = false;
    }
  };

  const closeIt = async (funnel) => {
    status.clear();
    const result = await closeVacancy({ api, signal, vacancy: funnel.vacancy });
    if (!result.saved) return;
    notice = { text: result.text, undo: null, close: 0 };
    await refresh();
  };

  const pauseIt = async (funnel) => {
    status.clear();
    notice = null;
    try {
      if (await togglePause({ api, signal, vacancy: funnel.vacancy })) await refresh();
    } catch (error) {
      if (error && error.name === "AbortError") return;
      status.show(error);
    }
  };

  // ---------------------------------------------------------------- вкладка «Кандидаты»

  function personCard(funnel, person, stage) {
    const last = person.last_mark;
    const undo = canMove && canUndo({ last, userId, canApprove });
    return h("div.mini", {
      draggable: canMove ? "true" : null,
      onDragstart: (event) => {
        dragged = { person, from: stage.slot };
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", String(person.candidate_id));
      },
      onDragend: () => { dragged = null; box.querySelectorAll(".droptarget").forEach((el) => el.classList.remove("droptarget")); },
    },
    h("a.open", { href: personHref(person.person_id), title: "Открыть досье", "data-tip": "Открыть досье" },
      h("div.namewrap", face(person, { small: true }), h("b", personName(person))),
      h("span", dayText(person.days))),
    last ? h("span.lastmark", lastMarkText(last)) : null,
    canMove || undo ? h("div.btns.tight",
      canMove ? h("button.b.sm", { type: "button", onClick: () => openOutcome(funnel, person, stage.slot) }, "Итог этапа") : null,
      undo ? h("button.b.sm", { type: "button", "aria-label": `Отменить последнюю отметку: ${personName(person)}`,
        onClick: () => openUndo(funnel, person) }, "Отменить последнюю отметку") : null) : null);
  }

  function stageEl(funnel, stage, total) {
    const summary = stageSummary(stage, total);
    const headingId = uid("stage");
    const el = h("section.stage", { class: summary.empty ? "empty" : null, "aria-labelledby": headingId, dataset: { slot: stage.slot },
      onDragover: (event) => {
        if (!dragged || !canDropOn(funnel.stages, dragged.from, stage.slot)) return;   // только на следующий этап
        event.preventDefault();
        el.classList.add("droptarget");
      },
      onDragleave: () => el.classList.remove("droptarget"),
      onDrop: (event) => {
        event.preventDefault();
        el.classList.remove("droptarget");
        const drag = dragged;
        dragged = null;
        if (drag && canDropOn(funnel.stages, drag.from, stage.slot)) openOutcome(funnel, drag.person, drag.from, "passed");
      } },
    h("div.top", h("h2.nm", { id: headingId }, stage.title), h("span.cnt", summary.countText),
      h("span.meter", { "aria-hidden": "true" }, h("i", { style: `width:${summary.pct}%` })), h("span.days", summary.avgText)),
    stage.people.length ? h("div.cards", stage.people.map((p) => personCard(funnel, p, stage))) : null);
    return el;
  }

  function agentBlock(funnel) {
    const picks = funnel.agent || [];
    if (!picks.length) return null;
    return h("details.agentblock", { open: true },
      h("summary", `До воронки — отобраны агентом (${picks.length}), в отчёт не входят`),
      h("div.cards", picks.map((p) => {
        const take = h("button.b.sm.fill", { type: "button", "aria-label": `Взять в работу: ${personName(p)}`,
          onClick: () => takePick(funnel, p, take) }, "Взять в работу");
        return h("div.mini", { style: "cursor:default" },
          h("a.open", { href: personHref(p.person_id), title: "Открыть досье" },
            h("div.namewrap", face(p, { small: true }), h("b", personName(p))),
            h("span", typeof p.days === "number" ? `сигнал ${p.days === 0 ? "сегодня" : `${p.days} дн. назад`}` : "")),
          canMove ? take : null);
      })));
  }

  function noticeEl(funnel) {
    if (!notice) return null;
    const { text, undo, close } = notice;
    return h("div.note", { role: "status" }, text,
      undo || close ? h("div.btns.tight",
        undo && canMove ? h("button.b.sm", { type: "button", onClick: () => openUndo(funnel, undo) }, "Отменить последнюю отметку") : null,
        close ? h("span", { style: "margin-right:6px" }, closePrompt(close)) : null,
        close ? h("button.b.sm.fill", { type: "button", onClick: () => closeIt(funnel) }, "Закрыть вакансию") : null) : null);
  }

  function board(funnel) {
    const total = funnelTotals(funnel.stages);
    return [
      noticeEl(funnel),
      agentBlock(funnel),
      h("div", funnel.stages.map((s) => stageEl(funnel, s, total))),
      total === 0 ? h("p.hint", { style: "margin:10px 6px" },
        "В воронке пока никого: «Взять в работу» на «Сегодня», в «Досье» или в блоке «Отобраны агентом» ставит человека на этап «Отобранные резюме».") : null,
      funnel.closed ? h("p.hint", { style: "margin:10px 6px" }, `Завершили путь по вакансии: ${funnel.closed} чел. (вышли на работу, отказ). В этапах они не показываются.`) : null,
      h("p.hint", { style: "margin:10px 6px" },
        "Этапы идут сверху вниз, ширина полосы — доля людей на этапе. «Итог этапа» записывает, прошёл человек этап, не прошёл или отказался: "
        + "назад и через этап перевести нельзя, карточку можно перетащить только на следующий этап. Ошибочную отметку отменяет её автор в тот же день "
        + "или руководитель отдела персонала; каждая отметка пишется в историю с автором и временем."),
    ];
  }

  // ---------------------------------------------------------------- вкладки «Отчёт» и «Этапы»

  const reportTab = (report) => [
    h("div.card", { style: "margin-bottom:12px" }, h("div.t2", periodText(report))),
    funnelTable(report, { expand: true, title: report.title }),
    h("p.hint", { style: "margin:10px 6px" }, hintText(report.since)),
  ];

  /** Содержимое вкладки; Promise-загрузка и разметка. preloaded — уже загруженная доска. */
  function panelFor(name, funnel) {
    if (name === "report") {
      return { load: async () => {
        try {
          return await api.get(`/api/vacancy/${encodeURIComponent(vacancyId)}/funnel-report`, { signal });
        } catch (error) {
          if (error && error.status === 404) return null;
          throw error;
        }
      },
      render: (report) => (report ? reportTab(report)
        : emptyCard("Отчёт этой вакансии видит ответственный рекрутер и руководитель отдела персонала")) };
    }
    if (name === "stages") {
      return { load: () => api.get(`/api/vacancy/${encodeURIComponent(vacancyId)}/stage-sets`, { signal }), render: stagesTab };
    }
    return { load: async () => funnel || loadFunnel(), render: (data) => (data ? board(data) : notFound()) };
  }

  // ---------------------------------------------------------------- экран

  function screen(funnel) {
    const { vacancy } = funnel;
    const total = funnelTotals(funnel.stages);
    const item = [...lists.open, ...lists.closed].find((v) => v.id === vacancy.id);
    const text = item ? (item.state === "closed" || item.state === "cancelled" ? closedText(item) : openText(item))
      : `Ставок: ${vacancy.headcount} · в работе ${total}`;
    const stateChip = vacancy.state === "closed" ? "закрыта" : vacancy.state === "on_hold" ? "на паузе" : vacancy.state !== "open" ? String(vacancy.state) : "";
    const chips = [stateChip ? chip(stateChip) : null, ...stageChips(item || {}).map((c) => chip(c.text, c.tone))].filter(Boolean);
    const pausable = canEdit && (vacancy.state === "open" || vacancy.state === "on_hold");

    const panel = h("div", { id: PANEL_ID, role: "tabpanel" });
    const tabs = new Map();
    let preloaded = funnel;   // доска уже загружена: первая вкладка берёт её, дальше — свежий запрос
    const select = (name, { focus = false } = {}) => {
      view = name;
      for (const [tab, button] of tabs) {
        const on = tab === name;
        button.setAttribute("aria-selected", String(on));
        button.setAttribute("tabindex", on ? "0" : "-1");
        button.classList.toggle("on", on);
        if (on) panel.setAttribute("aria-labelledby", button.id);
      }
      if (focus) tabs.get(name).focus();
      window.history.replaceState(null, "", hashFor("funnel", { id: vacancy.id }, name === "candidates" ? {} : { view: name }));   // адрес показывает вкладку
      const content = panelFor(name, name === "candidates" ? preloaded : null);
      preloaded = null;
      mountAsync(panel, content.load, content.render, { signal });
    };
    const tablist = h("div.seg", { role: "tablist", "aria-label": "Вакансия",
      onKeydown: (event) => {
        const names = VIEWS.map(([name]) => name);
        const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
        if (!step && event.key !== "Home" && event.key !== "End") return;
        event.preventDefault();
        const at = names.indexOf(view);
        const next = event.key === "Home" ? 0 : event.key === "End" ? names.length - 1 : (at + step + names.length) % names.length;
        select(names[next], { focus: true });
      } },
    VIEWS.map(([name, title]) => {
      const button = h("button", { type: "button", role: "tab", id: uid("vtab"), "aria-controls": PANEL_ID, "aria-selected": "false",
        tabindex: "-1", dataset: { view: name }, "data-tip": title, onClick: () => select(name) }, title);
      tabs.set(name, button);
      return button;
    }));

    const header = h("div.card", { style: "margin-bottom:4px" }, h("div.row",
      h("div.grow", h("h2.t1", vacancy.title), h("div.t2", text), chips.length ? h("div.chips", chips) : null),
      h("div.btns", { style: "margin:0" },
        h("button.b.tint", { type: "button", onClick: () => ctx.go("base", {}, { vacancy: vacancy.id }) }, "Показать подходящих"),
        h("button.b", { type: "button", onClick: () => chooseVacancy({ open: lists.open, current: vacancy.id,
          onPick: (pick) => ctx.go("funnel", { id: pick }) }) }, "Другая вакансия"),
        pausable ? h("button.b", { type: "button", "data-tip": pauseLabel(vacancy), onClick: () => pauseIt(funnel) }, pauseLabel(vacancy)) : null)),
    status.el);
    queueMicrotask(() => select(view));
    return [header, tablist, panel];
  }

  return mountAsync(box, load, (data) => {
    if (data.redirect) return null;
    if (data.none) {
      return emptyCard("Открытых вакансий нет", h("a.link", { href: "#/vacancies" }, "Перейти к «Вакансиям»"));
    }
    return data.funnel ? screen(data.funnel) : notFound();
  }, { signal });
}
