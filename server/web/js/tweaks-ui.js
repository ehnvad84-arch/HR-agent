// Личное обучение (S15, S16): «Предложения агента» и «Мои поправки» — плюс, минус, стоп-слова, свои веса признаков.
// Поправки меняют только «мой балл» и порядок своих списков; общий балл не трогают. Всё из API выводится только текстом.
import { h, mount, uid } from "./dom.js";
import { chip } from "./people.js";
import { weightShare } from "./screens/profiles-model.js";
import {
  RESET_HINT, SAVE_HINT, WORD_KINDS, diffTweaks, draftFrom, groupTweaks, isDirty, proposalText, validateWord, weightRows,
} from "./screens/tweaks-model.js";
import { confirmSheet, errorLine, toast } from "./ui.js";
import { weightControl } from "./weight-input.js";

const wordKey = (text) => String(text).trim().toLowerCase().replaceAll("ё", "е");

/** Поле одного вида слов: чипсы с «×», ввод по Enter, запятой или уходу из поля; ошибка проверки — под полем. */
function wordsField({ kind, title, hint, draft, inDigest, onChange }) {
  const id = uid(`tw-${kind}`);
  const hintId = `${id}-hint`;
  const errId = `${id}-err`;
  const list = h("span.chiplist", { role: "list" });
  const input = h("input", { id, type: "text", maxlength: 80, autocomplete: "off", placeholder: "добавить слово или фразу", "data-tip": "tweak-word",
    "aria-describedby": `${hintId} ${errId}` });
  const error = h("p.fielderr", { id: errId, role: "alert" });
  const box = h("div.chipbox", { onClick: () => input.focus() }, list, input);
  const words = () => draft[kind];

  const draw = () => mount(list, words().map((word) => h("span.tw", { role: "listitem" },
    h("span.chip.removable", word, h("button", { type: "button", "data-tip": "chips-remove", "aria-label": `Убрать «${word}»`,
      onClick: (event) => { event.stopPropagation(); draft[kind] = words().filter((x) => x !== word); draw(); onChange(); input.focus(); } }, "×")),
    inDigest(kind, word) ? chip("в сводке", "v", "tweak-digest") : null)));
  const add = () => {
    if (!input.value.trim()) { error.textContent = ""; input.setAttribute("aria-invalid", "false"); return; }
    const checked = validateWord(input.value, words());
    error.textContent = checked.ok ? "" : checked.error;
    input.setAttribute("aria-invalid", checked.ok ? "false" : "true");
    if (!checked.ok) return;
    draft[kind] = [...words(), checked.value];
    input.value = "";
    draw();
    onChange();
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === ",") { event.preventDefault(); add(); }
    else if (event.key === "Backspace" && !input.value && words().length) { draft[kind] = words().slice(0, -1); draw(); onChange(); }
  });
  input.addEventListener("blur", () => { if (input.value.trim()) add(); });
  draw();
  return h("div.field", h("label", { for: id }, title), box, h("div.hint", { id: hintId, style: "margin-top:5px" }, hint), error);
}

/**
 * Два блока правой колонки «Обучения агента». initial — ответ GET /api/tweaks.
 * Возвращает {proposals, mine}: оба узла перерисовываются сами после каждого действия.
 */
