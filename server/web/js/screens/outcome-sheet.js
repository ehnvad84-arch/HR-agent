// Окна доски вакансии: «Итог этапа» (S6–S9, S36) и «Отменить последнюю отметку» (S16).
// Всё из API выводится только текстом. Ошибка сервера — под кнопкой, окно не закрывается, введённое остаётся.
import { countChars, minFromError } from "../decisions-model.js";
import { h, uid } from "../dom.js";
import { personName } from "../people.js";
import { errorLine, field, sheet } from "../ui.js";
import { buildMark, OTHER_MIN, otherCounter, outcomeForm, reasonGroups } from "./outcome-model.js";

/**
 * Окно «Итог этапа». stages — колонки доски; reasons и min — GET /api/stages → reasons и other_min; preselect — «passed» при перетаскивании.
 * Возвращает Promise<{saved, outcome, name, nextTitle, offerClose}>.
 */
export function outcomeSheet({ api, signal, vacancyId, person, slot, stages, reasons, min = OTHER_MIN, hiringManager = "", preselect = "" }) {
  const form = outcomeForm({ stages, slot, hiringManager });
  const name = personName(person);
  const win = sheet({ title: `Итог этапа: ${name}`, width: "480px" });
  let limit = min;

  const lead = h("div.t2", { style: "margin:0 0 10px" }, [form.stage ? form.stage.title : "", form.resolution].filter(Boolean).join(" · "));

  // варианты исхода: fieldset + legend, радиокнопки с подписями
  const groupName = uid("outcome");
  const radios = form.options.map((o) => {
    const id = uid("opt");
    const input = h("input", { type: "radio", name: groupName, id, value: o.value });
    return { value: o.value, input, row: h("div.optrow", input, h("label", { for: id }, o.label)) };
  });
  const choose = (value) => { const r = radios.find((x) => x.value === value); if (r) { r.input.checked = true; sync(); } };
  const outcomeBox = h("fieldset.outcomes", h("legend", "Итог"), radios.map((r) => r.row));

  // дата встречи — если следующий этап встреча (S36); необязательна
  const when = field({ label: "Дата и время встречи (необязательно)", kind: "datetime-local", name: "scheduled_at" });
  when.root.hidden = true;

  // причина — select своего исхода; «Другое» ведёт к комментарию от порога
  const reasonId = uid("reason");
  const reasonSelect = h("select", { id: reasonId });
  const reasonBox = h("div.field", { hidden: true }, h("label", { for: reasonId }, "Причина"), reasonSelect);

  const counterId = uid("counter");
  const comment = field({ label: "Комментарий", kind: "textarea", name: "comment", describedBy: counterId });
  const counter = h("span.counter.bad", { id: counterId }, otherCounter("", limit));
  counter.hidden = true;
  comment.root.hidden = true;

  const status = errorLine();
  const save = h("button.b.fill", { type: "submit", disabled: true }, "Сохранить итог");

  const current = () => (radios.find((r) => r.input.checked) || {}).value || "";
  function fillReasons(outcome) {
    const { groups, other } = reasonGroups(reasons, outcome, form.stage ? form.stage.report_row : "");
    reasonSelect.replaceChildren(h("option", { value: "" }, "Выберите…"),
      ...groups.map((g) => h("optgroup", { label: g.label }, g.items.map((i) => h("option", { value: i.code }, i.title)))),
      other ? h("option", { value: other.code }, other.title) : null);
  }
  function sync() {
    const outcome = current();
    const needsReason = (outcome === "failed" || outcome === "refused") && !(outcome === "refused" && form.fixedReason);
    if (needsReason && reasonSelect.dataset.outcome !== outcome) {
      fillReasons(outcome);
      reasonSelect.dataset.outcome = outcome;
    }
    reasonBox.hidden = !needsReason;
    when.root.hidden = !(outcome === "passed" && form.askWhen);
    const other = needsReason && reasonSelect.value === "other";
    comment.root.hidden = !outcome;
    counter.hidden = !other;
    comment.root.querySelector("label").textContent = other ? `Комментарий (обязательно, от ${limit} знаков)`
      : form.resolution && outcome === "passed" ? "Резолюция руководителя (необязательно)" : "Комментарий (необязательно)";
    counter.textContent = otherCounter(comment.input.value, limit);
    counter.classList.toggle("bad", countChars(comment.input.value) < limit);
    save.disabled = !outcome || (needsReason && !reasonSelect.value);
  }
  radios.forEach((r) => r.input.addEventListener("change", () => { status.clear(); sync(); }));
  reasonSelect.addEventListener("change", sync);
  comment.input.addEventListener("input", sync);

  const formEl = h("form", { novalidate: true }, lead, h("div.form", outcomeBox, when.root, reasonBox, comment.root),
    status.el, h("div.sheetfoot", counter, h("button.b", { type: "button", onClick: () => win.close("cancel") }, "Отмена"), save));
  let result = { saved: false };
  formEl.addEventListener("submit", async (event) => {
    event.preventDefault();
    status.clear();
    const built = buildMark({ slot, outcome: current(), reason: reasonSelect.value, comment: comment.input.value,
      when: when.input.value, fixedReason: form.fixedReason, min: limit });
    if (!built.ok) { status.show(built.error); return; }
    save.disabled = true;
    try {
      const reply = await api.post(`/api/vacancy/${encodeURIComponent(vacancyId)}/candidate/${encodeURIComponent(person.candidate_id)}/mark`,
        built.payload, { signal });
      result = { saved: true, outcome: built.payload.outcome, name, offerClose: Boolean(reply && reply.offer_close),
        nextTitle: form.next && built.payload.outcome === "passed" ? form.next.title : "" };
      win.close("saved");
    } catch (error) {
      if (error && error.name === "AbortError") { win.close("cancel"); return; }
      limit = minFromError(error, limit);   // сервер строже клиента: счётчик берёт его число
      status.show(error);   // 409 stale — «этап кандидата уже другой», окно остаётся
      sync();
    }
  });
  win.body.append(formEl);
  if (preselect) choose(preselect);
  sync();
  win.show(radios[0].input);
  return win.closed.then((outcome) => (outcome === "saved" ? result : { saved: false }));
}

