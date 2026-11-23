// «Воронка» без вакансии у руководителя отдела персонала: «Ждут утверждения — наборы этапов» (S4, T13), справочник этапов и отчёты
// отдела и рекрутеров за год (S12, S13, T14 — funnel-overview.js).
import { approvalsZone, loadPending } from "./stage-approvals.js";
import { overview } from "./funnel-overview.js";
import { pendingBadge } from "./stage-approvals-model.js";
import { mountAsync } from "../ui.js";

export function render(box, ctx) {
  const { api, signal } = ctx;
  const draw = ({ lists, entries }) => {
    ctx.setBadge("funnel", pendingBadge(lists));
    const zone = approvalsZone({ api, signal, entries, go: ctx.go, onChange: (left) => ctx.setBadge("funnel", left || "") });
    return overview({ api, signal, session: ctx.session, go: ctx.go, query: ctx.query, between: zone });
  };
  return mountAsync(box, () => loadPending(api, signal), draw, { signal });
}
