// «База» (S19, S20, S22; П:327–340): таблица всех карточек с сортировкой и фильтрами, поиск по смыслу, «подходящие к вакансии».
// Сортировка, фильтры и страницы считает сервер (GET /api/base); состояние — в адресе: #/base?city=Томск&sort=city&dir=asc.
// Всё из API выводится только текстом (h(), textContent).
import { h, mount } from "../dom.js";
import { absoluteDay } from "../format.js";
import { chip, face, personHref, personName } from "../people.js";
import { loadResumesButton } from "../hh-load.js";
import { hashFor } from "../router.js";
import { emptyCard, errorCard, loadingLine } from "../ui.js";
import {
  COLUMNS, WHERE_VALUES, apiRequest, ariaSort, filtersActive, nextSort, pageInfo, parseState, similarityText, sourceTitle,
  stateToQuery, whereTitle,
} from "./base-model.js";

const DEBOUNCE_MS = 400;
const WHERE_PILLS = { candidate: "cand", watched: "watch", observed: "obs" };

export function render(box, ctx) {
  const { api, signal } = ctx;
  let state = parseState(ctx.query);
  let current = null;           // AbortController последнего запроса: новый запрос отменяет прежний
  let loadTimer = null;
  let typing = null;
  const seen = { cities: new Set(), sources: new Set(), depts: new Map(), roles: new Map() };
  let profiles = [];            // действующие профили: полный список подразделений и должностей для выпадающих списков

  const syncUrl = () => window.history.replaceState(null, "", hashFor("base", {}, stateToQuery(state)));

  // ---------------------------------------------------------------- каркас

  const search = h("input.search", { id: "base-q", type: "search", name: "q", value: state.q, "data-tip": "search",
    placeholder: "Поиск по имени, должности, навыку, городу", autocomplete: "off" });
  const found = h("span.hint", { role: "status" });
  const vacancyBar = h("div");
  const resetButton = h("button.b", { type: "button", onClick: () => resetAll() }, "Сбросить");
  const tools = h("form.tools", { role: "search" },
    h("label.sr-only", { for: "base-q" }, "Поиск по смыслу"), search,
    h("button.b.fill", { type: "submit" }, "Найти"), resetButton,
    // «Загрузить резюме» — только с правом settings.edit; после запуска ход виден в полосе под вкладками
    loadResumesButton({ api, signal, session: ctx.session }), found);
  const searchHint = h("p.hint", { style: "margin:-4px 6px 12px" },
    "Поиск по смыслу: опишите словами, кто нужен, например «схемотехник Altium». Показывается список по близости; фильтры таблицы в нём не действуют.");
  const searchBox = h("div", { "aria-live": "polite" });

  const headerRow = h("tr");
  const filterRow = h("tr.filters");
  const tbody = h("tbody");
  const table = h("table.base", h("caption.sr-only", "Карточки людей: щелчок по заголовку сортирует"), h("thead", headerRow, filterRow), tbody);
  const tableWrap = h("div.tablewrap", table);
  const pager = h("nav.pager", { "aria-label": "Страницы" });
  const tableHint = h("p.hint", { style: "margin:10px 6px" },
    "Щелчок по заголовку сортирует, повторный — в обратную сторону. Строка под заголовками фильтрует по значениям колонки, фильтры складываются. Имя открывает досье этого человека.");
  const tableBox = h("div", vacancyBar, tableWrap, pager, tableHint);

  // ---------------------------------------------------------------- заголовки

  const headerCells = COLUMNS.map((column) => {
    const arrow = h("span.dir", { "aria-hidden": "true" }, "↕");
    const th = h("th", { scope: "col", "aria-sort": "none", onClick: () => sortBy(column.sort) },
      h("button", { type: "button", "data-tip": "sort" }, column.title, " ", arrow));
    return { column, th, arrow };
  });
  mount(headerRow, headerCells.map((c) => c.th));
  const drawHeaders = () => {
    for (const { column, th, arrow } of headerCells) {
      const order = ariaSort(state, column.sort);
      th.setAttribute("aria-sort", order);
      th.classList.toggle("sorted", order !== "none");
      arrow.textContent = order === "ascending" ? "↑" : order === "descending" ? "↓" : "↕";
    }
  };

  // ---------------------------------------------------------------- фильтры

  const options = (list, value) => {
    const items = list.some(([id]) => id === value) || !value ? list : [...list, [value, value]];
    return [h("option", { value: "" }, "все"), ...items.map(([id, label]) => h("option", { value: id }, label))];
  };

  const deptSelect = h("select", { "aria-label": "Фильтр: Подразделение", "data-tip": "filter", onChange: (e) => setFilter("dept", e.target.value) });
  const roleSelect = h("select", { "aria-label": "Фильтр: Должность", "data-tip": "filter", onChange: (e) => setFilter("role", e.target.value) });
  const whereSelect = h("select", { "aria-label": "Фильтр: Где числится", "data-tip": "filter", onChange: (e) => setFilter("where", e.target.value) });
  const sourceSelect = h("select", { "aria-label": "Фильтр: Источник", "data-tip": "filter", onChange: (e) => setFilter("source", e.target.value) });
  const cityList = h("datalist", { id: "base-cities" });
  const cityInput = h("input", { type: "text", list: "base-cities", placeholder: "город", autocomplete: "off", "aria-label": "Фильтр: Город", "data-tip": "filter",
    onInput: (e) => typed(() => setFilter("city", e.target.value.trim())) });
  const scoreInput = h("input", { type: "number", min: 0, max: 100, placeholder: "от", "aria-label": "Фильтр: Балл от", "data-tip": "filter",
    onInput: (e) => typed(() => setFilter("score_min", /^\d{1,3}$/.test(e.target.value) ? e.target.value : "")) });
  const controls = { dept: deptSelect, role: roleSelect, where: whereSelect, source: sourceSelect, city: cityInput, score: scoreInput };
  mount(filterRow,
    h("th"), h("th", cityInput, cityList), h("th", roleSelect), h("th", deptSelect), h("th", scoreInput),
    h("th", whereSelect), h("th", sourceSelect), h("th"), h("th"));

  const typed = (action) => {
    clearTimeout(typing);
    typing = setTimeout(action, DEBOUNCE_MS);
  };

  const deptList = () => {
    const map = new Map(seen.depts);
    for (const p of profiles) if (p.dept_code) map.set(p.dept_code, p.dept);
    return [...map.entries()].sort((a, b) => a[1].localeCompare(b[1], "ru"));
  };
  const roleList = () => {   // должности выбранного подразделения; без профилей — те, что встретились в данных
    const map = new Map();
    for (const [code, role] of seen.roles) if (!state.dept || role.dept === state.dept) map.set(code, role.title);
    for (const p of profiles) if (!state.dept || p.dept_code === state.dept) map.set(p.code, p.title);
    return [...map.entries()].sort((a, b) => a[1].localeCompare(b[1], "ru"));
  };
  const drawFilters = () => {
    mount(deptSelect, options(deptList(), state.dept));
    mount(roleSelect, options(roleList(), state.role));
    mount(whereSelect, options(WHERE_VALUES.map((w) => [w, whereTitle(w)]), state.where));
    mount(sourceSelect, options([...new Set([...Object.keys({ hh: 1, trudvsem: 1, habr: 1, avito: 1, manual: 1 }), ...seen.sources])]
      .map((code) => [code, sourceTitle(code)]), state.source));
    mount(cityList, [...seen.cities].sort((a, b) => a.localeCompare(b, "ru")).map((c) => h("option", { value: c })));
    deptSelect.value = state.dept;
    roleSelect.value = state.role;
    whereSelect.value = state.where;
    sourceSelect.value = state.source;
    if (document.activeElement !== cityInput) cityInput.value = state.city;
    if (document.activeElement !== scoreInput) scoreInput.value = state.score_min;
    // для вакансии сервер знает только город и балл
    for (const key of ["dept", "role", "where", "source"]) {
      controls[key].disabled = Boolean(state.vacancy);
      controls[key].title = state.vacancy ? "Для подходящих к вакансии фильтр недоступен" : "";
    }
  };

  // ---------------------------------------------------------------- действия

  function setFilter(key, value) {
    if (state[key] === value) return;
    state = { ...state, [key]: value, page: 0 };
    if (key === "dept" && state.role && !roleList().some(([code]) => code === state.role)) state.role = "";
    changed();
  }
  function sortBy(sortKey) {
    state = { ...state, ...nextSort(state, sortKey) };
    changed();
  }
  function goPage(page) {
    state = { ...state, page };
    changed();
    tableWrap.scrollTop = 0;
  }
  function resetAll() {
    state = parseState({});
    search.value = "";
    changed();
  }
  function changed() {
    syncUrl();
    drawHeaders();
    drawFilters();
    load();
  }
  tools.addEventListener("submit", (event) => {
    event.preventDefault();
    state = { ...state, q: search.value.trim(), page: 0 };
    changed();
    search.focus();
  });

  // ---------------------------------------------------------------- данные

  function remember(items) {
    for (const row of items) {
      if (row.city) seen.cities.add(row.city);
      for (const code of row.sources || []) seen.sources.add(code);
      if (row.dept_code) seen.depts.set(row.dept_code, row.dept);
      if (row.role_code) seen.roles.set(row.role_code, { title: row.role_title, dept: row.dept_code });
    }
  }

  const pill = (where) => h("span.pill", { class: WHERE_PILLS[where] || null, "data-tip": "pill-where" }, whereTitle(where));
  const dash = (value) => (value === null || value === undefined || value === "" ? "—" : value);

  function rowFor(row) {
    const open = () => ctx.go("person", { id: row.person_id });
    const tr = h("tr.trow", { onClick: (event) => { if (!event.target.closest("a, button")) open(); } },
      h("td.name", h("span.namewrap", face(row, { size: "md" }), h("a.link", { href: personHref(row.person_id) }, personName(row)))),
      h("td", dash(row.city)), h("td", dash(row.role_title)), h("td.hint", dash(row.dept)),
      h("td.num", dash(row.score)), h("td", pill(row.where)),
      h("td", (row.sources || []).map(sourceTitle).join(", ") || "—"),
      h("td.num", row.socn || "—"), h("td.hint", row.updated ? absoluteDay(row.updated) : "—"));
    return tr;
  }

  function showTable(data) {
    remember(data.items);
    searchBox.hidden = true;
    tableBox.hidden = false;
    const info = pageInfo(data);
    if (!data.items.length && data.total > 0 && state.page > 0) {   // страниц стало меньше (сменился фильтр): на последнюю
      state = { ...state, page: info.pages - 1 };
      changed();
      return;
    }
    found.textContent = info.text;
    if (!filtersActive(state) && !state.vacancy) ctx.setBadge("base", data.total);

    mount(vacancyBar, state.vacancy ? h("div.filterbar",
      h("span", `Подходящие к вакансии «${data.vacancy ? data.vacancy.title : state.vacancy}»: баллы по профилю вакансии, выше порога наблюдения, по убыванию балла`),
      h("button", { type: "button", onClick: () => resetAll() }, "Показать всю базу")) : null);

    mount(tbody, data.items.length ? data.items.map(rowFor)
      : h("tr", h("td.hint", { colspan: COLUMNS.length },
        filtersActive(state) || state.vacancy
          ? ["Ничего не нашлось ", h("button.link", { type: "button", onClick: () => resetAll() }, "Сбросить")]
          : "Карточек пока нет: сбор идёт по утрам")));

    mount(pager, info.pages > 1 ? [
      h("button.b", { type: "button", disabled: !info.hasPrev, onClick: () => goPage(info.page - 1) }, "← Назад"),
      h("span.hint", `Страница ${info.page + 1} из ${info.pages}`),
      h("button.b", { type: "button", disabled: !info.hasNext, onClick: () => goPage(info.page + 1) }, "Вперёд →"),
    ] : null);
    drawFilters();
  }

  function showSearch(data) {
    tableBox.hidden = true;
    searchBox.hidden = false;
    const items = data.items || [];
    found.textContent = items.length ? `Найдено по смыслу: ${items.length}` : "";
    mount(searchBox, items.length
      ? h("div.list", items.map((row) => h("a.li", { href: personHref(row.person_id) },
        face(row, { size: "md" }),
        h("div", h("div", { style: "font-weight:600" }, personName(row)), h("div.sub", dash(row.city))),
        h("span.chev", { style: "font-size:13px" }, similarityText(row.similarity)))))
      : emptyCard("Похожих нет, попробуйте другими словами"));
  }

  function failed(error, retry) {
    found.textContent = "";
    const card = errorCard(error, { onRetry: retry });
    if (state.q) {
      tableBox.hidden = true;
      searchBox.hidden = false;
      mount(searchBox, card);
    } else {
      tableBox.hidden = false;
      searchBox.hidden = true;
      mount(tbody, h("tr", h("td", { colspan: COLUMNS.length }, card)));
      mount(pager, null);
    }
  }

  async function load() {
    if (current) current.abort();
    const controller = new AbortController();
    current = controller;
    signal.addEventListener("abort", () => controller.abort(), { once: true });
    clearTimeout(loadTimer);
    const request = apiRequest(state);
    box.setAttribute("aria-busy", "true");
    loadTimer = setTimeout(() => {
      if (current !== controller) return;
      if (request.kind === "search") { searchBox.hidden = false; tableBox.hidden = true; mount(searchBox, loadingLine()); }
      else mount(tbody, h("tr", h("td.hint", { colspan: COLUMNS.length }, "Загружаю…")));
    }, 300);
    try {
      const data = await api.get(request.path, { query: request.query, signal: controller.signal });
      if (current !== controller) return;
      if (request.kind === "search") showSearch(data);
      else showTable(data);
    } catch (error) {
      if (current !== controller || (error && error.name === "AbortError")) return;
      failed(error, () => load());
    } finally {
      if (current === controller) {
        clearTimeout(loadTimer);
        box.removeAttribute("aria-busy");
      }
    }
  }

  // ---------------------------------------------------------------- старт

  mount(box, tools, searchHint, searchBox, tableBox);
  searchBox.hidden = true;
  drawHeaders();
  drawFilters();
  load();
  // полный список подразделений и должностей — из действующих профилей; не пришёл — берём значения из данных
  api.get("/api/profiles", { signal }).then((reply) => {
    profiles = (reply.items || []).filter((p) => p.state === "approved");
    drawFilters();
  }).catch(() => {});
  return undefined;
}
