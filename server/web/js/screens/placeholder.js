import { h, mount } from "../dom.js";

/** Заглушка вкладки, пока экран не сделан: заголовок и пояснение. */
export function placeholder(title) {
  return (box) => mount(box, h("div.card", h("div.t1", title), h("div.t2", "Экран появится в следующей задаче")));
}
