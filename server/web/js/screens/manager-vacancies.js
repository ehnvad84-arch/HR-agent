// Кабинет руководителя: «Мои вакансии» (список вакансий поддерева) и страница вакансии — слева «Подходящие», справа воронка только для чтения (S7).
// Создать, изменить или закрыть вакансию руководитель не может; контактов, перетаскивания и «Перевести» здесь нет.
// Всё из API выводится только текстом (h(), textContent).
import { h, mount } from "../dom.js";
import { dayText, funnelTotals, stageSummary } from "./funnel-model.js";
import { handoffControl, loadHandoffMap } from "../handoff.js";
import { chip, face, myScoreChip, personHref, personName, scoreBadge } from "../people.js";
import { hashFor } from "../router.js";
import { emptyCard, errorCard, mountAsync } from "../ui.js";
import { EMPTY_VACANCIES, customerLine, hrLine, matchingLabel, vacancyMeta } from "./manager-model.js";

// ------------------------------------------------------------------ «Мои вакансии»

function vacancyCard(v, ctx) {
  const state = v.state && v.state !== "open" ? chip(v.state === "on_hold" ? "на паузе" : String(v.state)) : null;
  return h("div.card", h("div.cardbody", h("div.row",
    h("div.grow", h("div.t1", h("a", { href: hashFor("funnel", { id: v.id }) }, v.title)),
      h("div.t2", [vacancyMeta(v), v.org_unit].filter(Boolean).join(" · ")),
      h("div.t2", hrLine(v)), customerLine(v) ? h("div.t2", customerLine(v)) : null, state ? h("div.chips", state) : null),
    h("div.btns", { style: "margin:0" }, h("button.b.fill", { type: "button", onClick: () => ctx.go("funnel", { id: v.id }) }, "Подходящие и воронка")))));
}

export function renderList(box, ctx) {
  const { api, signal } = ctx;
  return mountAsync(box, () => api.get("/api/vacancies", { signal }), (data) => {
    const open = data.open || [];
    ctx.setBadge("vacancies", open.length || "");
    return [h("h2.sechead", "Открытые вакансии вашего подразделения"),
      h("div.stack", open.length ? open.map((v) => vacancyCard(v, ctx)) : emptyCard(EMPTY_VACANCIES))];
  }, { signal });
}

// ------------------------------------------------------------------ страница вакансии

function suitableRow(row, env) {
  return h("div.li", { style: "cursor:default;align-items:flex-start" },
    face(row, { size: "md" }),
    h("div", { style: "flex:1;min-width:0" }, h("div", { style: "font-weight:600" }, h("a.link", { href: personHref(row.person_id) }, personName(row))),
      h("div.sub", [row.city, row.role_title].filter(Boolean).join(" · ") || "—")),
    h("div", { style: "display:grid;gap:4px;justify-items:center" }, scoreBadge(row.score), myScoreChip(row)),
    handoffControl({ api: env.api, signal: env.signal, session: env.session, person: row, existing: env.handed.get(row.person_id) || null, short: true, variant: "tint" }));
}

function stageColumn(stage, total) {
  const summary = stageSummary(stage, total);
  return h("section.stage", { class: summary.empty ? "empty" : null },
    h("div.top", h("h3.nm", stage.title), h("span.cnt", summary.countText),
      h("span.meter", { "aria-hidden": "true" }, h("i", { style: `width:${summary.pct}%` })), h("span.days", summary.avgText)),
    stage.people.length ? h("div.cards", stage.people.map((p) => h("div.mini",
      h("a.open", { href: personHref(p.person_id), "data-tip": "Открыть досье" }, h("div.namewrap", face(p, { small: true }), h("b", personName(p))), h("span", dayText(p.days))),
      p.note ? h("div.t2", { style: "margin-top:4px" }, p.note) : null))) : null);
}

export function renderPage(box, ctx) {
  const { api, signal, session } = ctx;
  const id = ctx.params.id;
  if (!id) return mount(box, emptyCard("Откройте вакансию из списка", h("a.link", { href: hashFor("vacancies") }, "Мои вакансии")));
  if (!/^\d+$/.test(id)) return mount(box, emptyCard("Не найдено или нет доступа", h("a.link", { href: hashFor("today") }, "К «Сегодня»")));
  const env = { api, signal, session, handed: new Map() };
  const notFound = () => emptyCard("Не найдено или нет доступа", h("a.link", { href: hashFor("today") }, "К «Сегодня»"));

  const load = async () => {
    try {
      const [funnel, lists, handed] = await Promise.all([
        api.get(`/api/vacancy/${encodeURIComponent(id)}/funnel`, { signal }),
        api.get("/api/vacancies", { signal }),
        loadHandoffMap(api, signal),
      ]);
      env.handed = handed;
      let matching = await api.get(`/api/vacancy/${encodeURIComponent(id)}/matching`, { query: { sort: "score", dir: "desc" }, signal });
      // есть личные поправки — подходящие по «моему баллу»
      if ((matching.items || []).some((r) => typeof r.my_score === "number")) {
        matching = await api.get(`/api/vacancy/${encodeURIComponent(id)}/matching`, { query: { sort: "my", dir: "desc" }, signal });
      }
      return { funnel, lists, matching };
    } catch (error) {
      if (error && (error.status === 404 || error.status === 403)) return null;   // чужую вакансию не отличаем от несуществующей
      throw error;
    }
  };

  return mountAsync(box, load, (data) => {
    if (!data) return notFound();
    const { funnel, lists, matching } = data;
    const vacancy = funnel.vacancy;
    const item = (lists.open || []).find((v) => v.id === vacancy.id) || vacancy;
    const total = funnelTotals(funnel.stages);
    const rows = matching.items || [];
    return [
      h("p", { style: "margin:0 6px 10px" }, h("a.link", { href: hashFor("vacancies") }, "← Мои вакансии")),
      h("div.card", { style: "margin-bottom:14px" }, h("h2.t1", vacancy.title),
        h("div.t2", [vacancyMeta(item), item.org_unit].filter(Boolean).join(" · ")),
        h("div.t2", [hrLine(item), customerLine(item)].filter(Boolean).join(" · "))),
      h("div.split",
        h("div", h("h2.sechead", "Подходящие"), rows.length ? h("div.list", rows.map((r) => suitableRow(r, env))) : emptyCard("Подходящих пока нет: агент копит людей по этой должности")),
        h("div", h("h2.sechead", "Воронка"), h("p.hint", { style: "margin:0 6px 8px" }, "Этапы и заметки отдела персонала. Контактов здесь нет, переводит по этапам отдел персонала."),
          funnel.stages.map((s) => stageColumn(s, total)),
          total === 0 ? h("p.hint", { style: "margin:10px 6px" }, "В воронке пока никого: отдел персонала берёт в работу тех, кого вы передали.") : null)),
    ];
  }, { signal });
}
