// «Досье» (S6–S10, S14; П:986–1056): шапка, резюме, места работы, навыки, следы в сети, баллы с разбором, лента, решения.
// Данные — GET /api/person/{id}/dossier. Разбор моделью приходит отдельно (POST …/explain) и не задерживает досье.
// Всё из ответа выводится только текстом (h(), textContent).
import { h, mount } from "../dom.js";
import { absoluteDay, dateTimeLabel, moneyText, statusTitle, yearsText } from "../format.js";
import { chip, face, personName, scoreBadge } from "../people.js";
import { hashFor } from "../router.js";
import { downloadBlob, emptyCard, errorLine, mountAsync, sheet, toast } from "../ui.js";
import {
  bestScore, confidenceInfo, decisionTitle, explainTargets, explanationBlocks, featureRows, headerTitle, periodText,
  personState, socialInfo, timeline,
} from "./person-model.js";

const TEMPLATE_TIP = "Модель недоступна или кончился пакет";

// ------------------------------------------------------------------ мелкие части

const title = (text) => h("h2.t1", text);
const card = (heading, ...content) => h("section.card", title(heading), content);
const hint = (text) => h("div.t2", { style: "margin-top:8px" }, text);

/** Таблица «название — значение»; строки без значения пропускаются. */
function kv(rows) {
  return h("table.kv", rows.filter(([, v]) => v !== null && v !== undefined && v !== "" && v !== false)
    .map(([k, v]) => h("tr", h("td", k), h("td", v))));
}

