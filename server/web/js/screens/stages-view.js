// Вкладка «Этапы» вакансии (S2–S5): действующий набор по строкам отчёта и редактор «Предлагаю» — добавить этап из справочника,
// убрать, переставить ↑/↓ внутри строки, подпись, «Отправить на утверждение», «Отозвать», «Править заново» после отклонения.
// Руководителю ОП на этой вкладке доступны те же «Утвердить / Отклонить», что в блоке на «Воронке». Всё из API — только текстом.
import { h, mount, uid } from "../dom.js";
import { chip } from "../people.js";
import { announce, errorLine, toast } from "../ui.js";
import { approveSet, declineSet } from "./stage-approvals.js";
import { approvalLine, diffSets } from "./stage-approvals-model.js";
import {
  NOT_IN_SET, addItem, addOptions, groupDraft, isChanged, moveItem, movePlace, proposalPayload, rebaseDraft, removeItem, removedRows,
} from "./stage-editor-model.js";
import { PENDING_NOTE, declinedNote, groupItems, isStandardSet, setHeading } from "./stages-model.js";

const SENT_TOAST = "Набор отправлен на утверждение";
const WITHDRAWN_TOAST = "Набор отозван";
const AGAIN_HINT = "Набор собран заново из прежнего предложения: поправьте и отправьте на утверждение ещё раз.";

function column(heading, set, tone) {
  const groups = groupItems(set.items, set.people);
  return h("div",
    h("h2.sechead", heading, isStandardSet(set) ? chip("стандартный", tone) : null),
    groups.map((g) => h("section", { "aria-label": g.title },
      h("h3.sechead", g.title),
      h("div.list", g.items.map((i) => h("div.li", { style: "cursor:default" },
        h("div.grow", i.title), h("span.cnt.t2", i.peopleText)))))));
}

/** Предложенный набор, ждущий решения: только чтение, с причиной. */
function proposedColumn(set) {
  return h("div", column("Предлагается", set, "o"), set.note ? h("blockquote.quote", `«${set.note}»`) : null);
}

// ------------------------------------------------------------------ редактор «Предлагаю»

/**
 * start — {items, again}: с чего начать черновик (действующий набор, отозванный или отклонённый — «Править заново»).
 * onSent() — набор отправлен: вкладку перерисовывает вызывающий.
 */
