// Вес признака в портрете должности (S26): компактный степпер «− 20 +», подпись «вес» и мини-полоска доли от суммы весов.
// Источник правды — само поле (input.value): форма читает его как раньше. Значения — только текст.
import { h } from "./dom.js";
import { stepWeight } from "./screens/profiles-model.js";

/**
 *   const w = weightControl({ key: "keywords", label: "Ключевые слова и инструменты", value: "25", disabled: false, onInput: redraw });
 *   w.root — разметка; w.input — поле (значение строкой); w.share(percent) — обновить полоску и «% суммы».
 */
export function weightControl({ key, label, value = "0", disabled = false, onInput = () => {} }) {
  const input = h("input.wnum", { type: "text", inputmode: "numeric", maxlength: 3, name: `w_${key}`, value, disabled, autocomplete: "off",
    "aria-label": `Вес: ${label}`, "data-tip": "weight" });
  const bar = h("i");
  const percent = h("span.wshare");
  const step = (direction) => {
    input.value = stepWeight(input.value, direction);
    onInput();
  };
  input.addEventListener("input", () => {
    const digits = input.value.replace(/\D/g, "");   // буквам и знакам в поле веса не место
    if (digits !== input.value) input.value = digits;
    onInput();
  });
  const minus = h("button.wbtn", { type: "button", disabled, "aria-label": `Уменьшить вес: ${label}`, "data-tip": "weight-minus", onClick: () => step(-1) }, "−");
  const plus = h("button.wbtn", { type: "button", disabled, "aria-label": `Увеличить вес: ${label}`, "data-tip": "weight-plus", onClick: () => step(1) }, "+");
  return {
    input,
    root: h("div.wgt", h("span.wcap", "вес"), h("div.wstep", minus, input, plus),
      h("div.wmeta", h("div.wbar", { "aria-hidden": "true" }, bar), percent)),
    share(value) {
      bar.style.width = `${Math.max(0, Math.min(100, value))}%`;
      percent.textContent = `${value}% суммы`;
    },
  };
}
