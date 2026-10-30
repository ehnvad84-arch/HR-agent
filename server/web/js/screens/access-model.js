// «Доступ» без DOM (S1–S3): пользователи, роли, «вручную», вход, выгрузка сотрудников из 1С, выдача одноразового кода.
// Данные — GET /api/access/users: {items: [{id, login, full_name, position, org_unit_id, org_unit, role, manual, last_login_at,
// code_expires_at, is_active, has_password}], sync: {received_at, new, changed, fired} | null}.
import { ROLE_TITLES } from "../nav-model.js";

export const ROLE_ORDER = ["recruiter", "head_recruiter", "hiring_manager", "director", "no_access"];
export const roleText = (role) => ROLE_TITLES[role] || String(role ?? "");

const MANAGER_ROLES = new Set(["hiring_manager", "director", "head_recruiter"]);
const fold = (text) => String(text ?? "").toLowerCase().replace(/ё/g, "е").replace(/\s+/g, " ").trim();
const pad = (n) => String(n).padStart(2, "0");
const toDate = (value) => {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
};
const ddmm = (date) => `${pad(date.getDate())}.${pad(date.getMonth() + 1)}`;
const ddmmHm = (date) => `${ddmm(date)} ${pad(date.getHours())}:${pad(date.getMinutes())}`;

/** «Вход»: «уволен» / «код до 31.10 14:00» / «входил 29.10» / «не входил». */
export function loginStatus(user, now = new Date()) {
  if (!user.is_active) return "уволен";
  const code = user.code_expires_at ? toDate(user.code_expires_at) : null;
  if (code && code > now) return `код до ${ddmmHm(code)}`;
  const last = user.last_login_at ? toDate(user.last_login_at) : null;
  return last ? `входил ${ddmm(last)}` : "не входил";
}

/** «Последняя выгрузка 30.10 06:00 · новых 3, изменено 5, уволено 1». */
export function syncLine(sync) {
  const at = sync && sync.received_at ? toDate(sync.received_at) : null;
  if (!at) return "Выгрузок сотрудников из 1С ещё не было";
  return `Последняя выгрузка ${ddmmHm(at)} · новых ${sync.new ?? 0}, изменено ${sync.changed ?? 0}, уволено ${sync.fired ?? 0}`;
}

export function resultText(reply) {
  if (reply && reply.state === "same") return "Эта выгрузка уже применена";
  const r = reply || {};
  return `Выгрузка сотрудников применена: новых ${r.new ?? 0}, изменено ${r.changed ?? 0}, уволено ${r.fired ?? 0}`;
}

/** Поиск по ФИО, логину, должности и подразделению (без регистра и «ё/е»); фильтр по роли и «только руководители». */
export function filterUsers(items, { q = "", role = "", managersOnly = false } = {}) {
  const needle = fold(q);
  return (items || []).filter((u) => (!role || u.role === role) && (!managersOnly || MANAGER_ROLES.has(u.role))
    && (!needle || [u.full_name, u.login, u.position, u.org_unit].some((value) => fold(value).includes(needle))));
}

export const sortUsers = (items) => [...(items || [])].sort((a, b) => String(a.full_name).localeCompare(String(b.full_name), "ru"));

/** Подразделения для выбора — деревом по parent_id, в порядке 1С, с отступом; пустые тоже (должность можно дать и в «пустом»). */
export function unitOptions(units) {
  const list = Array.isArray(units) ? units : [];
  const ids = new Set(list.map((u) => u.id));
  const children = new Map();
  for (const u of list) {
    const parent = u.parent_id !== null && u.parent_id !== undefined && ids.has(u.parent_id) && u.parent_id !== u.id ? u.parent_id : null;
    if (!children.has(parent)) children.set(parent, []);
    children.get(parent).push(u);
  }
  const out = [];
  const seen = new Set();
  const visit = (parent, depth) => {
    for (const u of children.get(parent) || []) {
      if (seen.has(u.id)) continue;   // цикл в данных
      seen.add(u.id);
      out.push({ id: u.id, depth, title: u.title, label: `${"  ".repeat(depth)}${u.title}` });
      visit(u.id, depth + 1);
    }
  };
  visit(null, 0);
  return out;
}

/** Что изменилось в окне «Доступ: ФИО» (S2): только изменённое; reset — «вернуть правило 1С» (manual: false). */
export function changesFor(user, form) {
  if (form.reset) return user.manual ? { manual: false } : {};
  const changes = {};
  if (form.role !== user.role) changes.role = form.role;
  const unit = form.org_unit_id === "" || form.org_unit_id === null || form.org_unit_id === undefined ? null : Number(form.org_unit_id);
  if (unit !== (user.org_unit_id ?? null)) changes.org_unit_id = unit;
  return changes;
}

export function newUserProblem(form) {
  const login = String(form.login ?? "").trim();
  if (!login) return "Введите логин";
  if (!/^[A-Za-z0-9._-]+$/.test(login)) return "Логин — латиницей, цифрами, точкой, дефисом и подчёркиванием";
  if (!String(form.full_name ?? "").trim()) return "Введите ФИО";
  if (!form.role) return "Выберите роль";
  return null;
}

export const newUserBody = (form) => ({
  login: String(form.login).trim(), full_name: String(form.full_name).trim(), position: String(form.position ?? "").trim(),
  org_unit_id: form.org_unit_id === "" || form.org_unit_id === null || form.org_unit_id === undefined ? null : Number(form.org_unit_id), role: form.role,
});

/** Код выдаётся только действующему человеку с ролью (сервер ответит 409 иначе). */
export function canIssueCode(user) {
  if (!user.is_active) return { ok: false, reason: "Человек уволен: код выдать нельзя" };
  if (user.role === "no_access") return { ok: false, reason: "У человека нет доступа: сначала выберите роль" };
  return { ok: true, reason: "" };
}

/** Код читается по знакам: «K 7 M 2 …» (скринридеру), на экране — группами по пять. */
export const codeAriaLabel = (code) => [...String(code ?? "")].join(" ");
export const groupCode = (code) => String(code ?? "").replace(/(.{5})(?=.)/g, "$1 ");
export function codeExpiryText(expiresAt) {
  const date = toDate(expiresAt);
  return date ? `Действует до ${ddmmHm(date)}` : "";
}
