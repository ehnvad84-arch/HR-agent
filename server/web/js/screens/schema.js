// «Схема» (S34; П:486–546): путь данных от площадки до рекрутера с живыми цифрами базы и таблица «Последние прогоны»
// фоновых задач из ops.job_run. Пока идёт задача (например пересчёт после утверждения профиля), экран сам перечитывает данные.
// Всё из API выводится только текстом (h(), textContent).
import { h, mount } from "../dom.js";
import { timeLabel } from "../format.js";
import { EXPECT_MS, takeExpectation } from "../hh-load-model.js";
import { loadResumesButton } from "../hh-load.js";
import { chip } from "../people.js";
import { mountAsync, toast } from "../ui.js";
import { countTiles, hasRunning, jobRows, runChanged } from "./schema-model.js";
import { schemeSvg } from "./schema-svg.js";

const POLL_MS = 3000;   // как часто перечитывать, пока что-то идёт
const WAIT_MS = 1000;   // после ручного запуска сбора: как часто смотреть, появился ли прогон (он стартует в фоне, чуть позже ответа)
const startedAt = (jobs, job) => ((jobs || []).find((row) => row.job === job) || {}).started_at ?? null;

function runRow(run) {
  const facts = run.facts.length ? h("div", run.facts.join(", ")) : run.empty ? h("div.t2", { style: "margin:0" }, run.empty) : null;
  return h("tr", { class: run.never ? "never" : null },
    h("td.name", h("div", run.title), h("div.runcode", run.job)),
    h("td", run.when || "—"),
    h("td", run.duration || "—"),
    h("td", chip(run.statusText, run.tone)),
    h("td.runfacts", facts, run.note ? h("div.t2", { style: "margin:2px 0 0" }, run.note) : null,
      run.error ? h("div", { class: run.status === "error" ? "formerror" : "hint", style: "margin-top:2px" }, run.error) : null));
}

export function render(box, ctx) {
  const { api, signal } = ctx;
  let timer = null;
  let running = new Set();   // задачи, которые шли при прошлом обновлении: когда закончатся — скажем об этом
  let waiting = null;        // {job, until, baseline}: человек запустил сбор, ждём, когда прогон появится в ops.job_run
  const wait = (found, jobs) => { waiting = found ? { job: found.job, until: Date.now() + EXPECT_MS, baseline: startedAt(jobs, found.job) } : null; };

  const load = () => api.get("/api/schema", { signal });

  const view = (data) => {
    const tilesBox = h("div.livecounts", { role: "group", "aria-label": "Объём базы сейчас" });
    const svgBox = h("div");
    const tbody = h("tbody");
    const updated = h("span.hint");

    let lastJobs = data.jobs;
    const draw = (fresh) => {
      const now = new Date();
      lastJobs = fresh.jobs;
      mount(tilesBox, countTiles(fresh.counts).map((tile) => h("div.livecount", { "data-tip": `schema-${tile.key}` },
        h("div.n", tile.value), h("div.l", tile.label))));
      mount(svgBox, schemeSvg(fresh.counts));
      mount(tbody, jobRows(fresh.jobs, now).map(runRow));
      updated.textContent = `Обновлено в ${timeLabel(now)}`;

      // ход: что шло и закончилось — сообщаем; пока что-то идёт — перечитываем через несколько секунд
      const nowRunning = new Set((fresh.jobs || []).filter((j) => j.finished_at === null || j.finished_at === undefined).map((j) => j.job));
      for (const row of jobRows(fresh.jobs, now)) {
        if (running.has(row.job) && !nowRunning.has(row.job)) toast(`${row.title}: ${row.statusText}`);
      }
      running = nowRunning;
      if (waiting && (runChanged(fresh.jobs, waiting.job, waiting.baseline) || nowRunning.has(waiting.job) || Date.now() > waiting.until)) waiting = null;
      clearTimeout(timer);
      if (hasRunning(fresh.jobs, now)) timer = setTimeout(() => refresh({ quiet: true }), POLL_MS);
      else if (waiting) timer = setTimeout(() => refresh({ quiet: true }), WAIT_MS);
    };

    const refresh = async ({ quiet = false } = {}) => {
      try {
        const fresh = await api.get("/api/schema", { signal });
        if (!signal.aborted) draw(fresh);
      } catch (error) {
        if (error && error.name === "AbortError") return;
        if (!quiet) toast(error && error.message ? error.message : "Не удалось обновить схему");
        else timer = setTimeout(() => refresh({ quiet: true }), POLL_MS * 3);   // сеть моргнула: попробуем позже
      }
    };
    signal.addEventListener("abort", () => clearTimeout(timer), { once: true });

    wait(takeExpectation(), data.jobs);   // сюда перешли с «Базы» после «Загрузить резюме»
    draw(data);

    // «Загрузить резюме» — только с правом settings.edit; после запуска ход виден в строке «Сбор с hh»
    const loadButton = loadResumesButton({ api, signal, session: ctx.session, variant: "fill",
      onStarted: () => { wait({ job: "collect_hh" }, lastJobs); refresh({ quiet: true }); } });

    return [
      tilesBox,
      h("div.card", { style: "margin-top:14px" }, h("div.t1", { style: "margin-bottom:12px" }, "Путь данных: от площадки до рекрутера"), svgBox),
      h("div.sechead", { style: "justify-content:space-between" }, h("span", "Последние прогоны"),
        h("span", { style: "display:flex;align-items:center;gap:10px;text-transform:none;letter-spacing:0;font-weight:400" }, updated,
          loadButton, h("button.b", { type: "button", onClick: () => refresh() }, "Обновить"))),
      h("div.tablewrap", { style: "max-height:none" },
        h("table.runs", h("caption.sr-only", "Последний запуск каждой фоновой задачи"),
          h("thead", h("tr", ["Задача", "Когда", "Сколько длилась", "Итог", "Что сделано"].map((title) => h("th", { scope: "col" }, title)))),
          tbody)),
      h("p.hint", { style: "margin:10px 6px 0" }, "Показан последний запуск каждой задачи. После утверждения версии профиля база пересчитывается в фоне: ход виден в строке «Пересчёт по профилю», экран обновляется сам, пока задача идёт."),
    ];
  };

  return mountAsync(box, load, view, { signal });
}
