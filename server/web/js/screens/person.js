// «Досье» (S6–S10, S14; П:986–1056): шапка, резюме, места работы, навыки, следы в сети, баллы с разбором, лента, решения.
// Данные — GET /api/person/{id}/dossier. Разбор моделью приходит отдельно (POST …/explain) и не задерживает досье.
// Всё из ответа выводится только текстом (h(), textContent).
import { h, mount } from "../dom.js";
import { COPY_FALLBACK, copyText, selectText } from "../clipboard.js";
import { fetchContacts, startContact } from "../contact-ui.js";
import { actionOutcome, contactKindTitle, draftKind } from "../contacts-model.js";
import { TOASTS } from "../decisions-model.js";
import { openDecisionSheet, takePerson } from "../decisions.js";
import { absoluteDay, dateTimeLabel, moneyText, statusTitle, yearsText } from "../format.js";
import { handoffControl, statusBadge } from "../handoff.js";
import { handoffsOfPerson } from "../handoff-model.js";
import { draftSheet } from "../letter-ui.js";
import { isCabinet } from "../nav-model.js";
import { chip, face, personName, scoreBadge, templateChip } from "../people.js";
import { hashFor } from "../router.js";
import { announce, downloadBlob, emptyCard, errorLine, mountAsync, sheet, toast } from "../ui.js";
import {
  bestScore, confidenceInfo, decisionTitle, explainTargets, explanationBlocks, featureRows, headerTitle, periodText,
  personState, socialInfo, timeline,
} from "./person-model.js";

// ------------------------------------------------------------------ мелкие части

const title = (text) => h("h2.t1", text);
const card = (heading, ...content) => h("section.card", title(heading), content);
const hint = (text) => h("div.t2", { style: "margin-top:8px" }, text);

/** Таблица «название — значение»; строки без значения пропускаются. */
function kv(rows) {
  return h("table.kv", rows.filter(([, v]) => v !== null && v !== undefined && v !== "" && v !== false)
    .map(([k, v]) => h("tr", h("td", k), h("td", v))));
}

function explanationNodes(text) {
  return explanationBlocks(text).map((b) =>
    h("div.t2", { style: "margin-top:6px" }, b.label ? h("b", `${b.label}: `) : null, b.text));
}

// ------------------------------------------------------------------ выгрузка (S10)

function exportSheet(person, env) {
  const win = sheet({ title: "Выгрузить досье", width: "420px",
    hint: `${personName(person)} — полное досье из базы знаний: резюме, оценка, профили в сети, хронология изменений. Выберите формат.` });
  const status = errorLine();
  const id = encodeURIComponent(person.person_id);
  const run = async (job) => {
    status.clear();
    const buttons = [...win.el.querySelectorAll("button")];
    buttons.forEach((b) => { b.disabled = true; });
    try {
      await job();
    } catch (error) {
      if (error && error.name === "AbortError") return;
      status.show(error);
      buttons.forEach((b) => { b.disabled = false; });
    }
  };
  const word = h("button.b.blue", { type: "button", onClick: () => run(async () => {
    const { blob, filename } = await env.api.get(`/api/person/${id}/export`, { query: { f: "docx" }, as: "blob", signal: env.signal });
    downloadBlob(blob, filename || `dossier-${person.person_id}.docx`);
    toast("Word-файл сформирован, выгрузка записана в журнал доступа");
    win.close("word");
  }) }, "Word (.docx)");
  const pdf = h("button.b.fill", { type: "button", onClick: () => run(async () => {
    await env.api.get(`/api/person/${id}/export`, { query: { f: "pdf" }, signal: env.signal });   // запись в журнал; сам PDF печатает браузер
    win.close("pdf");
  }) }, "PDF через печать");
  win.body.append(h("div.btns", word, pdf, h("button.b", { type: "button", onClick: () => win.close("cancel") }, "Отмена")),
    status.el, h("div.hint", { style: "margin-top:10px" }, "Каждая выгрузка пишется в журнал доступа: кто, когда и по кому."));
  win.show(word);
  win.closed.then((outcome) => {
    if (outcome === "pdf") requestAnimationFrame(() => env.print());   // окно уже закрыто: в печать оно не попадёт
  });
}

// ------------------------------------------------------------------ письмо кандидату (S18)

