// Кнопка «Загрузить резюме HH.ru» и окно «сколько» (S44, S46): вручную скачать полные резюме с hh в пределах дневного лимита просмотров.
// Только с правом settings.edit (руководитель подбора): загрузка тратит лимит hh. Сервер сам проверяет 1…остаток и отвечает 422.
// Два режима: общий («Сегодня», «База») и по одной должности и региону («База должностей»).
// После запуска — переход на «Схему», где виден ход прогона collect_hh. Всё из API выводится только текстом (h(), textContent).
import { h, mount, uid } from "./dom.js";
import {
  AREA_DEBOUNCE_MS, EXPLAIN, FALLBACK_AREAS, ROLE_EXPLAIN, STARTED_TOAST, areaForGeo, areaLine, canSearchAreas, canStart, collectBody,
  countError, defaultCount, exhaustedText, limitsText, moveHighlight, normalizeAreas, normalizeLimits, roleSheetTitle,
} from "./hh-load-model.js";
import { errorLine, field, sheet, toast } from "./ui.js";

/**
 * Выбор региона для загрузки по должности (S46): быстрые кнопки, поиск по названию (подсказки hh), явная строка «Регион: …».
 * Возвращает {root, get area()}. Выбранный вне быстрого списка регион появляется своей кнопкой с «×»: она возвращает регион из портрета.
 */
function regionPicker({ api, signal, quick, initial, fallback }) {
  let selected = initial;
  let found = [];
  let active = -1;
  let timer = null;
  let ticket = 0;
  const listId = uid("areas");
  const searchId = uid("area-search");
  const summary = h("p.regionline", { role: "status" });
  const chips = h("div.areachips", { role: "group", "aria-label": "Быстрый выбор региона" });
  const status = errorLine();
  const list = h("div.arealist", { id: listId, role: "listbox", "aria-label": "Найденные регионы", hidden: true });
  const search = h("input", { id: searchId, type: "text", name: "area-search", autocomplete: "off", role: "combobox", "aria-controls": listId,
    "aria-expanded": "false", "aria-autocomplete": "list", placeholder: "Другой город или регион: начните вводить название", "data-tip": "hh-area-search" });

  const closeList = () => {
    found = [];
    active = -1;
    list.hidden = true;
    search.setAttribute("aria-expanded", "false");
    search.removeAttribute("aria-activedescendant");
    mount(list);
  };
  const choose = (area) => {
    selected = area;
    search.value = "";
    status.clear();
    closeList();
    drawChips();
  };
  function drawChips() {
    summary.textContent = areaLine(selected);
    const inQuick = quick.some((area) => area.id === selected.id);
    mount(chips,
      quick.map((area) => h("button.areachip", { type: "button", "aria-pressed": area.id === selected.id, "data-tip": "hh-area", onClick: () => choose(area) }, area.name)),
      inQuick ? null : h("span.areachip.on.removable", h("span", selected.name),
        h("button", { type: "button", "aria-label": `Убрать регион «${selected.name}»: вернуть ${fallback.name}`, "data-tip": "hh-area-remove", onClick: () => choose(fallback) }, "×")));
  }
  function drawList(message) {
    list.hidden = false;
    search.setAttribute("aria-expanded", "true");
    mount(list, message ? h("p.hint", { style: "margin:6px 10px" }, message) : found.map((area, i) =>
      h("div.areaopt", { id: `${listId}-${i}`, role: "option", "aria-selected": i === active, onMousedown: (event) => event.preventDefault(), onClick: () => choose(area) }, area.name)));
    if (active >= 0) search.setAttribute("aria-activedescendant", `${listId}-${active}`);
    else search.removeAttribute("aria-activedescendant");
  }
  async function lookup(text) {
    const mine = ++ticket;
    drawList("Ищу…");
    try {
      const reply = await api.get("/api/hh/areas", { query: { q: text.trim() }, signal });
      if (mine !== ticket) return;
      found = normalizeAreas(reply);
      active = found.length ? 0 : -1;
      status.clear();
      drawList(found.length ? "" : "Ничего не нашлось");
    } catch (error) {
      if (mine !== ticket || (error && error.name === "AbortError")) return;
      closeList();
      status.show(error);   // 502 «hh не ответил»
    }
  }
  search.addEventListener("input", () => {
    clearTimeout(timer);
    ticket += 1;   // ответ на прежний запрос уже не нужен
    if (!canSearchAreas(search.value)) { closeList(); status.clear(); return; }
    timer = setTimeout(() => lookup(search.value), AREA_DEBOUNCE_MS);
  });
  search.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (!found.length) return;
      event.preventDefault();
      active = moveHighlight(active, event.key === "ArrowDown" ? 1 : -1, found.length);
      drawList("");
    } else if (event.key === "Enter") {
      event.preventDefault();   // Enter в поиске региона выбирает регион, а не отправляет форму
      if (found.length) choose(found[Math.max(0, active)]);
    } else if (event.key === "Escape" && !list.hidden) {
      event.preventDefault();   // первый Esc закрывает список, второй — окно
      event.stopPropagation();
      closeList();
    }
  });
  drawChips();
  return {
    root: h("div.region", summary, chips,
      h("div.field", h("label.sr-only", { for: searchId }, "Найти город или регион по названию"), search, list), status.el),
    get area() { return selected; },
    get searching() { return Boolean(search.value.trim()); },
  };
}

