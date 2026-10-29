// «Сегодня» (S4, S5, S36, S37; П:305–320): четыре зоны — сигналы, ждут подтверждения, зависли, сегодня и завтра.
// Данные — GET /api/today. Всё из ответа выводится только текстом (h(), textContent).
import { actBtn, actionCard, perform } from "../cards.js";
import { h, mount } from "../dom.js";
import { actionTitle, alertReason, dayLabel, capitalize, interviewTitle, silentText, timeLabel, whenLabel } from "../format.js";
import { blockedSheet, contactSheet, quietDecline } from "../contact-ui.js";
import { actionOutcome, contactPlan, isLetter } from "../contacts-model.js";
import { TOASTS, resultText, takenText } from "../decisions-model.js";
import { openDecisionSheet, takePerson } from "../decisions.js";
import { declineSheet, draftSheet } from "../letter-ui.js";
import { chip, face, personHref, personName, scoreBadge, summaryBlock } from "../people.js";
import { hashFor } from "../router.js";
import { emptyCard, errorLine, errorText, field, mountAsync, sheet, toast } from "../ui.js";
import { normalizeToday } from "./today-model.js";

const nameLine = (person) => h("div.namewrap", face(person, { small: true }),
  h("div.t1", h("a", { href: personHref(person.person_id) }, personName(person))));

// ------------------------------------------------------------------ зоны

function signalCard(row, env) {
  const reason = alertReason(row.reason);
  const dossier = () => env.go("person", { id: row.person_id });
  const profiles = row.role_profile_id ? [{ role_profile_id: row.role_profile_id, role_title: row.role_title, score: row.score }] : [];
  /** «Временно отложить» и «Категорически нет»: общее окно комментария (200 знаков); после сохранения карточка гаснет. */
  const decide = (kind) => async () => {
    const result = await openDecisionSheet({ api: env.api, signal: env.signal, kind, person: row, matches: profiles });
    if (!result.saved) return;
    card.finish(resultText(kind, result.length));
    env.resolved();
    toast(TOASTS[kind]);
  };
  const card = actionCard(({ btns, status }) =>
    h("div.row.sigrow", scoreBadge(row.score), face(row, { size: "lg" }),
      h("div.grow", h("div.t1", h("a", { href: personHref(row.person_id) }, personName(row))),
        h("div.t2", [row.role_title, row.city].filter(Boolean).join(" · ")),
        h("div.t2", { style: "color:var(--ink2)" }, row.headline),
        h("div.chips", chip(reason.title, reason.tone)),
        btns, status),
      summaryBlock(row.summary)),
  [
    // вакансию определяет профиль человека: одна — сразу, несколько — окно выбора, ни одной — подтверждение «в резерв»
    actBtn("fill", "Сохранить", () => perform(card, () => takePerson({ api: env.api, signal: env.signal, person: row }),
      (result) => {
        if (!result.taken) { card.busy(false); return; }
        card.finish(takenText(result.vacancy));
        env.resolved();
        toast("Взят в работу");
      }), "take"),
    actBtn("", "Временно отложить", decide("postpone")),
    actBtn("danger", "Категорически нет", decide("reject")),
    h("button.b.tint", { type: "button", onClick: dossier }, "Открыть досье"),
  ]);
  return card.el;
}

