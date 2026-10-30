// Письма кандидату (S18): окно черновика «Скопировать и одобрить» и окно причины отказа.
// Письмо не уходит само: человек копирует текст и отправляет его сам. Всё из API выводится только текстом.
import { COPY_FALLBACK, copyText, selectText } from "./clipboard.js";
import { h } from "./dom.js";
import { personName, templateChip } from "./people.js";
import { announce, errorLine, field, sheet } from "./ui.js";

const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

/**
 * Окно причины отказа: непустой текст → POST /api/action/{id}/decline. Возвращает Promise<boolean> (отклонено ли).
 * Ошибка — под кнопкой, окно не закрывается.
 */
export function declineSheet({ api, signal, actionId, title = "Отклонить черновик",
  hint = "Причина сохранится в очереди: по ней агент учится готовить такие письма лучше." }) {
  const win = sheet({ title, hint, width: "480px" });
  const reason = field({ label: "Причина", kind: "textarea", name: "reason", required: true, tip: "reason" });
  const status = errorLine();
  const save = h("button.b.danger", { type: "submit", disabled: true }, "Отклонить");
  const form = h("form", { novalidate: true }, reason.root, status.el,
    h("div.sheetfoot.end", h("button.b", { type: "button", onClick: () => win.close("cancel") }, "Отмена"), save));
  reason.input.addEventListener("input", () => { save.disabled = !reason.input.value.trim(); });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = reason.input.value.trim();
    if (!text) return;
    status.clear();
    save.disabled = true;
    try {
      await api.post(`/api/action/${encodeURIComponent(actionId)}/decline`, { reason: text }, { signal });
      win.close("declined");
    } catch (error) {
      if (error && error.name === "AbortError") { win.close("cancel"); return; }
      status.show(error);   // окно остаётся, причина не теряется
      save.disabled = false;
    }
  });
  win.body.append(form);
  win.show(reason.input);
  return win.closed.then((outcome) => outcome === "declined");
}

/**
 * Окно черновика: текст можно править; «Скопировать и одобрить» копирует и записывает итоговый текст в историю
 * кандидата, «Отклонить» просит причину, «Закрыть» оставляет черновик в очереди.
 *   action: {action_id, kind, text, by}; person: {name, label}.
 * Возвращает Promise<"approved" | "declined" | "later">.
 */
export function draftSheet({ api, signal, action, person }) {
  const win = sheet({ title: `Письмо: ${personName(person)}`, width: "640px",
    hint: "Агент подготовил черновик. Правьте текст как нужно: итоговый вариант сохранится в истории кандидата. Письмо само не уходит: вы копируете его и отправляете сами." });
  const letter = field({ label: "Текст письма", kind: "textarea", name: "letter", value: action.text || "", required: true, tip: "letter" });
  letter.input.classList.add("tall");
  const note = h("p.hint", { role: "status", style: "margin:8px 0 0" });
  const status = errorLine();
  const id = encodeURIComponent(action.action_id);

  const close = h("button.b", { type: "button", onClick: () => win.close("later") }, "Закрыть");
  const decline = h("button.b.danger", { type: "button", onClick: async () => {
    if (busy) return;
    const done = await declineSheet({ api, signal, actionId: action.action_id });
    if (done) { outcome = "declined"; win.close("declined"); }
  } }, "Отклонить");
  const approve = h("button.b.fill", { type: "button", onClick: () => approveAndCopy() }, "Скопировать и одобрить");
  const refresh = () => { approve.disabled = busy || approved || !letter.input.value.trim(); };

  let busy = false;
  let approved = false;
  let outcome = "later";
  letter.input.addEventListener("input", refresh);

  async function approveAndCopy() {
    const text = letter.input.value.trim();
    if (!text || busy || approved) return;
    busy = true;
    status.clear();
    note.textContent = "";
    decline.disabled = true;
    refresh();
    const copied = await copyText(text);
    if (!copied) {   // буфер недоступен: текст выделяется, человек копирует сам
      selectText(letter.input);
      note.textContent = `${COPY_FALLBACK} — текст выделен`;
      announce(`${COPY_FALLBACK}, текст выделен`);
    }
    try {
      await api.post(`/api/action/${id}/approve`, { text }, { signal });
    } catch (error) {
      if (error && error.name === "AbortError") { win.close("later"); return; }
      status.show(error);
      busy = false;
      decline.disabled = false;
      refresh();
      return;
    }
    approved = true;
    outcome = "approved";
    letter.input.readOnly = true;
    close.textContent = "Готово";
    if (copied) {
      approve.textContent = "Скопировано";
      announce("Скопировано");
      await sleep(2000);   // кнопка «Скопировано» видна 2 секунды, потом окно закрывается
      win.close("approved");
    } else {
      note.textContent = `Письмо одобрено. ${COPY_FALLBACK} — текст выделен, потом закройте окно.`;
      announce(note.textContent);
      selectText(letter.input);
    }
  }

  win.body.append(h("div.chips", { style: "margin:0 0 10px" }, action.by === "template" ? templateChip("по шаблону") : null),
    letter.root, note, status.el, h("div.sheetfoot.end", close, decline, approve));
  refresh();
  win.show(letter.input);
  return win.closed.then(() => outcome);
}