/**
 * Окно «Загрузить резюме»: остаток просмотров и пакета, поле числа (по умолчанию весь остаток), «Загрузить» только для 1…остаток.
 * Ошибки (422, 503 «hh не настроен», 502 «hh не ответил») — текстом в окне, окно не закрывается.
 *   onStarted(reply) — вызывается после запуска (reply: {started, count, views_left}).
 *   role — {id, title, geo}: загрузка по одной должности и региону (S46); регион по умолчанию берётся из портрета (geo).
 */
export function openLoadSheet({ api, signal, onStarted, role = null }) {
  const win = sheet({ title: role ? roleSheetTitle(role.title) : "Загрузить резюме HH.ru", width: role ? "560px" : "500px" });
  let geo = role ? role.geo : undefined;   // в списке должностей географии портрета нет: тогда окно берёт её из самого портрета
  let selected = role ? areaForGeo(geo) : null;   // выбранный регион переживает перерисовку окна (например после 422)
  let quick = FALLBACK_AREAS;
  const body = h("div");
  win.body.append(body);
  win.show();

  const explain = () => h("div", role ? h("p.hint", { style: "margin:12px 0 0" }, ROLE_EXPLAIN) : null, h("p.hint", { style: "margin:12px 0 0" }, EXPLAIN));
  const cancel = (label = "Отмена") => h("button.b", { type: "button", "data-tip": label, onClick: () => win.close("cancel") }, label);

  const drawError = (error) => {
    const status = errorLine();
    status.show(error);
    mount(body, status.el, h("div.sheetfoot.end", cancel(), h("button.b.fill", { type: "button", onClick: () => load() }, "Повторить")));
  };

  const drawLimits = (limits, notice) => {
    if (limits.left < 1) {
      mount(body, h("p.t2", { style: "margin:0" }, exhaustedText()), explain(), h("div.sheetfoot.end", cancel("Понятно")));
      return;
    }
    const region = role ? regionPicker({ api, signal, quick, initial: selected, fallback: areaForGeo(geo) }) : null;
    const count = field({ label: "Сколько резюме загрузить", name: "count", value: defaultCount(limits.left), autocomplete: "off", tip: "hh-load-count" });
    count.input.setAttribute("inputmode", "numeric");
    const status = errorLine();
    if (notice) status.show(notice);
    const go = h("button.b.fill", { type: "submit", "data-tip": "hh-load-go" }, "Загрузить");
    let busy = false;
    const update = () => {
      const text = count.input.value;
      go.disabled = busy || !canStart(text, limits.left);
      count.error(text.trim() ? countError(text, limits.left) : "");   // пустое поле не ругаем, пока человек не начал вводить
    };
    count.input.addEventListener("input", () => { status.clear(); update(); });

    const form = h("form", { novalidate: true, onSubmit: async (event) => {
      event.preventDefault();
      if (busy || !canStart(count.input.value, limits.left)) return;
      status.clear();
      busy = true;
      go.textContent = "Запускаю…";
      update();
      try {
        if (region) selected = region.area;
        const reply = await api.post("/api/hh/collect", collectBody({ count: count.input.value, profileId: role ? role.id : null, areaId: selected ? selected.id : null }), { signal });
        win.close("started");
        toast(STARTED_TOAST);
        if (onStarted) onStarted(reply);
      } catch (error) {
        if (error && error.name === "AbortError") { win.close("cancel"); return; }
        busy = false;
        go.textContent = "Загрузить";
        if (error && error.status === 422) {   // остаток мог измениться (другая вкладка, ночной сбор): показываем причину и актуальный остаток
          load(error.message);
          return;
        }
        status.show(error);   // 503 «hh не настроен», 502 «hh не ответил»
        update();
      }
    } }, h("div.form", h("p.t2", { style: "margin:0" }, limitsText(limits)), region ? region.root : null, count.root, explain()),
    h("div.sheetfoot.end", cancel(), go), status.el);

    mount(body, form);
    update();
    count.input.focus();
    count.input.select();
  };

  async function load(notice = "") {
    mount(body, h("p.loading", "Загружаю…"));
    try {
      // регионы и география портрета не обязательны для запуска: не пришли — запасной список и Краснодар
      const optional = (request) => request.catch((error) => { if (error && error.name === "AbortError") throw error; return null; });
      const [limits, areas, portrait] = await Promise.all([api.get("/api/hh/limits", { signal }),
        role ? optional(api.get("/api/hh/areas", { signal })) : null,
        role && geo === undefined ? optional(api.get(`/api/profile/${encodeURIComponent(role.id)}`, { signal })) : null]);
      const fetched = normalizeAreas(areas);
      if (fetched.length) quick = fetched;
      if (role && geo === undefined) {
        geo = portrait && portrait.geo ? portrait.geo : null;
        selected = areaForGeo(geo);
      }
      drawLimits(normalizeLimits(limits), notice);
    } catch (error) {
      if (error && error.name === "AbortError") { win.close("cancel"); return; }
      drawError(error);
    }
  }
  load();
  return win.closed;
}

/**
 * Кнопка «Загрузить резюме HH.ru» или null, если у роли нет права settings.edit (у рекрутера её нет).
 * role — {id, title, geo} утверждённого портрета: кнопка открывает окно по должности и региону (S46).
 */
export function loadResumesButton({ api, signal, session, onStarted, variant = "", role = null }) {
  if (!session.can("settings.edit")) return null;
  return h("button.b", { type: "button", class: variant, "data-tip": role ? "hh-load-role" : null,
    onClick: (event) => {
      event.preventDefault();
      event.stopPropagation();
      openLoadSheet({ api, signal, onStarted, role });
    } }, "Загрузить резюме HH.ru");
}
