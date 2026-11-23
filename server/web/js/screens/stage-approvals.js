// Утверждение наборов этапов руководителем ОП (S4): блок «Ждут утверждения — наборы этапов» на «Воронке»,
// окно «Отклонить» с обязательным комментарием, решения «Утвердить / Отклонить» — те же на вкладке «Этапы».
// Всё из API выводится только текстом (h(), textContent).
import { actBtn, actionCard, perform } from "../cards.js";
import { h } from "../dom.js";
import { chip } from "../people.js";
import { confirmSheet, errorLine, field, sheet, toast } from "../ui.js";
import { APPROVE_HINT, DECLINE_HINT, approvalLine, decisionToast, diffSets, zoneTitle } from "./stage-approvals-model.js";
import { COMMENT_MAX, groupItems } from "./stages-model.js";

/** Вакансии с ждущими наборами и их наборы: GET /api/vacancies, затем stage-sets каждой. */
export async function loadPending(api, signal) {
  const lists = await api.get("/api/vacancies", { signal });
  const pending = [...(lists.open || []), ...(lists.closed || [])].filter((v) => v.stages_pending);
  const entries = await Promise.all(pending.map(async (vacancy) => ({
    vacancy, sets: await api.get(`/api/vacancy/${encodeURIComponent(vacancy.id)}/stage-sets`, { signal }),
  })));
  return { lists, entries: entries.filter((e) => e.sets.proposed) };
}

/** «Утвердить»: подтверждение, затем POST. Возвращает Promise<boolean> (утверждено); ошибка — исключением. */
export async function approveSet({ api, signal, set, vacancyTitle }) {
  const agreed = await confirmSheet({ title: `Утвердить набор этапов: ${vacancyTitle}?`, confirmText: "Утвердить", hint: APPROVE_HINT });
  if (!agreed) return false;
  await api.post(`/api/stage-sets/${encodeURIComponent(set.id)}/approve`, {}, { signal });
  toast(decisionToast("approved"));
  return true;
}

/** Окно «Отклонить набор»: комментарий обязателен; ошибка — под кнопкой, текст остаётся. Promise<boolean>. */
export function declineSet({ api, signal, set, vacancyTitle }) {
  const win = sheet({ title: `Отклонить набор этапов: ${vacancyTitle}`, hint: DECLINE_HINT, width: "480px" });
  const reason = field({ label: "Комментарий", kind: "textarea", name: "decline_comment", required: true, tip: "reason", maxlength: COMMENT_MAX });
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
      await api.post(`/api/stage-sets/${encodeURIComponent(set.id)}/decline`, { comment: text }, { signal });
      toast(decisionToast("declined"));
      win.close("declined");
    } catch (error) {
      if (error && error.name === "AbortError") { win.close("cancel"); return; }
      status.show(error);
      save.disabled = false;
    }
  });
  win.body.append(form);
  win.show(reason.input);
  return win.closed.then((outcome) => outcome === "declined");
}

/** Набор целиком по строкам отчёта: этап и «N чел.», если люди известны. */
function setList(set, caption) {
  const groups = groupItems(set ? set.items : [], (set && set.people) || {});
  return h("div", h("h3.sechead", caption),
    groups.length ? groups.map((g) => h("div", h("div.t2", { style: "font-weight:600;margin-top:6px" }, g.title),
      h("ul", { style: "margin:2px 0 0;padding-left:20px" }, g.items.map((i) => h("li", i.title))))) : h("p.hint", "Набора нет"));
}

/** Карточка набора: вакансия, кто и когда, что изменилось, зачем, «Наборы целиком», «Утвердить / Отклонить». */
export function approvalCard({ api, signal, entry, go, onDecided }) {
  const { vacancy, sets } = entry;
  const set = sets.proposed;
  const diff = diffSets(sets.current ? sets.current.items : null, set.items);
  const open = h("button.b.tint", { type: "button", onClick: () => go("funnel", { id: vacancy.id }, { view: "stages" }) }, "Открыть вакансию");
  const decide = (state) => () => perform(card, async () => {
    if (state === "approved") return approveSet({ api, signal, set, vacancyTitle: vacancy.title });
    return declineSet({ api, signal, set, vacancyTitle: vacancy.title });
  }, (done) => {
    if (!done) { card.busy(false); return; }
    card.finish(decisionToast(state));
    onDecided(state, entry);
  });
  const card = actionCard(({ btns, status }) => [
    h("div.t1", vacancy.title),
    h("div.t2", approvalLine({ ...set, recruiter: vacancy.recruiter })),
    diff.length ? h("div.chips", diff.map((d) => chip(d.text, d.tone))) : null,
    set.note ? h("blockquote.quote", `«${set.note}»`) : null,
    h("details", { style: "margin-top:10px" }, h("summary", "Наборы целиком (было / стало)"),
      h("div.two", { style: "margin-top:8px" }, setList(sets.current, "Было"), setList(set, "Стало"))),
    btns, status,
  ], [actBtn("fill", "Утвердить", decide("approved"), "stage-approve"), actBtn("danger", "Отклонить", decide("declined"), "stage-decline"), open]);
  return card.el;
}

/**
 * Блок «Ждут утверждения — наборы этапов» (S4). Нечего утверждать — блока нет (null).
 * onChange(left) вызывается после каждого решения: left — сколько наборов ещё ждёт (для счётчика вкладки).
 */
export function approvalsZone({ api, signal, entries, go, onChange }) {
  if (!entries.length) return null;
  let left = entries.length;
  const headId = "stage-approvals-head";
  const title = h("span", zoneTitle(left));
  const stack = h("div.stack", entries.map((entry) => approvalCard({ api, signal, entry, go, onDecided: () => {
    left -= 1;
    title.textContent = zoneTitle(left);
    onChange(left);
  } })));
  return h("section", { "aria-labelledby": headId },
    h("h2.sechead", { id: headId }, h("span.dot", { class: "o", "aria-hidden": "true" }, "!"), title),
    h("div.zone.z2", stack));
}
