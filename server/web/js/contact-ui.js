// Платный контакт hh (S16, S17): окно «Спишется один контакт hh», предупреждение о скрытых контактах, показ контактов.
// Значения контактов приходят только из GET /api/candidate/{id}/contacts и показываются только в досье, текстом.
import { confirmTexts, contactPlan, errorHint } from "./contacts-model.js";
import { h } from "./dom.js";
import { personName } from "./people.js";
import { errorLine, sheet } from "./ui.js";

/** Контакты кандидата; каждый показ пишется в журнал доступа на сервере. */
export async function fetchContacts(api, candidateId, signal) {
  const reply = await api.get(`/api/candidate/${encodeURIComponent(candidateId)}/contacts`, { signal });
  return reply.contacts || [];
}

/** Подготовка больше не нужна (контакт открыт или скрыт): действие снимается из очереди, чтобы не висело в «Сегодня». */
export async function quietDecline(api, signal, actionId, reason) {
  try {
    await api.post(`/api/action/${encodeURIComponent(actionId)}/decline`, { reason }, { signal });
  } catch { /* не вышло — действие просто останется в очереди */ }
}

/** hh скрыл все каналы связи: кнопки подтверждения нет (S17). */
export function blockedSheet(plan, person) {
  const win = sheet({ title: "Контакты скрыты", width: "440px",
    hint: `${personName(person)}: соискатель скрыл контакты на hh${plan.hidden.length ? ` (${plan.hidden.join(", ")})` : ""}. Открывать нечего, контакт не спишется.` });
  const ok = h("button.b.fill", { type: "button", onClick: () => win.close("ok") }, "Понятно");
  win.body.append(h("div.sheetfoot.end", ok));
  win.show(ok);
  return win.closed.then(() => undefined);
}

/**
 * Окно подтверждения платного контакта. Фокус — на «Отмена». Подтверждение: POST /api/action/{id}/approve;
 * ошибка (занят сбор hh, лимит, сбой) показывается в окне, действие остаётся в очереди.
 * plan — из contactPlan(); возвращает Promise<"opened" | "cancel">.
 */
export function contactSheet({ api, signal, actionId, plan, person }) {
  const texts = confirmTexts(plan);
  const win = sheet({ title: "Открыть контакт?", hint: texts.text, width: "460px" });
  const status = errorLine();
  const cancel = h("button.b", { type: "button", onClick: () => win.close("cancel") }, "Отмена");
  const open = h("button.b.fill", { type: "button", onClick: async () => {
    status.clear();
    cancel.disabled = true;
    open.disabled = true;
    try {
      await api.post(`/api/action/${encodeURIComponent(actionId)}/approve`, {}, { signal });
      win.close("opened");
    } catch (error) {
      if (error && error.name === "AbortError") { win.close("cancel"); return; }
      status.show(`${error.message || "Не удалось открыть контакт."} ${errorHint(error)}`.trim());
      cancel.disabled = false;
      open.disabled = false;
    }
  } }, texts.button);
  win.body.append(
    h("p", { style: "margin:0 0 10px" }, `Кандидат: ${personName(person)}`),
    plan.hidden.length ? h("div.note", { role: "note" }, `hh скрыл у соискателя: ${plan.hidden.join(", ")}. Остальное будет открыто.`) : null,
    status.el, h("div.sheetfoot.end", cancel, open));
  win.show(cancel);
  return win.closed.then((outcome) => (outcome === "opened" ? "opened" : "cancel"));
}

/**
 * «Открыть контакт» из досье: prepare → показ / «скрыто» / окно подтверждения → список контактов.
 * Возвращает {contacts, opened} или null, если человек отказался или контакты скрыты.
 */
export async function startContact({ api, signal, candidateId, person }) {
  const prep = await api.post(`/api/candidate/${encodeURIComponent(candidateId)}/contact/prepare`, {}, { signal });
  const plan = contactPlan(prep);
  if (plan.mode === "show") {
    await quietDecline(api, signal, prep.action_id, "Автоматически: контакт уже открыт");
    return { contacts: await fetchContacts(api, candidateId, signal), opened: false };
  }
  if (plan.mode === "blocked") {
    await quietDecline(api, signal, prep.action_id, "Автоматически: соискатель скрыл контакты");
    await blockedSheet(plan, person);
    return null;
  }
  const outcome = await contactSheet({ api, signal, actionId: prep.action_id, plan, person });
  if (outcome !== "opened") return null;   // действие остаётся в «Ждут подтверждения»: открыть можно позже
  return { contacts: await fetchContacts(api, candidateId, signal), opened: true };
}
