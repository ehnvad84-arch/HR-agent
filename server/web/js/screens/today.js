// «Сегодня» (S4, S5, S36, S37; П:305–320): четыре зоны — сигналы, ждут подтверждения, зависли, сегодня и завтра.
// Данные — GET /api/today. Всё из ответа выводится только текстом (h(), textContent).
import { actBtn, actionCard, perform } from "../cards.js";
import { h, mount } from "../dom.js";
import { actionTitle, alertReason, dayLabel, capitalize, interviewTitle, silentText, timeLabel, whenLabel } from "../format.js";
import { blockedSheet, contactSheet, quietDecline, startContact } from "../contact-ui.js";
import { loadResumesButton } from "../hh-load.js";
import { actionOutcome, contactPlan, isLetter } from "../contacts-model.js";
import { TOASTS, resultText, takenText } from "../decisions-model.js";
import { openCommentSheet, openDecisionSheet, takePerson } from "../decisions.js";
import { declineSheet, draftSheet } from "../letter-ui.js";
import { handoffControl, statusBadge } from "../handoff.js";
import { DEFAULT_MIN as HANDOFF_MIN, NO_INBOX, REJECT_HINT, REJECT_REASONS, inboxLines, takenToast, zoneTitle as inboxTitle } from "../handoff-model.js";
import { isCabinet } from "../nav-model.js";
import { chip, face, myScoreChip, personHref, personName, scoreBadge, summaryBlock } from "../people.js";
import { hashFor } from "../router.js";
import { emptyCard, errorLine, errorText, field, mountAsync, sheet, toast } from "../ui.js";
import { normalizeToday } from "./today-model.js";
import {
  EMPTY_SIGNALS, EMPTY_VACANCIES, NO_HANDOFFS, customerLine, hrLine, managerBadge, matchingLabel, normalizeManagerToday, vacancyMeta, zoneTitles,
} from "./manager-model.js";

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

/**
 * Карточка в блоке «Передали руководители» (S11): фото, балл по вакансии, кто и когда передал, комментарий цитатой.
 * «Взять в работу» — человек встаёт в воронку этой вакансии, контакт открывается отдельной кнопкой с подтверждением;
 * «Отклонить» — общее окно комментария с порогом передачи, причину увидит руководитель.
 */
function inboxCard(item, env) {
  const id = encodeURIComponent(item.id);
  const after = h("div.btns", { hidden: true });
  const take = () => perform(card, () => env.api.post(`/api/handoff/${id}/take`, {}, { signal: env.signal }), (reply) => {
    card.finish(`${takenToast(item)}. Контакт не открыт: он откроется по кнопке, с подтверждением`);
    env.resolved();
    toast(takenToast(item));
    if (env.session.can("contact.open") && reply && reply.candidate_id) {
      const status = errorLine();
      const open = h("button.b.fill", { type: "button", "data-tip": "Открыть контакт", onClick: async () => {
        status.clear();
        open.disabled = true;
        try {
          const result = await startContact({ api: env.api, signal: env.signal, candidateId: reply.candidate_id, person: item });
          if (result) {
            toast(result.opened ? "Контакт открыт, запись в журнале доступа" : "Контакт уже был открыт");
            open.textContent = "Контакт открыт";
            return;
          }
        } catch (error) {
          if (error && error.name === "AbortError") return;
          status.show(error);
        }
        open.disabled = false;
      } }, "Открыть контакт");
      mount(after, open, status.el);
      after.hidden = false;
    }
  });
  const reject = async () => {
    const result = await openCommentSheet({
      title: `Отклонить: ${personName(item)}`, hint: REJECT_HINT, reasons: REJECT_REASONS, min: HANDOFF_MIN, saveLabel: "Отклонить передачу",
      placeholder: "Что не подошло по этой вакансии. Руководитель увидит ваш ответ и поймёт, кого предлагать дальше.",
      send: (text) => env.api.post(`/api/handoff/${id}/reject`, { reason: text }, { signal: env.signal }),
    });
    if (!result.saved) return;
    card.finish("Отклонено, причину увидит руководитель");
    env.resolved();
    toast("Отклонено, причина отправлена руководителю");
  };
  const card = actionCard(({ btns, status }) =>
    h("div.row.sigrow", scoreBadge(item.score), face(item, { size: "lg" }),
      h("div.grow", h("div.t1", h("a", { href: personHref(item.person_id) }, personName(item))),
        inboxLines(item, { seeAll: env.session.can("handoff.all") }).map((line) => h("div.t2", line)),
        item.comment ? h("blockquote.quote", item.comment) : null,
        btns, status)),
  [
    actBtn("fill", "Взять в работу", take),
    actBtn("danger", "Отклонить", reject, "Отклонить передачу"),
    h("button.b.tint", { type: "button", onClick: () => env.go("person", { id: item.person_id }) }, "Открыть досье"),
  ]);
  card.el.append(after);
  return card.el;
}

function zone(num, tone, cls, title, cards, emptyText) {
  const headId = `today-zone-${num}`;
  return h("section", { "aria-labelledby": headId },
    h("h2.sechead", { id: headId }, h("span.dot", { class: tone }, num), title),
    h("div.zone", { class: cls }, h("div.stack", cards.length ? cards : emptyCard(emptyText))));
}

