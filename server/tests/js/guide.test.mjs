// «Как работает портал» (S19): текст справки, таблица ролей и прав, «что доступно вам» по /api/me.permissions, открыт ли блок при первом входе
import assert from "node:assert/strict";
import test from "node:test";

import {
  CANNOT, CAN, GUIDE_ROLES, ROLE_PERMS, RULES, SECTIONS, guideKey, guideOpen, markGuideSeen, myRights, rightsTable,
} from "../../web/js/guide.js";
import { ROLE_TITLES } from "../../web/js/nav-model.js";

const can = (...perms) => (p) => perms.includes(p);
const ALL_TEXT = JSON.stringify({ SECTIONS, RULES, CAN, CANNOT });

test("разделы справки идут сверху вниз: что делает, кто что может, правила, умеет и не умеет", () => {
  assert.deepEqual(SECTIONS.map((s) => s.id), ["what", "roles", "rules", "can"]);
  assert.deepEqual(SECTIONS.map((s) => s.title), ["Что делает портал", "Кто что видит и может", "Правила", "Что портал умеет и чего не умеет"]);
  assert.ok(SECTIONS[0].paragraphs.length >= 3);
});

test("в таблице прав — четыре роли в порядке спеки и подписи, как в «Доступе»", () => {
  assert.deepEqual(GUIDE_ROLES, ["recruiter", "head_recruiter", "hiring_manager", "director"]);
  const table = rightsTable();
  assert.deepEqual(table.columns.map((c) => c.title), GUIDE_ROLES.map((r) => ROLE_TITLES[r]));
  assert.ok(table.rows.length >= 12);
  for (const row of table.rows) {
    assert.equal(row.cells.length, 4, row.title);
    assert.ok(row.title.length > 5, row.title);
    for (const cell of row.cells) assert.ok(cell.text.length > 0, row.title);
  }
});

test("таблица совпадает с матрицей прав из спеки: кто что может", () => {
  const byTitle = (part) => rightsTable().rows.find((r) => r.title.includes(part));
  const yes = (row) => row.cells.map((c) => c.yes);
  assert.deepEqual(yes(byTitle("Вся база")), [true, true, false, false]);
  assert.deepEqual(yes(byTitle("Открыть контакт")), [true, true, false, false]);
  assert.deepEqual(yes(byTitle("Загрузить резюме")), [true, true, false, false]);
  assert.deepEqual(yes(byTitle("Утвердить портрет")), [false, true, false, false]);
  assert.deepEqual(yes(byTitle("Выдать код")), [true, true, false, false]);
  assert.deepEqual(yes(byTitle("Роли и доступ")), [false, true, false, false]);
  assert.deepEqual(yes(byTitle("Передать")), [false, false, true, true]);
  assert.deepEqual(yes(byTitle("Решить по передачам")), [true, true, false, false]);
  assert.deepEqual(yes(byTitle("Журнал изменений")), [false, true, false, true]);   // report.read: у директора остаётся
  assert.deepEqual(yes(byTitle("Править портрет")), [true, true, true, true]);
  assert.deepEqual(yes(byTitle("мой балл")), [true, true, true, true]);
});

test("у руководителя и директора права названы со своей границей, без контактов", () => {
  const rows = rightsTable().rows;
  const base = rows.find((r) => r.title.includes("Вся база"));
  assert.match(base.cells[2].text, /нет/);
  const portrait = rows.find((r) => r.title.includes("Править портрет"));
  assert.match(portrait.cells[2].text, /своего подразделения/);
  assert.match(portrait.cells[3].text, /всей компании/);
  const mine = rows.find((r) => r.title.includes("Посмотреть людей"));
  assert.match(mine.cells[2].text, /своего подразделения/);
  assert.match(mine.cells[2].text, /без контактов/);
});