/** Карточка «Ждут подтверждения»: письма — «Одобрить» (окно черновика) и «Отклонить», контакт — «Открыть контакт». */
function approvalCard(row, env) {
  const title = [actionTitle(row.kind), row.person_id ? personName(row) : null].filter(Boolean).join(" — ");
  const heading = row.person_id
    ? h("div.namewrap", face(row, { small: true }), h("div.t1", h("a", { href: personHref(row.person_id) }, title)))
    : h("div.t1", title);
  const id = encodeURIComponent(row.action_id);
  const letter = isLetter(row.kind);
  const contact = row.kind === "open_contact" && env.session.can("contact.open");
  const canAct = (letter || contact) && env.session.can("person.take");
  const gone = (text) => { card.finish(text); env.resolved(); };
  const alreadyDecided = (action) => {
    if (action.state === "proposed") return false;
    card.status.show("Это действие уже решено. Обновите экран.");
    card.busy(false);
    return true;
  };

  const review = () => perform(card, () => env.api.get(`/api/action/${id}`, { signal: env.signal }), async (action) => {
    if (alreadyDecided(action)) return;
    const outcome = await draftSheet({ api: env.api, signal: env.signal, person: row,
      action: { action_id: row.action_id, kind: row.kind, text: action.payload.text || "", by: action.payload.by } });
    const result = actionOutcome(outcome, row.kind);
    toast(result.toast);
    if (result.resolved) gone(result.card);
    else card.busy(false);
  });

  const openContact = () => perform(card, () => env.api.get(`/api/action/${id}`, { signal: env.signal }), async (action) => {
    if (alreadyDecided(action)) return;
    const plan = contactPlan(action.payload);
    if (plan.mode === "blocked") {   // hh скрыл все каналы: открывать нечего, действие снимается
      await quietDecline(env.api, env.signal, row.action_id, "Автоматически: соискатель скрыл контакты");
      await blockedSheet(plan, row);
      gone("Контакты скрыты соискателем: открывать нечего");
      return;
    }
    if (plan.mode === "show") {
      await quietDecline(env.api, env.signal, row.action_id, "Автоматически: контакт уже открыт");
      gone("Контакт уже открыт: значения — в досье кандидата");
      return;
    }
    const outcome = await contactSheet({ api: env.api, signal: env.signal, actionId: row.action_id, plan, person: row });
    if (outcome === "opened") {
      toast("Контакт открыт, запись в журнале доступа");
      gone("Контакт открыт: значения — в досье кандидата");
    } else {
      card.busy(false);   // «Отмена»: действие остаётся в очереди
    }
  });

  const reject = async () => {
    const done = await declineSheet({ api: env.api, signal: env.signal, actionId: row.action_id,
      title: contact ? "Отклонить открытие контакта" : "Отклонить черновик",
      hint: contact ? "Причина сохранится в очереди; контакт не спишется." : undefined });
    if (done) {
      toast("Отклонено, причина записана");
      gone("Отклонено, причина записана");
    }
  };

  const card = actionCard(({ btns, status }) => [heading,
    h("div.t2", [row.vacancy_title ? `Вакансия: ${row.vacancy_title}` : null, `подготовлено ${whenLabel(row.proposed_at)}`]
      .filter(Boolean).join(" · ")), btns, status], [
    canAct && letter ? actBtn("fill", "Одобрить", review) : null,
    canAct && contact ? actBtn("fill", "Открыть контакт", openContact) : null,
    canAct ? actBtn("danger", "Отклонить", reject) : null,
    row.person_id ? h("button.b.tint", { type: "button", onClick: () => env.go("person", { id: row.person_id }) }, "Открыть досье") : null,
  ]);
  return card.el;
}

function closeSheet(row, card, env) {
  const win = sheet({ title: "Закрыть: отказ кандидата", hint: "Опишите причину — она сохранится в истории кандидата.", width: "480px" });
  const reason = field({ label: "Причина", kind: "textarea", name: "reason", required: true });
  const status = errorLine();
  const save = h("button.b.danger", { type: "submit", disabled: true }, "Закрыть кандидатство");
  const form = h("form", { novalidate: true }, reason.root, status.el,
    h("div.sheetfoot", h("button.b", { type: "button", onClick: () => win.close("cancel") }, "Отмена"), save));
  reason.input.addEventListener("input", () => { save.disabled = !reason.input.value.trim(); });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const comment = reason.input.value.trim();
    if (!comment) return;
    status.clear();
    save.disabled = true;
    try {
      await env.api.post(`/api/candidate/${encodeURIComponent(row.candidate_id)}/status`,
        { to_status: "rejected_by_candidate", comment }, { signal: env.signal });
      win.close("saved");
    } catch (error) {
      if (error && error.name === "AbortError") return;
      status.show(error);   // окно остаётся, текст причины не теряется
      save.disabled = false;
    }
  });
  win.body.append(form);
  win.show(reason.input);
  win.closed.then((outcome) => {
    if (outcome !== "saved") return;
    card.finish("Закрыт: отказ кандидата, причина записана");
    toast("Кандидат закрыт, причина записана");
  });
}