/** «Написать»: агент готовит черновик (модель или шаблон), человек правит, копирует и одобряет в окне. */
function writeButton(data, env, act) {
  const { person, candidate } = data;
  const kind = draftKind(candidate.status);
  const button = h("button.b.tint", { type: "button", onClick: async () => {
    button.disabled = true;
    button.textContent = "Готовлю черновик…";
    try {
      await act(async () => {
        const draft = await env.api.post(`/api/candidate/${encodeURIComponent(candidate.id)}/draft`, { kind }, { signal: env.signal });
        return draftSheet({ api: env.api, signal: env.signal, person,
          action: { action_id: draft.action_id, kind, text: draft.text, by: draft.by } });
      }, (outcome) => actionOutcome(outcome, kind).toast);
    } finally {
      button.disabled = false;
      button.textContent = "Написать";
    }
  } }, "Написать");
  return button;
}

// ------------------------------------------------------------------ шапка

function headerCard(data, env) {
  const { person, candidate, observations: obs, matches } = data;
  const state = personState(person, candidate);
  const first = obs[0] || {};
  const status = errorLine();
  const canTake = env.session.can("person.take");
  const watched = person.watch === "watched";
  /** Действие из шапки: ошибка — под кнопками; false от request — человек отменил окно; успех — уведомление и новое досье. */
  const act = async (request, doneText) => {
    status.clear();
    try {
      const result = await request();
      if (result === false) return;
      toast(typeof doneText === "function" ? doneText(result) : doneText);
      await env.refresh();
    } catch (error) {
      if (error && error.name === "AbortError") return;
      status.show(error);
    }
  };
  const id = encodeURIComponent(person.person_id);
  const decide = (kind) => () => act(async () => {
    const result = await openDecisionSheet({ api: env.api, signal: env.signal, kind, person, matches });
    return result.saved ? kind : false;
  }, (k) => TOASTS[k]);
  const open = canTake && !candidate;
  // руководитель (S5): ни решений, ни контактов, ни выгрузки; главная кнопка — «Передать в отдел персонала»
  const cabinet = isCabinet(env.session.user);
  const buttons = cabinet ? [
    handoffControl({ api: env.api, signal: env.signal, session: env.session, person, variant: "fill", onSent: () => env.handoffs && env.handoffs.reload() }),
  ].filter(Boolean) : [
    open ? h("button.b.fill", { type: "button", onClick: () => act(async () => {
      const result = await takePerson({ api: env.api, signal: env.signal, person, matches });
      return result.taken ? result : false;
    }, () => "Взят в работу"), "data-tip": "take" }, "Сохранить") : null,
    open ? h("button.b", { type: "button", onClick: decide("postpone") }, "Временно отложить") : null,
    open && person.watch !== "dropped" ? h("button.b.danger", { type: "button", onClick: decide("reject") }, "Категорически нет") : null,
    canTake && candidate ? writeButton(data, env, act) : null,
    canTake && person.watch !== "dropped" ? h("button.b", { type: "button", "aria-pressed": watched, onClick: () =>
      act(() => env.api.post(`/api/person/${id}/watch`, { on: !watched }, { signal: env.signal }),
        watched ? "Особое наблюдение снято" : "Веду особо: сигнал придёт на любое изменение резюме") },
    watched ? "Не вести особо" : "Вести особо") : null,
    env.session.can("dossier.export") ? h("button.b", { type: "button", onClick: () => exportSheet(person, env) },
      "Выгрузить в Word или PDF") : null,
  ].filter(Boolean);
  const chips = [
    chip(state.text, state.tone, "pill-where"),
    first.open_for_search === true ? chip("открыл резюме для поиска", "b") : null,
    first.relocation_ready === true ? chip("готов к переезду", "o") : null,
    first.is_gone ? chip("резюме снято", "r") : null,
  ].filter(Boolean);
  const sources = [...new Set(obs.map((o) => o.source_title).filter(Boolean))].join(", ");
  return h("section.card",
    h("div.row", scoreBadge(bestScore(person, matches)), face(person),
      h("div.grow", h("h2.t1", headerTitle(person)),
        cabinet ? h("div.t2", "Просмотр записан в журнал доступа") : null,
        h("div.t2", [sources ? `источник: ${sources}` : null, `карточка ${person.person_id}`].filter(Boolean).join(" · ")),
        h("div.chips", chips))),
    buttons.length ? h("div.btns", buttons) : null, status.el);
}

