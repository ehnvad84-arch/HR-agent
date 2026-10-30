// Вход по одноразовому коду (S3): проверка до отправки и тексты ошибок
import assert from "node:assert/strict";
import test from "node:test";

import { BAD_CODE_TEXT, MAX_PASSWORD_BYTES, MIN_PASSWORD, normalizeCode, passwordProblem, passwordBytes } from "../../web/js/login-model.js";

test("код из отдела персонала: пробелы и дефисы при вводе не мешают", () => {
  assert.equal(normalizeCode(" ABCD2 345EF "), "ABCD2345EF");
  assert.equal(normalizeCode("ABCD-2345-EF"), "ABCD2345EF");
  assert.equal(normalizeCode("ab cd ef"), "abcdef");   // регистр не трогаем: сравнивает сервер
  assert.equal(normalizeCode(""), "");
  assert.equal(normalizeCode(null), "");
});

test("S3: новый пароль — не короче 8, оба раза одинаково, не длиннее 72 байт", () => {
  assert.equal(MIN_PASSWORD, 8);
  assert.equal(MAX_PASSWORD_BYTES, 72);
  assert.equal(passwordProblem("хорошийпароль1", "хорошийпароль1"), null);
  assert.deepEqual(passwordProblem("короткий", "короткий"), null);   // 8 знаков — ровно порог
  assert.deepEqual(passwordProblem("коротк", "коротк"), { field: "password", text: "Не короче 8 знаков" });
  assert.deepEqual(passwordProblem("", ""), { field: "password", text: "Не короче 8 знаков" });
  assert.deepEqual(passwordProblem("хорошийпароль1", "хорошийпароль2"), { field: "repeat", text: "Пароли не совпадают" });
  assert.deepEqual(passwordProblem("a".repeat(73), "a".repeat(73)), { field: "password", text: "Слишком длинный пароль: не больше 72 байт" });
  // кириллица — два байта на знак: 37 знаков уже 74 байта
  assert.equal(passwordBytes("а".repeat(36)), 72);
  assert.equal(passwordProblem("а".repeat(36), "а".repeat(36)), null);
  assert.equal(passwordProblem("а".repeat(37), "а".repeat(37)).field, "password");
});

test("S3: код не подошёл по любой причине — один текст, без подсказки, что именно не так", () => {
  assert.equal(BAD_CODE_TEXT, "Логин или код не подходят. Код мог истечь — попросите новый в отдел персонала");
});
