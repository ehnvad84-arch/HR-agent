// «Воронка» без вакансии (S12, S13): отчёт рекрутера за год — общая воронка и блоки его вакансий, как лист их Excel;
// у руководителя ОП — «Чей отчёт»: «Весь отдел» (воронка отдела и сравнение рекрутеров) или любой рекрутер.
// Данные — GET /api/funnel/recruiter/{uid}?year= и /api/funnel/department?year=; цифры считает сервер, прав на экране не проверяем:
// рекрутер видит только себя (адрес с чужим ?recruiter= игнорируется), отчёт отдела сервер отдаёт только руководителю ОП.
// Всё из API выводится только текстом (h(), textContent).
import { h, mount, uid } from "../dom.js";
import { compareTable } from "../funnel-compare.js";
import { funnelTable } from "../funnel-table.js";
import { hashFor } from "../router.js";
import { emptyCard, mountAsync } from "../ui.js";
import {
  ALL, emptyYearText, overviewQuery, overviewTitle, parseView, recruiterOptions, recruiterTitle, vacancyLine, yearOptions,
} from "./funnel-overview-model.js";
import { hintText, periodText } from "./funnel-report-model.js";

/** Руководитель ОП: report.read вместе со всей базой — как проверяет сервер. */
export const isDepartment = (session) => session.can("report.read") && Boolean(session.scope && session.scope.all);

function setOptions(select, options, value) {
  select.textContent = "";
  select.append(...options.map((o) => h("option", { value: o.value, selected: String(o.value) === String(value) }, o.label)));
  select.value = String(value);
}

/** Блок вакансии: заголовок-строка и под ним таблица отчёта с раскрытием этапов, даты и «Открыть». */
function vacancyBlock(v, go) {
  return h("details.vblock", { dataset: { vacancy: v.id } },
    h("summary", vacancyLine(v)),
    h("div.vbody",
      h("div.t2", periodText(v)),
      funnelTable(v, { expand: true, title: v.title }),
      h("div.btns", h("button.b.tint", { type: "button", "aria-label": `Открыть вакансию: ${v.title}`, "data-tip": "Открыть",
        onClick: () => go("funnel", { id: v.id }, { view: "report" }) }, "Открыть"))));
}

/**
 * Шапка с переключателями и панель отчёта. between — узлы между шапкой и отчётом (у руководителя ОП — «Ждут утверждения»).
 * Возвращает массив узлов для mount().
 */
export function overview({ api, signal, session, go, query, between = null }) {
  const department = isDepartment(session);
  const userId = session.user && session.user.id;
  const state = parseView(query, { department, userId });
  const names = new Map();
  const yearId = uid("year");
  const whoId = uid("who");
  const yearSelect = h("select", { id: yearId, onChange: () => change({ year: Number(yearSelect.value) }) });
  const whoSelect = department ? h("select", { id: whoId, onChange: () => change({ recruiter: whoSelect.value === ALL ? ALL : Number(whoSelect.value) }) }) : null;
  const heading = h("h2.t1", overviewTitle(state.year));
  const panel = h("div", { role: "region", "aria-label": "Отчёт воронки" });
  setOptions(yearSelect, yearOptions([], state.year).map((y) => ({ value: y, label: String(y) })), state.year || "");
  if (whoSelect) setOptions(whoSelect, recruiterOptions([], state.recruiter), state.recruiter);

  const reportUrl = (path, year) => `/api/funnel/${path}${year ? `?year=${encodeURIComponent(year)}` : ""}`;

  async function load() {
    if (!department) return { rec: await api.get(reportUrl(`recruiter/${encodeURIComponent(userId)}`, state.year), { signal }) };
    const dept = await api.get(reportUrl("department", state.year), { signal });
    dept.recruiters.forEach((r) => names.set(r.id, r.name));
    if (state.recruiter === ALL) return { dept };
    try {
      return { dept, rec: await api.get(reportUrl(`recruiter/${encodeURIComponent(state.recruiter)}`, state.year), { signal }) };
    } catch (error) {
      if (error && error.status === 404) { state.recruiter = ALL; return { dept }; }   // такого рекрутера нет — «Весь отдел»
      throw error;
    }
  }

  function syncUrl() {
    window.history.replaceState(null, "", hashFor("funnel", {}, overviewQuery(department ? state : { year: state.year, recruiter: ALL })));   // адрес показывает год и чей отчёт
  }

  function draw({ dept, rec }) {
    const data = rec || dept;
    state.year = data.year;
    heading.textContent = overviewTitle(state.year);
    setOptions(yearSelect, yearOptions(data.years, state.year).map((y) => ({ value: y, label: String(y) })), state.year);
    if (whoSelect) setOptions(whoSelect, recruiterOptions(dept.recruiters, state.recruiter, names), state.recruiter);
    syncUrl();
    const whole = state.recruiter === ALL && department;
    const empty = whole ? !dept.recruiters.length : !rec.vacancies.length;
    if (empty) return [emptyCard(emptyYearText(state.year))];
    const title = recruiterTitle(state, department ? dept.recruiters : []);
    const nodes = [
      h("h2.sechead", `Общая воронка — ${title}`),
      funnelTable(data.total, { expand: false, title }),
    ];
    if (whole) {
      nodes.push(h("h2.sechead", "Сравнение рекрутеров — % этапа"), compareTable(dept, { year: state.year }));
    } else {
      nodes.push(h("h2.sechead", `Вакансии ${state.year}`), h("div.vlist", rec.vacancies.map((v) => vacancyBlock(v, go))));
    }
    nodes.push(h("p.hint", { style: "margin:10px 6px" }, hintText(data.since)));
    return nodes;
  }

  function run() {
    return mountAsync(panel, load, draw, { signal });
  }

  function change(patch) {
    Object.assign(state, patch);
    if (patch.year !== undefined) heading.textContent = overviewTitle(state.year);
    syncUrl();
    run();
  }

  const header = h("div.card", { style: "margin-bottom:12px" }, h("div.row",
    h("div.grow", heading, h("div.t2", "Сколько людей вошло на каждый этап и прошло его, по отметкам «прошёл / не прошёл» за год.")),
    h("div.filters2",
      h("div.field", h("label", { for: yearId }, "Год"), yearSelect),
      whoSelect ? h("div.field", h("label", { for: whoId }, "Чей отчёт"), whoSelect) : null,
      h("div.btns", { style: "margin:0" }, h("button.b", { type: "button", onClick: () => go("stages") }, "Справочник этапов")))));
  queueMicrotask(run);
  return [header, between, panel];
}

/** Для экрана без руководителя ОП: страница целиком. */
export function render(box, ctx) {
  mount(box, overview({ api: ctx.api, signal: ctx.signal, session: ctx.session, go: ctx.go, query: ctx.query }));
}