/** Окно «Отменить последнюю отметку»: причина обязательна (она пишется в журнал). Возвращает Promise<{saved, slot}>. */
export function undoSheet({ api, signal, vacancyId, person }) {
  const name = personName(person);
  const win = sheet({ title: `Отменить последнюю отметку: ${name}`, width: "460px",
    hint: "Человек вернётся на этап, с которого ушёл, без итога; отчёт пересчитается. Отмена пишется в журнал с причиной." });
  const reason = field({ label: "Причина отмены", kind: "textarea", name: "cancel_comment", required: true });
  const status = errorLine();
  const save = h("button.b.fill", { type: "submit", disabled: true }, "Отменить отметку");
  reason.input.addEventListener("input", () => { save.disabled = !reason.input.value.trim(); });
  let result = { saved: false };
  const formEl = h("form", { novalidate: true }, reason.root, status.el,
    h("div.sheetfoot.end", h("button.b", { type: "button", onClick: () => win.close("cancel") }, "Не отменять"), save));
  formEl.addEventListener("submit", async (event) => {
    event.preventDefault();
    const comment = reason.input.value.trim();
    if (!comment) return;
    status.clear();
    save.disabled = true;
    try {
      const reply = await api.post(`/api/vacancy/${encodeURIComponent(vacancyId)}/candidate/${encodeURIComponent(person.candidate_id)}/undo`,
        { comment }, { signal });
      result = { saved: true, slot: reply && reply.slot };
      win.close("saved");
    } catch (error) {
      if (error && error.name === "AbortError") { win.close("cancel"); return; }
      status.show(error);   // 403 «Отменить может руководитель ОП», 409 — под кнопкой, причина остаётся
      save.disabled = false;
    }
  });
  win.body.append(formEl);
  win.show(reason.input);
  return win.closed.then((outcome) => (outcome === "saved" ? result : { saved: false }));
}
