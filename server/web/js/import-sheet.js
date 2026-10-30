// Окно выгрузки из 1С: «Взять последнюю выгрузку 1С» из обменного каталога или «Выбрать файл выгрузки…» (.json до 5 МБ).
// Общее для оргструктуры («База должностей», S47) и сотрудников («Доступ», S1). Ошибки сервера (404, 422) — текстом в окне.
import { h } from "./dom.js";
import { errorLine, sheet, toast } from "./ui.js";

/**
 *   syncText — строка «Последняя выгрузка …» над кнопками; refreshPath / uploadPath — POST без тела и multipart (поле file);
 *   resultText(reply) — текст тоста; fileError(file) — проверка файла до отправки (null — можно); onDone(reply) — после успеха.
 */
export function openImportSheet({ api, signal, title, hint, syncText, refreshPath, uploadPath, resultText, fileError, onDone }) {
  const win = sheet({ title, width: "520px", hint });
  const status = errorLine();
  status.el.classList.add("errtint");   // ошибка файла — на красной плашке, текст сервера
  const fileInput = h("input", { type: "file", accept: ".json,application/json", hidden: true, "aria-label": "Файл выгрузки 1С" });
  let busy = false;
  const buttons = [];
  const run = async (request) => {
    if (busy) return;
    status.clear();
    busy = true;
    buttons.forEach((b) => { b.disabled = true; });
    try {
      const reply = await request();
      win.close("ok");
      toast(resultText(reply));
      onDone(reply);
    } catch (error) {
      if (error && error.name === "AbortError") { win.close("cancel"); return; }
      status.show(error);
      busy = false;
      buttons.forEach((b) => { b.disabled = false; });
    }
  };
  const take = h("button.b.fill", { type: "button", onClick: () => run(() => api.post(refreshPath, {}, { signal, timeoutMs: 60000 })) }, "Взять последнюю выгрузку 1С");
  const pick = h("button.link", { type: "button", onClick: () => fileInput.click() }, "Выбрать файл выгрузки…");
  buttons.push(take, pick);
  fileInput.addEventListener("change", () => {
    const chosen = fileInput.files && fileInput.files[0];
    const problem = fileError(chosen);
    if (problem) {
      status.show(problem);
      fileInput.value = "";
      return;
    }
    const data = new FormData();
    data.append("file", chosen);
    run(() => api.upload(uploadPath, data, { signal, timeoutMs: 60000 }));
  });
  win.body.append(h("p.t2", { style: "margin:0 0 12px" }, syncText), h("div.btns", { style: "margin-top:0" }, take),
    h("p", { style: "margin:12px 0 0" }, pick), fileInput, status.el,
    h("div.sheetfoot.end", h("button.b", { type: "button", onClick: () => win.close("cancel") }, "Отмена")));
  win.show(take);
  return win.closed;
}
