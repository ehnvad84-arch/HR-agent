// Карточка «Синонимы ключей» (S1–S4) в разделе «Как ищем» «Базы должностей». Своё сохранение: синонимы действуют сразу и в версию
// профиля не входят. Логика — synonyms-model.js; здесь только сборка DOM. Всё из API выводится текстом (h()).
import { chipsInput } from "../chips.js";
import { h, mount } from "../dom.js";
import { hashFor } from "../router.js";
import { confirmSheet, errorLine, mountAsync, toast } from "../ui.js";
import {
  HEADER_NOTE, NO_KEYS_TIP, SAVED_TEXT, SCHEMA_NOTE, THINKING_TEXT, applySuggestions, discard, draftFromView, historyLines, panelState, rollbackSheet,
  savePayload, setTerms, suggestFailure, suggestedText,
} from "./synonyms-model.js";

/** Карточка для профиля pid. Возвращает корневой узел; грузит себя сама, ошибка загрузки — карточка с «Повторить». */
export function synonymsCard({ api, signal, pid }) {
  const box = h("div.card.inset.syncard", { style: "margin-top:14px" });
  let flash = null;   // итог прошлого действия: показывается после перезагрузки карточки

  const load = () => api.get(`/api/profiles/${pid}/synonyms`, { signal });

  const build = (view, kept = null) => {
    let draft = kept || draftFromView(view);
    let asking = null;   // AbortController запроса к модели
    const ps = () => panelState({ view, draft, asking: Boolean(asking) });
    const first = ps();

    const info = h("div", { "aria-live": "polite" });   // результат модели, сохранения, отказ S4
    const saveStatus = errorLine();
    const historyStatus = errorLine();
    const note = (text, { extra = null, role = "status", link = false } = {}) => h("div.note", { role, style: "margin:10px 0 0" }, text,
      extra ? [" ", extra] : null, link ? [" ", h("a.link", { href: hashFor("arch") }, SCHEMA_NOTE)] : null);

    // ---------------- строки: ключ и его чипсы
    const inputs = new Map();
    const rows = draft.rows.map((row) => {
      const chips = chipsInput({ label: row.keyword, values: row.terms, disabled: !first.canEdit, placeholder: "слово и Enter",
        isNew: (term) => Boolean(draft.rows.find((r) => r.keyword === row.keyword)?.fresh.some((f) => f.toLowerCase() === term.toLowerCase())) });
      const none = h("span.hint.nosyn", "нет синонимов");
      chips.root.querySelector(".chipbox").prepend(none);
      none.hidden = row.terms.length > 0;
      chips.onChange((terms) => {
        draft = setTerms(draft, row.keyword, terms);
        none.hidden = terms.length > 0;
        refresh();
      });
      inputs.set(row.keyword, chips);
      return h("div.synrow", chips.root);
    });

    // ---------------- кнопки
    const suggest = first.suggest;
    const askButton = suggest ? h("button.b.tint", { type: "button", "aria-disabled": suggest.inactive ? "true" : null, "data-tip": suggest.tip || null,
      onClick: () => ask() }, suggest.label) : null;
    const stopButton = h("button.b", { type: "button", onClick: () => asking && asking.abort() }, "Остановить");
    stopButton.hidden = true;
    const save = h("button.b.blue", { type: "button", onClick: () => doSave() }, "Сохранить синонимы");
    const cancel = h("button.b", { type: "button", onClick: () => { flash = null; mount(box, build(view, discard(draft))); } }, "Отменить");
    const dirty = h("span.hint");
    const bar = h("div.btns", { style: "margin-top:12px;align-items:center" }, save, cancel, dirty);

    function refresh() {
      const p = ps();
      bar.hidden = !p.showSave;
      dirty.textContent = p.dirtyText;
      if (askButton) {
        askButton.textContent = p.suggest.label;
        askButton.disabled = p.suggest.disabled;
      }
      stopButton.hidden = !p.suggest || !p.suggest.stop;
    }

    async function ask() {
      if (!first.hasKeys) { toast(NO_KEYS_TIP); return; }
      if (asking) return;
      asking = new AbortController();
      const stopWith = () => asking && asking.abort();
      signal.addEventListener("abort", stopWith, { once: true });
      mount(info, h("p.hint", { style: "margin:10px 0 0" }, THINKING_TEXT));
      refresh();
      try {
        const reply = await api.post(`/api/profiles/${pid}/synonyms/suggest`, {}, { signal: asking.signal });
        const result = applySuggestions(draft, reply.suggestions);
        draft = result.draft;
        for (const row of draft.rows) for (const term of row.fresh) inputs.get(row.keyword).add(term);
        mount(info, result.added > 0 ? h("p.hint", { style: "margin:10px 0 0" }, "(i) ", suggestedText(result.added)) : note(suggestedText(0)));
      } catch (error) {
        if (error && error.name === "AbortError") {
          mount(info);
        } else {
          const failure = suggestFailure(error);
          mount(info, note(failure.text, { extra: failure.extra, role: "note" }));
        }
      } finally {
        signal.removeEventListener("abort", stopWith);
        asking = null;
        refresh();
      }
    }

    async function doSave() {
      saveStatus.clear();
      save.disabled = true;
      try {
        await api.put(`/api/profiles/${pid}/synonyms`, savePayload(draft), { signal });
        flash = { text: SAVED_TEXT };
        toast(SAVED_TEXT);
        await reload();
      } catch (error) {
        if (error && error.name === "AbortError") return;
        saveStatus.show(error);   // введённое остаётся
        save.disabled = false;
      }
    }

    // ---------------- история
    const lines = historyLines(view);
    const historyTitle = h("summary", { tabindex: "-1" }, first.historyTitle);
    const history = h("details.logmore", { style: "margin-top:14px" }, historyTitle,
      lines.length ? h("div.list", { style: "box-shadow:none;margin-top:8px" }, lines.map((line) => h("div.li", { style: "cursor:default;align-items:center" },
        h("div", { style: "flex:1;min-width:0" }, h("div.sub", `${line.when} · ${line.by} · ${line.text}`)),
        line.canRollback && first.canEdit ? h("button.b", { type: "button", "aria-label": `Откатить правку от ${line.when}`, onClick: () => rollback(line) }, "Откатить") : null)))
        : h("p.hint", { style: "margin:8px 0 0" }, "Правок пока не было."),
      historyStatus.el);

    async function rollback(line) {
      historyStatus.clear();
      if (!await confirmSheet(rollbackSheet(line))) return;
      try {
        await api.post(`/api/profiles/${pid}/synonyms/rollback/${line.id}`, {}, { signal });
        flash = { text: SAVED_TEXT, focusHistory: true };
        toast("Правка откатана");
        await reload();
      } catch (error) {
        if (error && error.name === "AbortError") return;
        historyStatus.show(error);   // 409: после открытия кто-то сохранил — обновить карточку
      }
    }

    const mine = flash;
    flash = null;
    const root = h("div", { "data-synonyms": "" },
      h("div", { style: "display:flex;gap:12px;align-items:center;flex-wrap:wrap" },
        h("h3.t1", { style: "flex:1 1 200px;margin:0;font-size:16px" }, "Синонимы ключей"), askButton, stopButton),
      h("p.hint", { style: "margin:6px 0 10px" }, HEADER_NOTE),
      first.readonlyNote ? h("p.hint", { style: "margin:0 0 8px;font-weight:600" }, first.readonlyNote) : null,
      first.hasKeys ? h("div.synrows", rows) : h("p.hint", { style: "margin:0" }, NO_KEYS_TIP),
      info, bar, saveStatus.el,
      mine ? h("div", { "aria-live": "polite" }, note(mine.text, { link: true })) : null,
      history);
    refresh();
    if (mine && mine.focusHistory) queueMicrotask(() => historyTitle.focus());
    return root;
  };

  async function reload() {
    await mountAsync(box, load, build, { signal });
  }
  reload();
  return box;
}