// ------------------------------------------------------------------ левая колонка

function resumeCard(o, index, data, cabinet = false) {
  const { candidate } = data;
  const lastJob = (o.experience || [])[0];
  const last = o.employer_current || (lastJob ? [lastJob.company, lastJob.position].filter(Boolean).join(", ") : "");
  const tenure = o.avg_tenure_months ? `в среднем ${o.avg_tenure_months} мес. на месте` : "";
  const contacts = index === 0 && !cabinet   // у руководителя строки «Контакты» нет совсем
    ? (candidate ? (candidate.contact_opened ? "открыты, журнал доступа ведётся" : "не открыты")
      : chip("откроются после «Сохранить»"))
    : null;
  return h("section.card", title(data.observations.length > 1 ? `Резюме — ${o.source_title}` : "Резюме"),
    kv([
      ["Желаемая должность", o.title],
      ["Город", [o.city, o.relocation_ready === true ? "готов к переезду" : null].filter(Boolean).join(", ")],
      ["Опыт", [yearsText(o.exp_years), tenure].filter(Boolean).join(", ")],
      ["Последнее место", last],
      ["Образование", o.education],
      ["Ожидания", o.salary_expect ? moneyText(o.salary_expect) : "не указаны"],
      ["Поиск работы", o.open_for_search === true ? "открыт" : o.open_for_search === false ? "не ищет" : null],
      ["Источник", [o.source_title, o.url && !cabinet ? h("a.link", { href: o.url, target: "_blank" }, "резюме на площадке") : null]
        .filter(Boolean).flatMap((x, i) => (i ? [" · ", x] : [x]))],
      ["В поле зрения", `с ${absoluteDay(o.first_seen)}, видели ${absoluteDay(o.last_seen)}`],
      ["Резюме", o.is_gone ? chip("снято с площадки", "r") : null],
      ["Контакты", contacts],
    ]),
    o.about ? h("details", { style: "margin-top:8px" }, h("summary.link", "О себе"),
      h("div.t2", { style: "white-space:pre-wrap;margin-top:6px" }, o.about)) : null);
}

function experienceCard(o) {
  if (!o || !(o.experience || []).length) return null;
  return card("Места работы", o.experience.map((e) =>
    h("div.exp", h("div", { style: "font-weight:600" }, [e.position, e.company].filter(Boolean).join(" — ") || "Место работы"),
      h("div.t2", periodText(e.start, e.end)),
      e.description ? h("details", h("summary.link", "Описание"),
        h("div.t2", { style: "white-space:pre-wrap;margin-top:4px" }, e.description)) : null)));
}

function skillsCard(obs) {
  const skills = [...new Set(obs.flatMap((o) => o.skills || []))];
  return skills.length ? card("Навыки", h("div.chips", { style: "margin-top:0" }, skills.map((s) => chip(s)))) : null;
}

function socialCard(social, env) {
  const canConfirm = env.session.can("person.take");
  const rows = social.map((s) => {
    const info = socialInfo(s.kind);
    const conf = confidenceInfo(s.confidence);
    const meta = [s.handle, s.last_activity ? `последняя активность: ${absoluteDay(s.last_activity)}` : null].filter(Boolean).join(" · ");
    // S15: вероятный профиль подтверждает рекрутер; 409 (профиль отклонён) — текст ответа под кнопкой
    const status = errorLine();
    const confirm = h("button.b.tint", { type: "button", onClick: async () => {
      status.clear();
      confirm.disabled = true;
      try {
        await env.api.post(`/api/social/${encodeURIComponent(s.id)}/confirm`, {}, { signal: env.signal });
        toast("Профиль подтверждён: теперь он учитывается в разборе");
        await env.refresh();
      } catch (error) {
        if (error && error.name === "AbortError") return;
        status.show(error);
        confirm.disabled = false;
      }
    } }, "Подтвердить");
    return h("div.soc", h("div.ico", { class: info.ico, "aria-hidden": "true" }, info.abbr),
      h("div", h("div", { style: "font-weight:600" }, info.title, " ", chip(conf.text, conf.tone)),
        s.summary ? h("div.t2", s.summary) : null,
        meta ? h("div.t2", meta) : null,
        s.url ? h("div.t2", h("a.link", { href: s.url, target: "_blank" }, "открыть профиль")) : null,
        canConfirm && s.confidence === "probable" ? h("div.btns", { style: "margin-top:8px" }, confirm) : null,
        status.el));
  });
  return card("Соцсети и профессиональные следы",
    rows.length ? rows : h("div.t2", "Профессиональных следов в открытых источниках пока не найдено. Агент повторит поиск при следующем изменении резюме."),
    hint("Ищет агент по имени, нику, почте и работодателю. Вероятное совпадение подтверждает рекрутер: однофамилец в досье хуже пустого поля."));
}

