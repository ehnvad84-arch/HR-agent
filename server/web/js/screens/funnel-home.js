// «Воронка» без вакансии у руководителя отдела персонала: блок «Ждут утверждения — наборы этапов» (S4), справочник этапов,
// открытые вакансии. Отчёты рекрутера и отдела (S12, S13) добавляются сюда же — funnel-report T14.
import { h } from "../dom.js";
import { chip } from "../people.js";
import { hashFor } from "../router.js";
import { emptyCard, mountAsync } from "../ui.js";
import { approvalsZone, loadPending } from "./stage-approvals.js";
import { pendingBadge } from "./stage-approvals-model.js";
import { openText, stageChips } from "./vacancies-model.js";

function vacancyRow(v) {
  const chips = stageChips(v).map((c) => chip(c.text, c.tone));
  return h("a.li", { href: hashFor("funnel", { id: v.id }) },
    h("div.grow", h("div", { style: "font-weight:600" }, v.title), h("div.sub", openText(v))), chips);
}

export function render(box, ctx) {
  const { api, signal } = ctx;
  const draw = ({ lists, entries }) => {
    ctx.setBadge("funnel", pendingBadge(lists));
    const open = lists.open || [];
    return [
      h("div.card", { style: "margin-bottom:12px" }, h("div.row",
        h("div.grow", h("h2.t1", "Воронка"), h("div.t2", "Отметки «прошёл / не прошёл» по вакансиям; наборы этапов, которые ждут вашего решения.")),
        h("div.btns", { style: "margin:0" }, h("button.b", { type: "button", onClick: () => ctx.go("stages") }, "Справочник этапов")))),
      approvalsZone({ api, signal, entries, go: ctx.go, onChange: (left) => ctx.setBadge("funnel", left || "") }),
      h("h2.sechead", "Открытые вакансии"),
      open.length ? h("div.list", open.map(vacancyRow)) : emptyCard("Открытых вакансий нет", h("a.link", { href: "#/vacancies" }, "Перейти к «Вакансиям»")),
    ];
  };
  return mountAsync(box, () => loadPending(api, signal), draw, { signal });
}
