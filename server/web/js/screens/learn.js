// «Обучение агента» (S29–S31, S41; П:426–464): чат с тегами, недельная сводка с анкетой, решения руководителя.
// Агент по сообщению ничего не меняет: оно попадает в сводку, решает руководитель, принятое применяет ночная задача apply_digest.
// Всё из API выводится только текстом (h(), textContent).
import { h, mount, uid } from "../dom.js";
import { absoluteDay, whenLabel } from "../format.js";
import { chip, templateChip } from "../people.js";
import { confirmSheet, emptyCard, errorLine, field, mountAsync, toast } from "../ui.js";
import {
  TAGS, canDecide, canSend, chatItems, counterText, decisionInfo, digestIntro, targetText, weekTitle,
} from "./learn-model.js";

const PLACEHOLDER = "Например: в выдачу по продажам третий день попадают люди из розницы. Ожидал, что стоп-фактор «нет опыта B2B» их отсечёт.";
const CHANGELOG_DAYS = 90;

const isoDay = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;

// ------------------------------------------------------------------ чат

function bubble(item) {
  return h("div.msg", { class: item.kind === "agent" ? "agent" : null },
    h("div.who", { "aria-hidden": "true" }, item.initials),
    h("div.bubble", h("div.meta", h("b", item.author), h("span", whenLabel(item.at)),
      item.kind === "agent" ? chip("принято", "g") : chip(item.tagLabel, "b"),
      item.byTemplate ? templateChip("по шаблону") : null), item.text));
}

// ------------------------------------------------------------------ сводка

function proposalCard(proposal, env) {
  const info = decisionInfo(proposal);
  const status = errorLine();
  const noteId = uid("note");
  const note = h("textarea", { id: noteId, rows: 2, "data-tip": "digest-note", placeholder: "Например: обсудим на планёрке", style: "min-height:0" });
  const buttons = [];

  const decide = async (decision) => {
    status.clear();
    if (decision === "accepted") {
      const agreed = await confirmSheet({ title: "Принять предложение?", confirmText: "Принять",
        hint: "Изменение применится ночью (задача apply_digest) и запишется в журнал изменений с вашим именем и датой." });
      if (!agreed) return;
    }
    buttons.forEach((b) => { b.disabled = true; });
    try {
      await env.api.post(`/api/digest/${encodeURIComponent(proposal.id)}/decide`, { decision, note: note.value.trim() || null }, { signal: env.signal });
      toast({ accepted: "Принято: применится ночью", declined: "Предложение отклонено", discuss: "Помечено «обсудить»" }[decision]);
      await env.refreshDigest();
    } catch (error) {
      if (error && error.name === "AbortError") return;
      if (error && error.status === 409) {   // решение уже принято (другой вкладкой, другим человеком): показываем причину и актуальную карточку
        toast(error.message);
        await env.refreshDigest();
        return;
      }
      status.show(error);
      buttons.forEach((b) => { b.disabled = false; });
    }
  };

  if (env.canDecide && canDecide(proposal.decision)) {
    buttons.push(h("button.b.fill", { type: "button", "data-tip": "digest-accept", onClick: () => decide("accepted") }, "Принять"),
      h("button.b.danger", { type: "button", "data-tip": "digest-decline", onClick: () => decide("declined") }, "Отклонить"),
      h("button.b.blue", { type: "button", "data-tip": "digest-discuss", onClick: () => decide("discuss") }, "Обсудить"));
  }
  return h("div.card.inset", { style: "margin-bottom:10px" },
    h("div.t1", { style: "font-size:15.5px" }, proposal.title),
    h("div.t2", `Основание: ${proposal.rationale}`),
    h("div.t2", `Что изменится: ${targetText(proposal.target, env.profileTitles)}`),
    h("div.t2", { style: "color:var(--ink2);margin-top:6px" }, h("b", "Вопрос руководителю: "), proposal.question),
    buttons.length ? h("div.field", { style: "margin-top:10px" }, h("label", { for: noteId }, "Заметка (необязательно)"), note) : null,
    buttons.length ? h("div.btns", buttons) : null,
    h("div.t2", { style: "margin-top:10px" }, chip(info.state === "pending" ? "ждёт решения" : info.state === "accepted" ? "принято" : info.state === "declined" ? "отклонено" : "обсудить", info.tone), " ", info.text),
    info.note ? h("div.t2", `Заметка: ${info.note}`) : null,
    status.el);
}

