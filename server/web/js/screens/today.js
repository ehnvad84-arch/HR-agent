// «Сегодня» (S4, S5, S36, S37; П:305–320): четыре зоны — сигналы, ждут подтверждения, зависли, сегодня и завтра.
// Данные — GET /api/today. Всё из ответа выводится только текстом (h(), textContent).
import { h, mount } from "../dom.js";
import { actionTitle, alertReason, dayLabel, capitalize, interviewTitle, silentText, timeLabel, whenLabel } from "../format.js";
import { TOASTS, resultText, takenText } from "../decisions-model.js";
import { openDecisionSheet, takePerson } from "../decisions.js";
import { chip, face, personHref, personName, scoreBadge } from "../people.js";
import { hashFor } from "../router.js";
import { emptyCard, errorLine, errorText, field, mountAsync, sheet, toast } from "../ui.js";
import { normalizeToday } from "./today-model.js";

// ------------------------------------------------------------------ карточка с действиями

/**
 * Карточка с действиями: тело (.cardbody) и под ним итог «✓ что сделано».
 * layout({btns, status}) собирает тело и решает, где стоят кнопки и строка ошибки.
 * Кнопки с data-act блокируются на время запроса и после успеха; «Открыть досье» — переход, не действие.
 */
function actionCard(layout, buttons) {
  const status = errorLine();
  const parts = { btns: h("div.btns", buttons), status: status.el };
  const result = h("p.result", { role: "status", tabindex: -1 });
  const el = h("div.card", h("div.cardbody", layout(parts)), result);
  const acting = () => [...el.querySelectorAll("button[data-act]")];
  return {
    el,
    status,
    busy(on) { acting().forEach((b) => { b.disabled = on; }); },
    /** Итог без изменения карточки: пометка под кнопками. */
    note(text) { result.textContent = `✓ ${text}`; },
    /** Итог действия: карточка гаснет, кнопки действий остаются выключенными, фокус — на итоге. */
    finish(text) {
      acting().forEach((b) => { b.disabled = true; });
      el.classList.add("done");
      result.textContent = `✓ ${text}`;
      result.focus();
    },
  };
}

const actBtn = (cls, text, onClick) => h("button.b", { class: cls, type: "button", dataset: { act: 1 }, onClick }, text);

/** Выполнить запрос от кнопки карточки: ошибка — под кнопками, карточка остаётся как была. */
async function perform(card, request, onDone) {
  card.status.clear();
  card.busy(true);
  try {
    await onDone(await request());
  } catch (error) {
    if (error && error.name === "AbortError") return;
    card.status.show(error);
    card.busy(false);
  }
}

const nameLine = (person) => h("div.namewrap", face(person, { small: true }),
  h("div.t1", h("a", { href: personHref(person.person_id) }, personName(person))));

// ------------------------------------------------------------------ зоны

function signalCard(row, env) {
  const reason = alertReason(row.reason);
  const dossier = () => env.go("person", { id: row.person_id });
  const profiles = row.role_profile_id ? [{ role_profile_id: row.role_profile_id, role_title: row.role_title, score: row.score }] : [];
  /** «Отложить» и «Не наш»: общее окно комментария (200 знаков); после сохранения карточка гаснет. */
  const decide = (kind) => async () => {
    const result = await openDecisionSheet({ api: env.api, signal: env.signal, kind, person: row, matches: profiles });
    if (!result.saved) return;
    card.finish(resultText(kind, result.length));
    env.resolved();
    toast(TOASTS[kind]);
  };
  const card = actionCard(({ btns, status }) =>
    h("div.row", scoreBadge(row.score),
      h("div.grow", nameLine(row),
        h("div.t2", [row.role_title, row.city].filter(Boolean).join(" · ")),
        h("div.t2", { style: "color:var(--ink2)" }, row.headline),
        h("div.chips", chip(reason.title, reason.tone)),
        btns, status)),
  [
    // вакансию определяет профиль человека: одна — сразу, несколько — окно выбора, ни одной — подтверждение «в резерв»
    actBtn("fill", "В работу", () => perform(card, () => takePerson({ api: env.api, signal: env.signal, person: row }),
      (result) => {
        if (!result.taken) { card.busy(false); return; }
        card.finish(takenText(result.vacancy));
        env.resolved();
        toast("Взят в работу");
      })),
    actBtn("", "Отложить", decide("postpone")),
    actBtn("danger", "Не наш", decide("reject")),
    h("button.b.tint", { type: "button", onClick: dossier }, "Открыть досье"),
  ]);
  return card.el;
}

function approvalCard(row, env) {
  const title = [actionTitle(row.kind), row.person_id ? personName(row) : null].filter(Boolean).join(" — ");
  const heading = row.person_id
    ? h("div.namewrap", face(row, { small: true }), h("div.t1", h("a", { href: personHref(row.person_id) }, title)))
    : h("div.t1", title);
  // «Одобрить» и «Отклонить» появятся в задаче про письма; здесь — список и переход в досье
  return h("div.card", h("div.cardbody", heading,
    h("div.t2", [row.vacancy_title ? `Вакансия: ${row.vacancy_title}` : null, `подготовлено ${whenLabel(row.proposed_at)}`]
      .filter(Boolean).join(" · ")),
    row.person_id
      ? h("div.btns", h("button.b.tint", { type: "button", onClick: () => env.go("person", { id: row.person_id }) }, "Открыть досье"))
      : null));
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
    api, signal, go: ctx.go,
    /** Сигнал разобран (взят, отложен, «не наш»): число на вкладке уменьшается. */
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