function stalledCard(row, env) {
  const id = encodeURIComponent(row.candidate_id);
  const remind = actBtn("fill", "Напомнить", () => perform(card,
    () => env.api.post(`/api/candidate/${id}/draft`, { kind: "follow_up" }, { signal: env.signal }),
    async () => {
      toast("Черновик напоминания в очереди");
      if (!(await env.refresh())) card.busy(false);
    }));
  const card = actionCard(({ btns, status }) => [nameLine(row), h("div.t2", silentText(row.status, row.days_silent)), btns, status], [
    remind,
    actBtn("", "В резерв", () => perform(card,
      () => env.api.post(`/api/candidate/${id}/status`, { to_status: "reserve" }, { signal: env.signal }),
      () => { card.finish("Перенесён в резерв"); toast("Перенесён в резерв"); })),
    actBtn("danger", "Закрыть", () => closeSheet(row, card, env)),
  ]);
  if (env.followUpQueued.has(row.candidate_id)) {   // второй черновик подряд не нужен
    remind.disabled = true;
    card.note("Напоминание уже в очереди подтверждений");
  }
  return card.el;
}

function meetingCard(row) {
  const when = capitalize(`${dayLabel(row.scheduled_at)}, ${timeLabel(row.scheduled_at)}`);
  return h("div.card", h("div.cardbody",
    h("div.namewrap", face(row, { small: true }),
      h("div.t1", h("a", { href: personHref(row.person_id) }, `${when} — ${interviewTitle(row.kind).toLowerCase()}, ${personName(row)}`))),
    row.vacancy_title ? h("div.t2", `Вакансия: ${row.vacancy_title}`) : null));
}

function zone(num, tone, cls, title, cards, emptyText) {
  const headId = `today-zone-${num}`;
  return h("section", { "aria-labelledby": headId },
    h("h2.sechead", { id: headId }, h("span.dot", { class: tone }, num), title),
    h("div.zone", { class: cls }, h("div.stack", cards.length ? cards : emptyCard(emptyText))));
}

// ------------------------------------------------------------------ экран

export function view(data, env) {
  if (data.empty) {
    return h("div.card.empty", h("div.t1", "Сегодня всё разобрано"),
      h("div.btns", h("a.link", { href: hashFor("base") }, "Открыть «Базу»")));
  }
  const cardEnv = { ...env, followUpQueued: data.followUpQueued };
  return [
    zone("1", "g", "z1", "Обратите внимание — новые и изменившиеся люди", data.signals.map((r) => signalCard(r, cardEnv)),
      "Новых сигналов нет"),
    zone("2", "o", "z2", "Ждут подтверждения — агент подготовил, решаете вы", data.actions.map((r) => approvalCard(r, cardEnv)), "Ничего не ждёт"),
    zone("3", "r", "z3", "Зависли — давно без движения", data.stalled.map((r) => stalledCard(r, cardEnv)), "Ничего не ждёт"),
    zone("4", "b", "z4", "Сегодня и завтра", data.meetings.map(meetingCard), "Встреч на сегодня и завтра нет"),
  ];
}

export function render(box, ctx) {
  const { api, signal, setBadge } = ctx;
  let badge = 0;
  const showBadge = () => setBadge("today", badge > 0 ? badge : "");
  const load = async () => normalizeToday(await api.get("/api/today", { signal }));

  const env = {
    api, signal, go: ctx.go, session: ctx.session,
    /** Сигнал разобран (сохранён, отложен, «категорически нет»): число на вкладке уменьшается. */
    resolved() { badge = Math.max(0, badge - 1); showBadge(); },
    /** Перерисовать без экрана «Загружаю…»: данные уже на месте, обновляется только список. */
    async refresh() {
      try {
        const data = await load();
        if (signal.aborted) return false;
        badge = data.badge;
        showBadge();
        mount(box, view(data, env));
        return true;
      } catch (error) {
        if (!(error && error.name === "AbortError")) toast(errorText(error));
        return false;
      }
    },
  };
  return mountAsync(box, load, (data) => {
    badge = data.badge;
    showBadge();
    return view(data, env);
  }, { signal });
}
