// Поле «чипсы со вводом»: ключевые слова, компании-доноры. Enter, запятая или потеря фокуса добавляют слово,
// «×» убирает, Backspace в пустом поле убирает последнее. Значения — только текст.
import { h, mount, uid } from "./dom.js";
import { splitChips } from "./screens/profiles-model.js";

export function chipsInput({ label, values = [], placeholder = "", hint = "", disabled = false }) {
  const id = uid("chips");
  const hintId = `${id}-hint`;
  let items = [...values];
  const listeners = [];
  const list = h("span.chiplist", { role: "list" });
  const input = h("input", { id, type: "text", placeholder, autocomplete: "off", disabled, "aria-describedby": hintId });
  const box = h("div.chipbox", { onClick: () => input.focus() }, list, input);

  const draw = () => {
    mount(list, items.map((item) => h("span.chip.removable", { role: "listitem" }, item,
      disabled ? null : h("button", { type: "button", "aria-label": `Убрать «${item}»`, onClick: (event) => { event.stopPropagation(); remove(item); } }, "×"))));
    listeners.forEach((fn) => fn(items));
  };
  function add(text) {
    let changed = false;
    for (const word of splitChips(text)) {
      if (!items.some((existing) => existing.toLowerCase() === word.toLowerCase())) {
        items.push(word);
        changed = true;
      }
    }
    input.value = "";
    if (changed) draw();
  }
  function remove(item) {
    items = items.filter((existing) => existing !== item);
    draw();
    input.focus();
  }
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === "," || event.key === ";") {
      event.preventDefault();
      add(input.value);
    } else if (event.key === "Backspace" && !input.value && items.length) {
      items = items.slice(0, -1);
      draw();
    }
  });
  input.addEventListener("input", () => { if (/[,;\n]/.test(input.value)) add(input.value); });   // вставка списка целиком
  input.addEventListener("blur", () => add(input.value));
  draw();

  return {
    root: h("div.field", h("label", { for: id }, label), box, hint ? h("div.hint", { id: hintId, style: "margin-top:5px" }, hint) : h("span", { id: hintId })),
    get values() { add(input.value); return [...items]; },
    onChange(fn) { listeners.push(fn); },
  };
}
