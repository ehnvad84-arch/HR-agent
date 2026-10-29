// Клиент API: CSRF, JSON, 401 -> окно входа -> повтор, ошибки по-русски (S1, S2, S35)
import assert from "node:assert/strict";
import test from "node:test";

import { ApiError, createClient } from "../../web/js/api.js";
import { createSession } from "../../web/js/session.js";

const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function setup({ replies, reauth } = {}) {
  const calls = [];
  const queue = [...replies];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    const r = queue.shift();
    if (r instanceof Error) throw r;
    if (typeof r === "function") return r(init);
    return r;
  };
  const session = createSession();
  session.set({ user: { id: 1, login: "dev", full_name: "Иван" }, permissions: ["base.read"], csrf: "tok1" });
  const client = createClient({ fetchImpl, session, reauth, timeoutMs: 200 });
  return { calls, client, session };
}

test("post шлёт JSON и X-CSRF-Token, cookie той же страницы", async () => {
  const { client, calls } = setup({ replies: [json(200, { ok: true })] });
  const data = await client.post("/api/decision", { person_id: 5, comment: "текст" });
  assert.deepEqual(data, { ok: true });
  const { url, init } = calls[0];
  assert.equal(url, "/api/decision");
  assert.equal(init.method, "POST");
  assert.equal(init.headers["X-CSRF-Token"], "tok1");
  assert.equal(init.headers["Content-Type"], "application/json");
  assert.equal(init.body, JSON.stringify({ person_id: 5, comment: "текст" }));
  assert.equal(init.credentials, "same-origin");
  assert.equal(init.cache, "no-store");
});

test("get собирает query без пустых значений и не шлёт тело", async () => {
  const { client, calls } = setup({ replies: [json(200, { items: [] })] });
  await client.get("/api/base", { query: { q: "схемотехник Altium", city: "", dept: null, page: 2 } });
  assert.equal(calls[0].url, "/api/base?q=%D1%81%D1%85%D0%B5%D0%BC%D0%BE%D1%82%D0%B5%D1%85%D0%BD%D0%B8%D0%BA+Altium&page=2");
  assert.equal(calls[0].init.body, undefined);
});

test("upload отправляет FormData без своего Content-Type, но с токеном", async () => {
  const { client, calls } = setup({ replies: [json(200, { job_doc_id: 3 })] });
  const form = new FormData();
  form.append("file", new Blob(["x"]), "di.docx");
  await client.upload("/api/profile/4/doc", form);
  assert.equal(calls[0].init.body, form);
  assert.equal(calls[0].init.headers["X-CSRF-Token"], "tok1");
  assert.equal(calls[0].init.headers["Content-Type"], undefined);
});

test("ошибка API превращается в ApiError с русским текстом и полями", async () => {
  const { client } = setup({ replies: [json(422, { error: "validation", message: "Проверьте введённые данные", fields: ["comment"] })] });
  await assert.rejects(client.post("/api/decision", {}), (e) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.status, 422);
    assert.equal(e.code, "validation");
    assert.equal(e.message, "Проверьте введённые данные");
    assert.deepEqual(e.fields, ["comment"]);
    return true;
  });
});

test("403 без csrf: «Нет прав на это действие»", async () => {
  const { client } = setup({ replies: [json(403, { error: "forbidden", message: "Недостаточно прав" })] });
  await assert.rejects(client.post("/api/x", {}), (e) => e.status === 403 && e.message === "Нет прав на это действие");
});

test("ответ без json (прокси, 502) даёт текст по статусу, а не разбор html", async () => {
  const { client } = setup({ replies: [new Response("<html>Bad Gateway</html>", { status: 502, headers: { "content-type": "text/html" } })] });
  await assert.rejects(client.get("/api/today"), (e) => e.status === 502 && !e.message.includes("<") && /[а-я]/i.test(e.message));
});

test("S35: сетевая ошибка — «Сервер недоступен, проверьте подключение»", async () => {
  const { client } = setup({ replies: [new TypeError("Failed to fetch")] });
  await assert.rejects(client.get("/api/today"), (e) => e.status === 0 && e.code === "network" && e.message === "Сервер недоступен, проверьте подключение");
});

test("зависший сервер: через таймаут — понятный текст", async () => {
  const hang = (init) => new Promise((_, reject) => init.signal.addEventListener("abort", () => reject(new DOMException("x", "AbortError"))));
  const { client } = setup({ replies: [hang] });
  await assert.rejects(client.get("/api/today"), (e) => e.code === "timeout" && /не отвечает/.test(e.message));
});

