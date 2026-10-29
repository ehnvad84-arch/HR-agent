// Копирование в буфер обмена. Работает на https и на localhost (через SSH-туннель — это localhost).
import { COPY_FALLBACK } from "./contacts-model.js";

/** true — скопировано; false — буфер недоступен или браузер отказал: тогда текст выделяется вручную (selectText). */
export async function copyText(text) {
  if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** Выделить текст в поле или в обычном элементе, чтобы человек нажал Ctrl+C. */
export function selectText(node) {
  if (typeof node.select === "function") {
    node.focus();
    node.select();
    return;
  }
  const range = document.createRange();
  range.selectNodeContents(node);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}

export { COPY_FALLBACK };
