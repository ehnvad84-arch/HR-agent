// Кнопка «Загрузить резюме» и окно «сколько» (S44): вручную скачать полные резюме с hh в пределах дневного лимита просмотров.
// Только с правом settings.edit (руководитель подбора): загрузка тратит лимит hh. Сервер сам проверяет 1…остаток и отвечает 422.
// Используется на «Схеме» (там же виден ход прогона collect_hh) и в «Базе». Всё из API выводится только текстом (h(), textContent).
import { h, mount } from "./dom.js";
import {
  EXPLAIN, STARTED_TOAST, canStart, countError, defaultCount, exhaustedText, limitsText, normalizeLimits, parseCount,
} from "./hh-load-model.js";
import { errorLine, field, sheet, toast } from "./ui.js";

/**
 * Окно «Загрузить резюме»: остаток просмотров и пакета, поле числа (по умолчанию весь остаток), «Загрузить» только для 1…остаток.
 * Ошибки (422, 503 «hh не настроен», 502 «hh не ответил») — текстом в окне, окно не закрывается.
 *   onStarted(reply) — вызывается после запуска (reply: {started, count, views_left}).
 */
export function openLoadSheet({ api, signal, onStarted }) {
  const win = sheet({ title: "Загрузить резюме", width: "500px" });
  const body = h("div");
  win.body.append(body);
  win.show();

  const explain = () => h("p.hint", { style: "margin:12px 0 0" }, EXPLAIN);
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
        const reply = await api.post("/api/hh/collect", { count: parseCount(count.input.value) }, { signal });
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
    } }, h("div.form", h("p.t2", { style: "margin:0" }, limitsText(limits)), count.root, explain()),
    h("div.sheetfoot.end", cancel(), go), status.el);

    mount(body, form);
    update();
    count.input.focus();
    count.input.select();
  };

  async function load(notice = "") {
    mount(body, h("p.loading", "Загружаю…"));
    try {
      drawLimits(normalizeLimits(await api.get("/api/hh/limits", { signal })), notice);
    } catch (error) {
      if (error && error.name === "AbortError") { win.close("cancel"); return; }
      drawError(error);
    }
  }
  load();
  return win.closed;
}

/** Кнопка «Загрузить резюме» или null, если у роли нет права settings.edit (у рекрутера её нет). */
export function loadResumesButton({ api, signal, session, onStarted, variant = "" }) {
  if (!session.can("settings.edit")) return null;
  return h("button.b", { type: "button", class: variant, onClick: () => openLoadSheet({ api, signal, onStarted }) }, "Загрузить резюме");
}