/** Пометка «по шаблону» с подсказкой при наведении и фокусе (S8). */
function templateChip() {
  return h("span.chip.tip", { tabindex: 0, "data-tip": TEMPLATE_TIP, title: TEMPLATE_TIP },
    "разбор по шаблону", h("span.sr-only", ` — ${TEMPLATE_TIP}`));
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

// ------------------------------------------------------------------ шапка

function headerCard(data, env) {
  const { person, candidate, observations: obs, matches } = data;
  const state = personState(person, candidate);
  const first = obs[0] || {};
  const status = errorLine();
  const canTake = env.session.can("person.take");
  const watched = person.watch === "watched";
  const act = async (request, done) => {
    status.clear();
    try {
      await request();
      done();
      await env.refresh();
    } catch (error) {
      if (error && error.name === "AbortError") return;
      status.show(error);
    }
  };
  const id = encodeURIComponent(person.person_id);
  const buttons = [
    canTake && !candidate ? h("button.b.fill", { type: "button", onClick: () =>
      act(() => env.api.post(`/api/person/${id}/take`, {}, { signal: env.signal }), () => toast("Взят в работу")) }, "В работу") : null,
    canTake && person.watch !== "dropped" ? h("button.b", { type: "button", "aria-pressed": watched, onClick: () =>
      act(() => env.api.post(`/api/person/${id}/watch`, { on: !watched }, { signal: env.signal }),
        () => toast(watched ? "Особое наблюдение снято" : "Веду особо: сигнал придёт на любое изменение резюме")) },
    watched ? "Не вести особо" : "Вести особо") : null,
    env.session.can("dossier.export") ? h("button.b", { type: "button", onClick: () => exportSheet(person, env) },
      "Выгрузить в Word или PDF") : null,
  ].filter(Boolean);
  const chips = [
    chip(state.text, state.tone),
    first.open_for_search === true ? chip("открыл резюме для поиска", "b") : null,
    first.relocation_ready === true ? chip("готов к переезду", "o") : null,
    first.is_gone ? chip("резюме снято", "r") : null,
  ].filter(Boolean);
  const sources = [...new Set(obs.map((o) => o.source_title).filter(Boolean))].join(", ");
  return h("section.card",
    h("div.row", scoreBadge(bestScore(person, matches)), face(person),
      h("div.grow", h("h2.t1", headerTitle(person)),
        h("div.t2", [sources ? `источник: ${sources}` : null, `карточка ${person.person_id}`].filter(Boolean).join(" · ")),
        h("div.chips", chips))),
    buttons.length ? h("div.btns", buttons) : null, status.el);
}

// ------------------------------------------------------------------ левая колонка

function resumeCard(o, index, data) {
  const { candidate } = data;
  const lastJob = (o.experience || [])[0];
  const last = o.employer_current || (lastJob ? [lastJob.company, lastJob.position].filter(Boolean).join(", ") : "");
  const tenure = o.avg_tenure_months ? `в среднем ${o.avg_tenure_months} мес. на месте` : "";
  const contacts = index === 0
    ? (candidate ? (candidate.contact_opened ? "открыты, журнал доступа ведётся" : "не открыты")
      : chip("откроются после «взять в работу»"))
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
      ["Источник", [o.source_title, o.url ? h("a.link", { href: o.url, target: "_blank" }, "резюме на площадке") : null]
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

function socialCard(social) {
  const rows = social.map((s) => {
    const info = socialInfo(s.kind);
    const conf = confidenceInfo(s.confidence);
    const meta = [s.handle, s.last_activity ? `последняя активность: ${absoluteDay(s.last_activity)}` : null].filter(Boolean).join(" · ");
    return h("div.soc", h("div.ico", { class: info.ico, "aria-hidden": "true" }, info.abbr),
      h("div", h("div", { style: "font-weight:600" }, info.title, " ", chip(conf.text, conf.tone)),
        s.summary ? h("div.t2", s.summary) : null,
        meta ? h("div.t2", meta) : null,
        s.url ? h("div.t2", h("a.link", { href: s.url, target: "_blank" }, "открыть профиль")) : null));
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

function matchCard(match, slot) {
  const rows = featureRows(match.features);
  return h("section.card",
    h("div.row", scoreBadge(match.score),
      h("div.grow", h("h2.t1", "Из чего сложился балл"),
        h("div.t2", `«${match.role_title}» · версия профиля ${match.profile_version}`))),
    h("div", { style: "margin-top:12px" }, rows.map((r) =>
      h("div.feat", h("div.lbl", h("span", r.label), h("span.num", `${r.points} / ${r.max}`)),
        h("div.bar", { class: r.low ? "low" : null }, h("i", { style: `width:${r.pct}%` })),
        r.detail ? h("div.detail", r.detail) : null))),
    slot.el,
    h("div.t2", { style: "margin-top:8px" }, `Веса признаков задаются в профиле должности «${match.role_title}» — экран «База должностей», правит любой рекрутер.`));
}

function candidateCard(candidate) {
  return card("Кандидат", kv([
    ["Статус", statusTitle(candidate.status)],
    ["В работе с", absoluteDay(candidate.created_at)],
    ["Последний контакт", candidate.last_contact_at ? absoluteDay(candidate.last_contact_at) : "ещё не было"],
    ["Контакты", candidate.contact_opened ? "открыты, журнал доступа ведётся" : "не открыты"],
  ]));
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
  const matchCards = data.matches.length
    ? data.matches.map((m) => matchCard(m, slots.get(m.role_profile_id)))
    : [card("Из чего сложился балл", h("div.t2", "Оценок пока нет: по утверждённым профилям человек ещё не оценён."))];
  const first = data.observations[0];
  return {
    slots,
    nodes: [
      headerCard(data, env),
      h("div.split", { style: "margin-top:14px" },
        h("div.stack", data.observations.length ? data.observations.map((o, i) => resumeCard(o, i, data))
          : card("Резюме", h("div.t2", "Резюме площадок для этого человека нет.")),
        experienceCard(first), skillsCard(data.observations), socialCard(data.social)),
        h("div.stack", matchCards, data.candidate ? candidateCard(data.candidate) : null, timelineCard(data), decisionsCard(data))),
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
    api, signal, session: ctx.session, explained, print: () => window.print(),
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