// ------------------------------------------------------------------ экран

export function render(box, ctx) {
  const { api, signal, session } = ctx;
  const canWrite = session.can("feedback.write");
  const env = { api, signal, canDecide: session.can("digest.decide"), profileTitles: {}, refreshDigest: null };

  const load = async () => {
    const [feed, digest] = await Promise.all([api.get("/api/feedback", { signal }), api.get("/api/digest/current", { signal })]);
    // необязательное: названия профилей для текста «что изменится» и журнал внедрённого; нет права или сбой — экран работает без них
    const [profiles, changelog] = await Promise.all([
      api.get("/api/profiles", { signal }).catch(() => null),
      api.get("/api/changelog", { query: { from: isoDay(new Date(Date.now() - CHANGELOG_DAYS * 86400000)) }, signal }).catch(() => null),
    ]);
    return { feed: feed.items || [], digest, profiles: profiles && profiles.items, changelog: changelog && changelog.items };
  };

  const view = (data) => {
    env.profileTitles = Object.fromEntries((data.profiles || []).map((p) => [p.code, p.title]));
    ctx.setBadge("learn", data.feed.length || "");

    // ---------------- чат
    const chat = h("div.chat", { role: "log", "aria-live": "polite", "aria-label": "Лента чата обучения агента", tabindex: 0 });
    const drawChat = (feed) => {
      const items = chatItems(feed);
      mount(chat, items.length ? items.map(bubble) : h("p.t2", "Сообщений пока нет"));
      chat.scrollTop = chat.scrollHeight;
    };
    drawChat(data.feed);

    // ---------------- композитор: поле и счётчик живут между обновлениями ленты, введённое не теряется
    let tag = "remark";
    const counterId = uid("counter");
    const message = field({ label: "Сообщение агенту", kind: "textarea", name: "message", placeholder: PLACEHOLDER, describedBy: counterId, tip: "feedback" });
    message.input.style.minHeight = "86px";
    const counter = h("span.counter.bad", { id: counterId }, counterText(""));
    const status = errorLine();
    const send = h("button.b.fill", { type: "submit", disabled: true, style: "margin-left:auto" }, "Отправить агенту");
    const tagButtons = TAGS.map(([code, label]) => h("button", { type: "button", "aria-pressed": code === tag, "data-tip": label,
      onClick: () => { tag = code; tagButtons.forEach((b, i) => b.setAttribute("aria-pressed", String(TAGS[i][0] === tag))); } }, label));
    let busy = false;
    const update = () => {
      counter.textContent = counterText(message.input.value);
      counter.classList.toggle("bad", !canSend(message.input.value));
      send.disabled = busy || !canSend(message.input.value);
    };
    message.input.addEventListener("input", update);
    const composer = h("form.composer", { novalidate: true, onSubmit: async (event) => {
      event.preventDefault();
      const text = message.input.value.trim();
      if (!canSend(text) || busy) return;
      status.clear();
      busy = true;
      send.textContent = "Отправляю…";
      update();
      try {
        await api.post("/api/feedback", { body: text, tag }, { signal });
        message.input.value = "";
        toast("Отправлено агенту");
        const fresh = await api.get("/api/feedback", { signal });
        drawChat(fresh.items || []);
        ctx.setBadge("learn", (fresh.items || []).length || "");
        message.input.focus();
      } catch (error) {
        if (error && error.name === "AbortError") return;
        status.show(error);   // 422 «Проверьте: …»; текст остаётся в поле
      } finally {
        busy = false;
        send.textContent = "Отправить агенту";
        update();
      }
    } }, h("div.tagseg", { role: "group", "aria-label": "Тип сообщения" }, tagButtons), message.root,
    h("div", { style: "display:flex;align-items:center;gap:10px" }, counter, send), status.el);

    // ---------------- сводка
    const digestBox = h("div");
    const mailStatus = errorLine();
    const drawDigest = (digest) => {
      const proposals = digest.proposals || [];
      if (!proposals.length) {
        mount(digestBox, h("div.card", h("div.t1", "Сводка собирается по пятницам"),
          h("div.t2", "Пока предложений нет: агент соберёт их из похожих сообщений чата и пришлёт руководителю отдела персонала с анкетой. Решение всегда за человеком.")));
        return;
      }
      mount(digestBox, h("div.card", h("div.t1", weekTitle(digest.period_from, digest.period_to)), h("div.t2", digestIntro(digest)),
        h("div", { style: "margin-top:14px" }, proposals.map((p) => proposalCard(p, env))),
        env.canDecide ? h("div.btns", h("button.b", { type: "button", "data-tip": "digest-mail", onClick: async (event) => {
          const button = event.currentTarget;
          mailStatus.clear();
          button.disabled = true;
          try {
            const reply = await api.post("/api/digest/current/send", {}, { signal });
            toast(`Сводка отправлена: ${(reply.sent_to || []).join(", ") || "получателям"}`);
          } catch (error) {
            if (error && error.name === "AbortError") return;
            mailStatus.show(error);   // 503 «почта не настроена», 502 «почтовый сервер не ответил»
          } finally {
            button.disabled = false;
          }
        } }, "Отправить на почту")) : null,
        mailStatus.el,
        h("div.hint", { style: "margin-top:10px" }, "Принятое применится ночью (задача apply_digest): каждое изменение запишется в журнал с именем руководителя и датой.")));
    };
    drawDigest(data.digest);
    env.refreshDigest = async () => {
      try {
        const fresh = await api.get("/api/digest/current", { signal });
        if (!signal.aborted) drawDigest(fresh);
      } catch (error) {
        if (!(error && error.name === "AbortError")) toast(error && error.message ? error.message : "Не удалось обновить сводку");
      }
    };

    // ---------------- что уже внедрено
    const applied = (data.changelog || []).filter((c) => c.proposal_id);
    const appliedList = data.changelog === null || data.changelog === undefined ? null
      : applied.length ? h("div.list", applied.map((c) => h("div.li", { style: "cursor:default" },
        h("div", h("div", { style: "font-weight:600" }, `${absoluteDay(c.applied_at)} · ${c.note}`),
          h("div.sub", c.author ? `Решение руководителя по сводке · ${c.author}` : "Решение руководителя по сводке")))))
        : h("p.hint", { style: "margin:0 6px" }, "Пока ничего не внедрено.");

    return h("div.split",
      h("div", h("h2.sechead", "Чат обучения агента"), h("div.card",
        h("div.t2", { style: "margin-bottom:12px" }, "Сюда рекрутеры пишут замечания, рекомендации и критику. Агент накапливает всё, что вы написали, ищет повторяющееся и раз в неделю присылает руководителю отдела персонала сводку с предложениями и анкетой. Решение принимает руководитель, после этого изменение попадает в работу системы."),
        chat, canWrite ? composer : h("p.hint", { style: "margin:12px 0 0" }, "Писать агенту можно с правом «писать в чат обучения»: у вашей роли его нет, лента открыта для чтения."))),
      h("div", h("h2.sechead", "Сводка для руководителя отдела персонала"), digestBox,
        appliedList ? [h("h2.sechead", "Что уже внедрено"), appliedList] : null));
  };

  return mountAsync(box, load, view, { signal });
}