test("отмена вызывающим не превращается в сообщение об ошибке", async () => {
  const ctl = new AbortController();
  const hang = (init) => new Promise((_, reject) => init.signal.addEventListener("abort", () => reject(new DOMException("x", "AbortError"))));
  const { client } = setup({ replies: [hang] });
  const p = client.get("/api/today", { signal: ctl.signal });
  ctl.abort();
  await assert.rejects(p, (e) => e.name === "AbortError");
});

test("S2: 401 открывает окно входа, после входа запрос повторяется с новым токеном", async () => {
  let session;
  let reauthCalls = 0;
  const reauth = async () => { reauthCalls += 1; session.set({ user: { id: 1 }, permissions: ["base.read"], csrf: "tok2" }); };
  const s = setup({
    replies: [json(401, { error: "auth", message: "Войдите в рабочее место" }), json(200, { done: 1 })],
    reauth: () => reauth(),
  });
  session = s.session;
  const data = await s.client.post("/api/decision", { comment: "не потеряется" });
  assert.deepEqual(data, { done: 1 });
  assert.equal(reauthCalls, 1);
  assert.equal(s.calls.length, 2);
  assert.equal(s.calls[1].init.headers["X-CSRF-Token"], "tok2");
  assert.equal(s.calls[1].init.body, s.calls[0].init.body);
});

test("несколько запросов с 401 делят одно окно входа", async () => {
  let n = 0;
  let release;
  const gate = new Promise((r) => { release = r; });
  const s = setup({
    replies: [
      json(401, { error: "auth", message: "x" }), json(401, { error: "auth", message: "x" }),
      json(200, { a: 1 }), json(200, { b: 2 }),
    ],
    reauth: async () => { n += 1; await gate; },
  });
  const p1 = s.client.get("/api/a");
  const p2 = s.client.get("/api/b");
  await new Promise((r) => setTimeout(r, 10));
  release();
  await Promise.all([p1, p2]);
  assert.equal(n, 1);
});

test("повторный 401 после входа не зацикливается", async () => {
  const s = setup({ replies: [json(401, { error: "auth", message: "Войдите" }), json(401, { error: "auth", message: "Войдите" })], reauth: async () => {} });
  await assert.rejects(s.client.get("/api/a"), (e) => e.status === 401);
  assert.equal(s.calls.length, 2);
});

test("вход не открывает окно входа на 401: неверный пароль показывается в форме", async () => {
  let opened = false;
  const s = setup({ replies: [json(401, { error: "auth", message: "Неверный логин или пароль" })], reauth: async () => { opened = true; } });
  await assert.rejects(s.client.post("/api/login", { login: "a", password: "b" }, { noAuth: true }), (e) => e.message === "Неверный логин или пароль");
  assert.equal(opened, false);
});

test("403 csrf (токен сменился в другой вкладке): токен обновляется через /api/me и запрос повторяется один раз", async () => {
  const s = setup({
    replies: [
      json(403, { error: "csrf", message: "Запрос отклонён: обновите страницу и повторите" }),
      json(200, { user: { id: 1 }, permissions: ["base.read"], csrf: "tok9" }),
      json(200, { ok: true }),
    ],
  });
  assert.deepEqual(await s.client.post("/api/decision", {}), { ok: true });
  assert.equal(s.calls[1].url, "/api/me");
  assert.equal(s.calls[2].init.headers["X-CSRF-Token"], "tok9");
  assert.equal(s.session.csrf, "tok9");
});

test("403 csrf повторяется не больше одного раза", async () => {
  const csrf = () => json(403, { error: "csrf", message: "Запрос отклонён" });
  const s = setup({ replies: [csrf(), json(200, { user: { id: 1 }, permissions: [], csrf: "t" }), csrf()] });
  await assert.rejects(s.client.post("/api/x", {}), (e) => e.code === "csrf");
  assert.equal(s.calls.length, 3);
});

test("get blob: файл выгрузки приходит вместе с именем из заголовка", async () => {
  const res = new Response(new Blob(["docx"]), { status: 200, headers: { "content-type": "application/octet-stream", "content-disposition": "attachment; filename=\"dossier-5.docx\"" } });
  const { client, calls } = setup({ replies: [res] });
  const out = await client.get("/api/person/5/export", { query: { f: "docx" }, as: "blob" });
  assert.equal(out.filename, "dossier-5.docx");
  assert.equal(await out.blob.text(), "docx");
  assert.equal(calls[0].init.headers["X-CSRF-Token"], "tok1");
});