// ------------------------------------------------------------------ экран

/** «Загрузить резюме HH.ru» над блоками — только с правом collect.run; после запуска ход виден в полосе под вкладками. */
function loadBar(env) {
  const button = loadResumesButton({ api: env.api, signal: env.signal, session: env.session, variant: "fill" });
  return button ? h("div.loadbar", button, h("span.hint", "Скачать полные резюме с hh вручную, в пределах дневного лимита просмотров")) : null;
}

export function view(data, env) {
  if (data.empty) {
    return [loadBar(env), h("div.card.empty", h("div.t1", "Сегодня всё разобрано"),
      h("div.btns", h("a.link", { href: hashFor("base") }, "Открыть «Базу»")))];
  }
  const cardEnv = { ...env, followUpQueued: data.followUpQueued };
  // блок передач — первый и только у тех, кому они адресованы; остальные блоки тогда нумеруются 2–5
  const inbox = env.session.can("handoff.review");
  const num = (n) => String(n + (inbox ? 1 : 0));
  return [
    loadBar(env),
    inbox ? zone("1", "v", "z5", inboxTitle(), data.handoffs.map((x) => inboxCard(x, cardEnv)), NO_INBOX) : null,
    zone(num(1), "g", "z1", "Обратите внимание — новые и изменившиеся люди", data.signals.map((r) => signalCard(r, cardEnv)),
      "Новых сигналов нет"),
    zone(num(2), "o", "z2", "Ждут подтверждения — агент подготовил, решаете вы", data.actions.map((r) => approvalCard(r, cardEnv)), "Ничего не ждёт"),
    zone(num(3), "r", "z3", "Зависли — давно без движения", data.stalled.map((r) => stalledCard(r, cardEnv)), "Ничего не ждёт"),
    zone(num(4), "b", "z4", "Сегодня и завтра", data.meetings.map(meetingCard), "Встреч на сегодня и завтра нет"),
  ];
}

// ------------------------------------------------------------------ «Сегодня» руководителя (S6): без решений по людям, контактов и загрузки резюме

function managerSignalCard(row, env) {
  const reason = alertReason(row.reason);
  const card = actionCard(({ btns }) =>
    h("div.row.sigrow", h("div", { style: "display:grid;gap:6px;justify-items:center" }, scoreBadge(row.score), myScoreChip(row)), face(row, { size: "lg" }),
      h("div.grow", h("div.t1", h("a", { href: personHref(row.person_id) }, personName(row))),
        h("div.t2", [row.role_title, row.city].filter(Boolean).join(" · ")),
        h("div.t2", { style: "color:var(--ink2)" }, row.headline),
        h("div.chips", chip(reason.title, reason.tone)),
        btns),
      summaryBlock(row.summary)),
  [
    handoffControl({ api: env.api, signal: env.signal, session: env.session, person: row, existing: env.handed.get(row.person_id) || null }),
    h("button.b.tint", { type: "button", onClick: () => env.go("person", { id: row.person_id }) }, "Досье"),
  ]);
  return card.el;
}

function managerVacancyCard(v, env) {
  return h("div.card", h("div.cardbody", h("div.row",
    h("div.grow", h("div.t1", v.title), h("div.t2", [vacancyMeta(v), v.org_unit].filter(Boolean).join(" · ")),
      h("div.t2", [hrLine(v), customerLine(v)].filter(Boolean).join(" · "))),
    h("div.btns", { style: "margin:0" }, h("a.link", { href: hashFor("funnel", { id: v.id }), "data-tip": "vacancy-matching" }, matchingLabel(v.matching))))));
}

function handoffCard(item) {
  return h("div.card", h("div.cardbody", h("div.namewrap", face(item, { small: true }),
    h("div.t1", h("a", { href: personHref(item.person_id) }, personName(item)))),
  h("div.t2", `Вакансия: ${item.vacancy_title}`), h("div", { style: "margin-top:6px" }, statusBadge(item))));
}

export function managerView(data, env) {
  const titles = zoneTitles();
  return [
    zone("1", "g", "z1", titles.signals, data.signals.map((r) => managerSignalCard(r, env)), EMPTY_SIGNALS),
    h("p", { style: "margin:-6px 6px 10px" }, h("a.link", { href: hashFor("vacancies") }, "Мои вакансии")),
    zone("2", "b", "z4 noicon", titles.vacancies, data.vacancies.map((v) => managerVacancyCard(v, env)), EMPTY_VACANCIES),
    zone("3", "o", "z2 noicon", titles.handoffs, data.handoffs.map(handoffCard), NO_HANDOFFS),
  ];
}

function renderManager(box, ctx) {
  const { api, signal, setBadge } = ctx;
  const env = { api, signal, go: ctx.go, session: ctx.session, handed: new Map() };
  const load = async () => {
    const data = normalizeManagerToday(await api.get("/api/today", { signal }));
    for (const item of data.handoffs) env.handed.set(item.person_id, item);   // у кого уже есть статус — бейдж вместо кнопки
    return data;
  };
  return mountAsync(box, load, (data) => {
    setBadge("today", managerBadge(data.badge));
    return managerView(data, env);
  }, { signal });
}

export function render(box, ctx) {
  if (isCabinet(ctx.session.user)) return renderManager(box, ctx);
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