function editor({ api, signal, vacancyId, current, library, start, onSent }) {
  let draft = rebaseDraft(start.items, current.items);
  let counter = 0;
  const people = current.people || {};
  const listBox = h("div");
  const removedBox = h("p.hint", { style: "margin:8px 6px 0" });
  const status = errorLine();
  const noteId = uid("why");
  const note = h("textarea", { id: noteId, rows: 2, name: "why" }, start.note || "");
  const send = h("button.b.fill", { type: "button", disabled: true, "data-tip": "stage-send" }, "Отправить на утверждение");
  const options = addOptions(library);
  const pickSelect = h("select", { id: uid("add"), "aria-label": "Этап из справочника" },
    h("option", { value: "" }, "Выберите этап…"),
    options.map((g) => h("optgroup", { label: g.title }, g.items.map((s) => h("option", { value: String(s.id) }, s.title)))));
  const pick = pickSelect;
  const addButton = h("button.b", { type: "button", disabled: true }, "+ Добавить этап");

  const sync = () => {
    const rows = removedRows(draft, library.rows);
    removedBox.textContent = rows.length ? `Строки вне набора: ${rows.map((r) => `«${r.title}»`).join(", ")} — ${NOT_IN_SET}.` : "";
    send.disabled = !isChanged(draft, current.items);
  };

  /** Нарисовать список; focus — {key, act}: кнопка, на которую вернуть фокус (если она неактивна — на парную). */
  function draw(focus) {
    status.clear();
    const groups = groupDraft(draft, people);
    listBox.replaceChildren(...groups.map((g) => h("section", { "aria-label": g.title },
      h("h3.sechead", g.title),
      h("div.list", g.items.map((i) => itemRow(i))))));
    sync();
    if (focus) {
      const row = listBox.querySelector(`[data-key="${focus.key}"]`);
      const pair = { up: "down", down: "up" }[focus.act];
      const target = row && [focus.act, pair, "remove"].map((a) => row.querySelector(`[data-act="${a}"]`)).find((b) => b && !b.disabled);
      if (target) target.focus();
    }
  }

  function itemRow(i) {
    const hintId = uid("rm");
    const labelInput = h("input", { type: "text", value: i.label, style: "padding:6px 10px;font-size:13.5px", placeholder: "Подпись вместо названия (необязательно)", "aria-label": `Подпись этапа «${i.stageTitle}»`,
      onInput: (event) => {
        const item = draft.find((d) => d.key === i.key);
        if (item) item.label = event.currentTarget.value;
        sync();
      } });
    const label = h("div.field", { style: "margin-top:6px" }, labelInput);
    const move = (step, act, text) => h("button.b.sm", { type: "button", dataset: { act }, disabled: !(step < 0 ? i.up : i.down), style: "margin-top:0",
      "aria-label": `${text}: ${i.title}`, "data-tip": text, onClick: () => {
        draft = moveItem(draft, i.key, step);
        draw({ key: i.key, act });
        announce(movePlace(draft, i.key, library.rows));
      } }, step < 0 ? "↑" : "↓");
    const remove = h("button.b.sm", { type: "button", dataset: { act: "remove" }, disabled: i.remove.disabled, style: "margin-top:0",
      "aria-label": `Убрать этап: ${i.title}`, "aria-describedby": i.remove.disabled ? hintId : null,
      "data-tip": i.remove.note ? `Убрать этап: ${i.remove.note}` : "Убрать этап",
      onClick: () => {
        const place = groupDraft(draft, people).findIndex((g) => g.items.some((x) => x.key === i.key));
        draft = removeItem(draft, i.key);
        draw(null);
        announce(`Этап «${i.title}» убран${i.remove.lastInRow ? `: ${NOT_IN_SET}` : ""}`);
        const rows = listBox.querySelectorAll("section");
        const next = rows[Math.min(place, rows.length - 1)];
        const target = next && next.querySelector("[data-act=remove]:not(:disabled), input");
        if (target) target.focus();
      } }, "✕");
    const hint = i.remove.disabled ? i.remove.reason : "";
    return h("div.li", { style: "cursor:default;flex-wrap:wrap", dataset: { key: i.key } },
      h("div.grow", { style: "min-width:200px" }, h("div", i.stageTitle), label),
      h("span.cnt.t2", i.peopleText), move(-1, "up", "Выше"), move(1, "down", "Ниже"), remove,
      hint ? h("div.hint", { id: hintId, style: "flex-basis:100%;margin:0" }, hint) : null);
  }

  pick.addEventListener("change", () => { addButton.disabled = !pick.value; });
  addButton.addEventListener("click", () => {
    const stage = options.flatMap((g) => g.items).find((s) => String(s.id) === pick.value);
    if (!stage) return;
    counter += 1;
    const key = `n${counter}`;
    draft = addItem(draft, stage, key);
    pick.value = "";
    addButton.disabled = true;
    draw(null);
    announce(`Добавлен этап «${stage.title}»: ${movePlace(draft, key, library.rows).split(" — ")[1]}`);
    pick.focus();
  });

  send.addEventListener("click", async () => {
    status.clear();
    send.disabled = true;
    try {
      await api.post(`/api/vacancy/${encodeURIComponent(vacancyId)}/stage-sets`, proposalPayload(draft, note.value), { signal });
      toast(SENT_TOAST);
      onSent();
    } catch (error) {
      if (error && error.name === "AbortError") return;
      status.show(error);   // 409 stage_busy и прочее — под кнопкой, черновик остаётся
      sync();
    }
  });

  draw(null);
  return h("div", h("h2.sechead", "Предлагаю"),
    start.again ? h("div.note", { role: "note" }, AGAIN_HINT) : null,
    listBox, removedBox,
    h("div.btns", { style: "align-items:center" }, h("div.field", { style: "flex:1 1 220px" }, pick), addButton),
    h("div.field", { style: "margin-top:12px" }, h("label", { for: noteId }, "Зачем меняете (необязательно)"), note),
    status.el, h("div.btns", send));
}