export function personalLearning({ api, signal, initial }) {
  let state = { items: initial.items || [], common: initial.weights || {} };
  let draft = draftFrom(state.items, state.common);
  let saveError = null;   // ошибка сохранения переживает перерисовку блока: введённое остаётся, причина видна
  const proposals = h("section.card", { "aria-labelledby": "tw-prop-title" });
  const mine = h("section.card", { "aria-labelledby": "tw-mine-title" });

  const reload = async ({ keepDraft = false } = {}) => {
    try {
      const fresh = await api.get("/api/tweaks", { signal });
      state = { items: fresh.items || [], common: fresh.weights || state.common };
      if (!keepDraft) draft = draftFrom(state.items, state.common);
    } catch (error) {
      if (!(error && error.name === "AbortError")) toast(error && error.message ? error.message : "Не удалось обновить поправки");
    }
    if (!signal.aborted) { drawProposals(); drawMine(); }
  };

  // ---------------------------------------------------------------- предложения агента
  function drawProposals() {
    const list = groupTweaks(state.items).proposed;
    mount(proposals, h("h2.t1", { id: "tw-prop-title" }, "Предложения агента"),
      list.length ? list.map(proposalCard)
        : h("p.t2", { style: "margin:6px 0 0" }, "Предложений пока нет: агент предлагает поправки по вашим замечаниям в чате. Применяются они только после вашей кнопки."));
  }
  function proposalCard(tweak) {
    const status = errorLine();
    const buttons = [];
    const act = (path, done) => async () => {
      status.clear();
      buttons.forEach((b) => { b.disabled = true; });
      try {
        await api.post(`/api/tweak/${encodeURIComponent(tweak.id)}/${path}`, {}, { signal });
        toast(done);
        await reload();
      } catch (error) {
        if (error && error.name === "AbortError") return;
        status.show(error);
        buttons.forEach((b) => { b.disabled = false; });
      }
    };
    buttons.push(
      h("button.b.fill", { type: "button", "data-tip": "tweak-apply", onClick: act("confirm", "Применено к вашему баллу: он пересчитывается") }, "Применить к моему баллу"),
      h("button.b", { type: "button", "data-tip": "tweak-decline", onClick: act("off", "Предложение убрано") }, "Не нужно"));
    return h("div.card.inset", { style: "margin-top:10px" }, h("div.t1", { style: "font-size:15.5px" }, proposalText(tweak)),
      h("div.t2", "Агент предлагает это по вашим сообщениям. Пока вы не нажали «Применить», балл не меняется."),
      h("div.btns", buttons), status.el);
  }

  // ---------------------------------------------------------------- мои поправки
  function drawMine() {
    const groups = groupTweaks(state.items);
    const digestKeys = new Set(state.items.filter((t) => t.in_digest && t.value && t.value.text).map((t) => `${t.kind}:${wordKey(t.value.text)}`));
    const status = errorLine();
    if (saveError) status.show(saveError);
    const save = h("button.b.fill", { type: "button", "data-tip": "tweak-save", disabled: true }, "Сохранить мои поправки");
    const hasActive = state.items.some((t) => t.state === "active");
    const reset = h("button.b", { type: "button", "data-tip": "tweak-reset", disabled: !hasActive }, "Сбросить к общим");
    let busy = false;
    const refreshSave = () => { save.disabled = busy || !isDirty(state.items, draft, state.common); };

    const fields = WORD_KINDS.map(([kind, title, hint]) => wordsField({ kind, title, hint, draft,
      inDigest: (k, word) => digestKeys.has(`${k}:${wordKey(word)}`), onChange: refreshSave }));

    const rows = weightRows(state.common, groups.weights);
    const controls = {};
    const redrawShares = () => {
      for (const row of rows) controls[row.key].share(weightShare(draft.weights, row.key));
      for (const row of rows) note[row.key].textContent = Number(draft.weights[row.key]) !== row.common ? `Общий вес: ${row.common} · у вас другой` : `Общий вес: ${row.common}`;
    };
    const note = {};
    const weightRowsEl = rows.map((row) => {
      controls[row.key] = weightControl({ key: row.key, label: row.label, value: String(draft.weights[row.key] ?? row.value), onInput: () => {
        const text = controls[row.key].input.value;
        const number = text === "" ? NaN : Math.min(100, Number(text));
        if (Number.isFinite(number) && String(number) !== text) controls[row.key].input.value = String(number);
        draft.weights[row.key] = number;
        redrawShares();
        refreshSave();
      } });
      note[row.key] = h("div.t2");
      return h("div.prow", h("div.pfield", h("div.t1", { style: "font-size:15px" }, row.label), note[row.key]), controls[row.key].root);
    });

    save.addEventListener("click", async () => {
      const diff = diffTweaks(state.items, draft, state.common);
      if (!diff.add.length && !diff.off.length) return;
      status.clear();
      saveError = null;
      busy = true;
      save.textContent = "Сохраняю…";
      refreshSave();
      let failed = false;
      try {
        for (const tweak of diff.add) await api.post("/api/tweaks", tweak, { signal });
        for (const id of diff.off) await api.post(`/api/tweak/${encodeURIComponent(id)}/off`, {}, { signal });
        toast("Поправки сохранены: ваш балл пересчитывается");
      } catch (error) {
        if (error && error.name === "AbortError") return;
        failed = true;
        saveError = error;   // 422 — текст сервера; введённое остаётся в форме
      }
      await reload({ keepDraft: failed });
    });
    reset.addEventListener("click", async () => {
      const agreed = await confirmSheet({ title: "Сбросить личные поправки?", confirmText: "Сбросить",
        hint: "Все ваши поправки выключатся: балл и порядок ваших списков снова станут общими. Общий балл при этом не меняется." });
      if (!agreed) return;
      status.clear();
      saveError = null;
      reset.disabled = true;
      try {
        for (const t of state.items.filter((x) => x.state === "active")) await api.post(`/api/tweak/${encodeURIComponent(t.id)}/off`, {}, { signal });
        toast("Поправки сброшены: ваш балл снова общий");
        await reload();
      } catch (error) {
        if (error && error.name === "AbortError") return;
        status.show(error);
        reset.disabled = false;
      }
    });

    mount(mine, h("h2.t1", { id: "tw-mine-title" }, "Мои поправки"),
      h("p.t2", { style: "margin:4px 0 12px" }, "Слова и веса, по которым агент считает «мой балл». Общий балл они не меняют."),
      h("div.form", fields),
      h("h3.t1", { style: "font-size:15.5px;margin:18px 0 0" }, "Веса признаков"),
      h("p.t2", { style: "margin:2px 0 0" }, "Общий вес — как в портрете должности. Меняйте только то, что для вас важнее или неважно."),
      weightRowsEl,
      h("div.btns", { style: "margin-top:14px" }, save, reset), h("div", { style: "margin-top:8px" }, status.el),
      h("p.hint", { style: "margin:8px 0 0" }, SAVE_HINT), h("p.hint", { style: "margin:4px 0 0" }, RESET_HINT));
    redrawShares();
    refreshSave();
  }

  drawProposals();
  drawMine();
  return { proposals, mine };
}