// ------------------------------------------------------------------ правая колонка

/** Блок разбора одного профиля: текст шаблона, пометка или «Агент пишет разбор…». Возвращает {el, set(reply)}. */
function explainSlot(match, cached) {
  const el = h("div.explain", { "aria-live": "polite" });
  const set = (text, by) => {
    el.removeAttribute("aria-busy");
    mount(el, explanationNodes(text), by === "template" ? h("div", { style: "margin-top:8px" }, templateChip()) : null);
  };
  const pending = () => {
    el.setAttribute("aria-busy", "true");
    mount(el, h("div.t2", "Агент пишет разбор…"));
  };
  if (cached) set(cached.text, cached.by);
  else if (match.model === "template") pending();
  else set(match.explanation, "model");
  return { el, set };
}

function matchCard(match, slot, cabinet = false) {
  const rows = featureRows(match.features);
  return h("section.card",
    h("div.row", scoreBadge(match.score),
      h("div.grow", h("h2.t1", "Из чего сложился балл"),
        h("div.t2", `«${match.role_title}» · версия профиля ${match.profile_version}`),
        typeof match.my_score === "number" ? h("div.chips", chip(`мой ${match.my_score}`, "b")) : null)),
    h("div", { style: "margin-top:12px" }, rows.map((r) =>
      h("div.feat", h("div.lbl", h("span", r.label), h("span.num", `${r.points} / ${r.max}`)),
        h("div.bar", { class: r.low ? "low" : null }, h("i", { style: `width:${r.pct}%` })),
        r.detail ? h("div.detail", r.detail) : null))),
    slot.el,
    h("div.t2", { style: "margin-top:8px" }, cabinet
      ? `Веса признаков задаются в профиле должности «${match.role_title}»; свои поправки вы вносите в «Обучении агента».`
      : `Веса признаков задаются в профиле должности «${match.role_title}» — экран «База должностей», правит любой рекрутер.`));
}

/** Строка контакта: значение текстом и «Скопировать»; без буфера значение выделяется и звучит «Нажмите Ctrl+C». */
function contactRow(contact) {
  const value = h("span.cval", contact.value);
  const note = h("span.hint", { role: "status" });
  const button = h("button.b", { type: "button", onClick: async () => {
    if (await copyText(contact.value)) {
      note.textContent = "";
      button.textContent = "Скопировано";
      announce("Скопировано");
      setTimeout(() => { button.textContent = "Скопировать"; }, 2000);
    } else {
      selectText(value);
      note.textContent = COPY_FALLBACK;
      announce(COPY_FALLBACK);
    }
  } }, "Скопировать");
  return h("div.contactrow", h("div", h("div.t2", `${contactKindTitle(contact.kind)}${contact.is_primary ? " · основной" : ""}`), value),
    button, note);
}

/**
 * Блок «Контакты» (S16): значения только здесь и только текстом; в карточках, таблице и воронке контактов нет.
 * «Открыть контакт» — платное действие с окном подтверждения; «Показать контакты» читает уже открытые (запись в журнал доступа).
 * Блок не печатается и не уходит в выгрузку.
 */