test("матрица прав: только отдел персонала видит всю базу и грузит резюме; руководители — нет", () => {
  for (const role of ["recruiter", "head_recruiter"]) {
    assert.ok(ROLE_PERMS[role].includes("base.all"), role);
    assert.ok(ROLE_PERMS[role].includes("collect.run"), role);
    assert.ok(ROLE_PERMS[role].includes("contact.open"), role);
  }
  for (const role of ["hiring_manager", "director"]) {
    for (const perm of ["base.all", "collect.run", "contact.open", "dossier.export", "person.take", "profile.approve", "access.manage"]) assert.equal(ROLE_PERMS[role].includes(perm), false, `${role} ${perm}`);
    assert.ok(ROLE_PERMS[role].includes("handoff.create"), role);
  }
  assert.ok(ROLE_PERMS.head_recruiter.includes("access.manage"));
  assert.equal(ROLE_PERMS.recruiter.includes("access.manage"), false);
  assert.equal(ROLE_PERMS.recruiter.includes("profile.approve"), false);
  assert.ok(ROLE_PERMS.director.includes("report.read"));
});

test("правила — с цифрами из спеки", () => {
  const text = RULES.join(" ");
  for (const part of ["200 знаков", "50 знаков", "72 часа", "500 просмотров", "18:00", "с понедельника на вторник", "платный контакт", "новая версия", "руководитель отдела персонала"]) {
    assert.ok(text.includes(part) || ALL_TEXT.includes(part), part);
  }
  assert.ok(RULES.length >= 9);
});

test("умеет и не умеет: главные границы названы прямо", () => {
  const cannot = CANNOT.join(" ").toLowerCase();
  for (const part of ["сам не пишет людям", "не открывает контакты без человека", "в обход", "не пишет в 1С", "бота"]) assert.ok(cannot.includes(part.toLowerCase()), part);
  assert.ok(CAN.length >= 5);
  assert.ok(CAN.join(" ").includes("балл считает программа"));
});

test("«Что доступно вам» — только из прав, которые пришли в /api/me", () => {
  assert.deepEqual(myRights(can()), []);
  const manager = myRights(can("base.read", "profile.edit", "handoff.create", "tweak.own", "feedback.write"));
  assert.ok(manager.length >= 3);
  assert.ok(manager.every((text) => !/контакт/.test(text) || /без контактов/.test(text)));
  const hr = myRights(can("base.read", "base.all", "contact.open", "collect.run", "access.manage"));
  assert.ok(hr.some((t) => /контакт/.test(t)));
  assert.ok(hr.some((t) => /Загрузить резюме/.test(t)));
  assert.ok(hr.some((t) => /роли и коды/.test(t) || /доступ/i.test(t)));
  assert.equal(myRights(can("settings.edit")).length, 1);
  assert.deepEqual(myRights(can("неведомое.право")), []);
});

test("блок открыт при первом входе и свёрнут потом; хранилище закрыто — не навязываем", () => {
  const store = new Map();
  const storage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)) };
  assert.equal(guideKey({ id: 7 }), "hrGuideSeen:7");
  assert.equal(guideOpen(storage, { id: 7 }), true);
  markGuideSeen(storage, { id: 7 });
  assert.equal(guideOpen(storage, { id: 7 }), false);
  assert.equal(guideOpen(storage, { id: 8 }), true);   // другой человек на этом компьютере — свой первый вход
  const broken = { getItem() { throw new Error("закрыто"); }, setItem() { throw new Error("закрыто"); } };
  assert.equal(guideOpen(broken, { id: 7 }), false);
  assert.doesNotThrow(() => markGuideSeen(broken, { id: 7 }));
  assert.equal(guideOpen(null, { id: 7 }), false);
});

test("S21: правила — недельная сводка замечаний по пятницам, в Телеграм и на почту, без имён кандидатов; личное обучение видит и руководитель", () => {
  const weekly = RULES.find((r) => r.includes("недельная сводка замечаний"));
  assert.ok(weekly, "правило про недельную сводку замечаний");
  for (const part of ["пятниц", "18:30", "по Москве", "Телеграм", "почту", "руководителю", "без имён кандидатов", "контактов"]) assert.ok(weekly.includes(part), part);
  const personal = RULES.find((r) => r.startsWith("Личное обучение"));
  assert.ok(/замечания.*видите вы, агент и руководитель — в недельной сводке/.test(personal), "замечания видит и руководитель — в недельной сводке");
  assert.ok(personal.includes("поправки"), "поправки остаются личными");
  assert.equal(RULES.join(" ").includes("видны вам и агенту"), false);
});
