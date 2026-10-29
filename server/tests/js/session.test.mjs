// Сессия в памяти страницы: пользователь, права, токен CSRF (S1, S3)
import assert from "node:assert/strict";
import test from "node:test";

import { createSession } from "../../web/js/session.js";

test("пустая сессия: никто не вошёл, прав нет", () => {
  const s = createSession();
  assert.equal(s.user, null);
  assert.equal(s.csrf, null);
  assert.equal(s.can("base.read"), false);
});

test("после входа права берутся из ответа API", () => {
  const s = createSession();
  s.set({ user: { id: 2, login: "dev", full_name: "Разработка" }, permissions: ["base.read", "person.take"], csrf: "c" });
  assert.equal(s.can("base.read"), true);
  assert.equal(s.can("profile.approve"), false);
  assert.equal(s.user.login, "dev");
  assert.equal(s.csrf, "c");
});

test("clear стирает пользователя и токен", () => {
  const s = createSession();
  s.set({ user: { id: 1 }, permissions: ["base.read"], csrf: "c" });
  s.clear();
  assert.equal(s.user, null);
  assert.equal(s.can("base.read"), false);
  assert.equal(s.csrf, null);
});

test("подписчики узнают о смене пользователя", () => {
  const s = createSession();
  const seen = [];
  s.subscribe((u) => seen.push(u && u.id));
  s.set({ user: { id: 1 }, permissions: [], csrf: "a" });
  s.set({ user: { id: 1 }, permissions: [], csrf: "b" });
  s.clear();
  assert.deepEqual(seen, [1, 1, null]);
});

test("повторный set того же пользователя обновляет токен и права", () => {
  const s = createSession();
  s.set({ user: { id: 1 }, permissions: ["a"], csrf: "a" });
  s.set({ user: { id: 1 }, permissions: ["a", "b"], csrf: "z" });
  assert.equal(s.can("b"), true);
  assert.equal(s.csrf, "z");
});