function contactsCard(data, env) {
  const { candidate, person } = data;
  const status = errorLine();
  const shown = env.contacts;
  const act = async (job) => {
    status.clear();
    try {
      if (await job()) await env.refresh();
    } catch (error) {
      if (error && error.name === "AbortError") return;
      status.show(error);
    }
  };
  let content;
  let button;
  if (shown) {
    content = shown.length ? shown.map(contactRow) : h("div.t2", "Контактов нет: hh не отдал ни телефона, ни почты.");
    button = h("button.b", { type: "button", onClick: () => act(async () => { env.contacts = null; return true; }) }, "Скрыть контакты");
  } else {
    content = h("div.t2", candidate.contact_opened
      ? "Контакты открыты. Каждый показ записывается в журнал доступа."
      : "Контакты не открыты. Открытие списывает один контакт hh; перед этим будет окно подтверждения.");
    button = h("button.b.fill", { type: "button", onClick: async () => {
      button.disabled = true;
      try {
        await act(async () => {
          if (candidate.contact_opened) {
            env.contacts = await fetchContacts(env.api, candidate.id, env.signal);
            return true;
          }
          const result = await startContact({ api: env.api, signal: env.signal, candidateId: candidate.id, person });
          if (!result) return false;
          env.contacts = result.contacts;
          toast(result.opened ? "Контакт открыт, запись в журнале доступа" : "Контакт уже был открыт");
          return true;
        });
      } finally {
        button.disabled = false;
      }
    } }, candidate.contact_opened ? "Показать контакты" : "Открыть контакт");
  }
  return h("section.card.noprint", title("Контакты"), content, h("div.btns", button), status.el);
}

function candidateCard(candidate) {
  return card("Кандидат", kv([
    ["Статус", statusTitle(candidate.status)],
    ["В работе с", absoluteDay(candidate.created_at)],
    ["Последний контакт", candidate.last_contact_at ? absoluteDay(candidate.last_contact_at) : "ещё не было"],
    ["Контакты", candidate.contact_opened ? "открыты, журнал доступа ведётся" : "не открыты"],
  ]));
}

/** Блок «Передачи» в досье руководителя (S10): что и кому передано, статус словом, причина отказа текстом. Возвращает {el, reload}. */
function handoffsCard(env, person) {
  const body = h("div", { "aria-live": "polite" }, h("p.loading", "Загружаю передачи…"));
  const draw = (items) => mount(body, items.length
    ? h("div.tl", items.map((x) => h("div.ev", h("time", { datetime: x.created_at }, dateTimeLabel(x.created_at)),
      h("div", { style: "font-weight:600" }, x.vacancy_title || "Вакансия"),
      h("div", { style: "margin-top:4px" }, statusBadge(x)),
      x.recruiter ? h("div.t2", `Ответственный HR: ${x.recruiter}`) : null,
      x.comment ? h("div.t2.lines", `«${x.comment}»`) : null)))
    : h("div.t2", "Вы ещё не передавали этого человека."));
  const reload = async () => {
    try {
      const reply = await env.api.get("/api/handoffs", { signal: env.signal });
      draw(handoffsOfPerson(reply.items, person.person_id));
    } catch (error) {
      if (error && error.name === "AbortError") return;
      const status = errorLine();
      status.show(error);
      mount(body, status.el);
    }
  };
  reload();
  return { el: card("Передачи", body), reload };
}

function timelineCard(data) {
  const items = timeline(data);
  return card("Что изменилось", items.length
    ? h("div.tl", items.map((i) => h("div.ev", h("time", { datetime: i.iso }, dateTimeLabel(i.iso)),
      h("div", { style: "font-weight:600" }, i.title), i.detail ? h("div.t2.lines", i.detail) : null)))
    : h("div.t2", "Изменений пока не было."));
}

function decisionsCard(data) {
  const byProfile = new Map(data.matches.map((m) => [m.role_profile_id, m.role_title]));
  return card("История решений", data.decisions.length
    ? h("div.tl", data.decisions.map((d) => h("div.ev", h("time", { datetime: d.created_at }, dateTimeLabel(d.created_at)),
      h("div", { style: "font-weight:600" }, `${decisionTitle(d.kind)} — ${d.author}`,
        byProfile.has(d.role_profile_id) ? h("span.t2", ` · по профилю «${byProfile.get(d.role_profile_id)}»`) : null),
      d.comment ? h("div.t2.lines", d.comment) : null)))
    : h("div.t2", "Решений по этому человеку ещё не было."));
}

// ------------------------------------------------------------------ экран