// ------------------------------------------------------------------ вкладка

/**
 * data — {sets: ответ GET /api/vacancy/{id}/stage-sets, library: ответ GET /api/stages}.
 * env — {api, signal, vacancy: {id, title}, canEdit, canApprove, carry: {items}, refresh()}: carry держит набор, который
 * только что отозвали (его можно предложить снова), refresh() перерисовывает экран вакансии (плашки в шапке тоже).
 */
export function stagesTab(data, env) {
  const { sets, library } = data;
  const { current, proposed, declined } = sets;
  const { api, signal, vacancy } = env;
  const root = h("div");

  const act = async (status, run) => {
    status.clear();
    try {
      await run();
    } catch (error) {
      if (error && error.name === "AbortError") return;
      status.show(error);
    }
  };

  function pendingNote() {
    const status = errorLine();
    const diff = diffSets(current ? current.items : null, proposed.items);
    const withdraw = h("button.b.sm", { type: "button", onClick: () => act(status, async () => {
      await api.post(`/api/stage-sets/${encodeURIComponent(proposed.id)}/withdraw`, {}, { signal });
      env.carry.items = proposed.items;
      env.carry.note = proposed.note || "";
      toast(WITHDRAWN_TOAST);
      await env.refresh();
    }) }, "Отозвать");
    const decide = (approve) => async () => {
      const args = { api, signal, set: proposed, vacancyTitle: vacancy.title };
      if (await (approve ? approveSet(args) : declineSet(args))) await env.refresh();
    };
    const approveBtn = h("button.b.sm.fill", { type: "button", "data-tip": "stage-approve", onClick: () => act(status, decide(true)) }, "Утвердить");
    const declineBtn = h("button.b.sm.danger", { type: "button", "data-tip": "stage-decline", onClick: () => act(status, decide(false)) }, "Отклонить");
    return h("div.note", { role: "note" }, PENDING_NOTE,
      approvalLine({ ...proposed, recruiter: "" }) ? h("div.t2", approvalLine({ ...proposed, recruiter: "" })) : null,
      env.canApprove && diff.length ? h("div.chips", diff.map((d) => chip(d.text, d.tone))) : null,
      h("div.btns.tight", env.canEdit ? withdraw : null, env.canApprove ? [approveBtn, declineBtn] : null),
      status.el);
  }

  function declinedNoteEl() {
    return h("div.note.bad", { role: "note" }, declinedNote(declined),
      env.canEdit ? h("div.btns.tight", h("button.b.sm", { type: "button", onClick: () => draw({ items: declined.items, again: true }) }, "Править заново")) : null);
  }

  /** start — с чего начать черновик; по умолчанию — отозванный набор (если есть) или действующий. */
  function draw(start) {
    let begin = start;
    if (!begin && env.carry.items) begin = { items: env.carry.items, again: true, note: env.carry.note || "" };
    env.carry.items = null;
    env.carry.note = "";
    if (!begin && current) begin = { items: current.items, again: false };
    const canEdit = env.canEdit && Boolean(current);
    const showDeclined = declined && !proposed && !(start && start.again);
    mount(root,
      proposed ? pendingNote() : null,
      showDeclined ? declinedNoteEl() : null,
      h("div.split",
        current ? column(setHeading(current), current, "")
          : h("div", h("h2.sechead", "Действует"), h("p.hint", "Набор этапов ещё не создан: вакансия идёт по 8 стандартным этапам.")),
        proposed ? proposedColumn(proposed)
          : canEdit ? editor({ api, signal, vacancyId: vacancy.id, current, library, start: begin, onSent: () => env.refresh() }) : null));
  }

  draw(null);
  return root;
}