export function view(data, env) {
  const slots = new Map(data.matches.map((m) => [m.role_profile_id, explainSlot(m, env.explained.get(m.role_profile_id))]));
  const cabinet = isCabinet(env.session.user);
  const matchCards = data.matches.length
    ? data.matches.map((m) => matchCard(m, slots.get(m.role_profile_id), cabinet))
    : [card("Из чего сложился балл", h("div.t2", "Оценок пока нет: по утверждённым профилям человек ещё не оценён."))];
  const first = data.observations[0];
  env.handoffs = cabinet ? handoffsCard(env, data.person) : null;
  const header = headerCard(data, env);   // после handoffsCard: кнопка «Передать» обновляет этот блок
  return {
    slots,
    nodes: [
      header,
      h("div.split", { style: "margin-top:14px" },
        h("div.stack", data.observations.length ? data.observations.map((o, i) => resumeCard(o, i, data, cabinet))
          : card("Резюме", h("div.t2", "Резюме площадок для этого человека нет.")),
        experienceCard(first), skillsCard(data.observations), cabinet ? null : socialCard(data.social, env)),
        cabinet
          ? h("div.stack", matchCards, env.handoffs.el, timelineCard(data))
          : h("div.stack", matchCards, data.candidate ? candidateCard(data.candidate) : null,
            data.candidate && env.session.can("contact.open") ? contactsCard(data, env) : null, timelineCard(data), decisionsCard(data))),
    ],
  };
}

function notFound() {
  return emptyCard("Человек не найден",
    h("a.link", { href: hashFor("today") }, "К «Сегодня»"), h("a.link", { href: hashFor("base") }, "Открыть «Базу»"));
}

export function render(box, ctx) {
  const { api, signal } = ctx;
  const id = ctx.params.id;
  if (!id) {   // вкладка «Досье» без выбранного человека
    return mount(box, emptyCard("Откройте человека из «Сегодня» или «Базы»",
      h("a.link", { href: hashFor("today") }, "«Сегодня»"), h("a.link", { href: hashFor("base") }, "«База»")));
  }
  if (!/^\d+$/.test(id)) return mount(box, notFound());

  // печать в PDF: раскрыть «О себе» и описания мест работы, иначе они не попадут на бумагу
  const openDetails = () => box.querySelectorAll("details").forEach((d) => { d.open = true; });
  window.addEventListener("beforeprint", openDetails);
  signal.addEventListener("abort", () => window.removeEventListener("beforeprint", openDetails), { once: true });

  const explained = new Map();   // role_profile_id → {text, by}: разбор моделью просим один раз за открытие
  let generation = 0;
  const env = {
    api, signal, session: ctx.session, explained, contacts: null, print: () => window.print(),
    async refresh() {
      try {
        const data = await load();
        if (signal.aborted) return false;
        show(data);
        return true;
      } catch (error) {
        if (error && error.name === "AbortError") return false;
        toast(error && error.message ? error.message : "Не удалось обновить досье");
        return false;
      }
    },
  };
  const load = async () => {
    try {
      return await api.get(`/api/person/${encodeURIComponent(id)}/dossier`, { signal });
    } catch (error) {
      if (error && error.status === 404) return null;
      throw error;
    }
  };

  /** Разбор моделью по одному профилю за раз: на сервере модель одна, параллельные запросы получили бы «модель занята». */
  async function explainAll(data, slots) {
    const mine = ++generation;
    for (const match of explainTargets(data.matches)) {
      if (explained.has(match.role_profile_id)) continue;
      let reply;
      try {
        reply = await api.post(`/api/person/${encodeURIComponent(id)}/explain`, { role_profile_id: match.role_profile_id }, { signal });
      } catch (error) {
        if (signal.aborted || (error && error.name === "AbortError")) return;
        reply = { text: match.explanation, by: "template" };   // разбор не пришёл: остаётся шаблон из досье
      }
      explained.set(match.role_profile_id, reply);
      if (mine !== generation) return;
      const slot = slots.get(match.role_profile_id);
      if (slot) slot.set(reply.text, reply.by);
    }
  }

  function show(data) {
    if (!data) {
      mount(box, notFound());
      return;
    }
    const built = view(data, env);
    mount(box, built.nodes);
    explainAll(data, built.slots);
  }

  return mountAsync(box, load, (data) => {
    if (!data) return notFound();
    const built = view(data, env);
    queueMicrotask(() => explainAll(data, built.slots));   // после отрисовки: досье не ждёт модель
    return built.nodes;
  }, { signal });
}
