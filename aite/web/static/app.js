/* aite web SPA - Vue3 + ECharts, no build step. */
const { createApp, reactive, ref, computed } = Vue;

const app = createApp({
  data() {
    return {
      apiBase: "/api/v1",
      token: localStorage.getItem("aite_token") || "",
      view: "runs",
      // runs
      runs: [], stats: null,
      showLaunch: false, launching: false, launchError: "",
      launchYaml: LAUNCH_TEMPLATE,
      launchLimit: null, launchProgress: "", launchPct: 0,
      // run detail
      run: null, runCases: [], caseFilter: "",
      // trends
      trendQuery: { suite: "", metric: "", days: 30 }, trendRows: [],
      // suites / cases / models
      suites: [], caseSuite: "", cases: [],
      models: [],
      suiteEditor: { open: false, name: "", yaml: "", error: "" },
      modelEditor: { open: false, name: "", type: "openai_compatible", base_url: "", model: "", api_key_env: "" },
      // manual eval / tasks
      manualTasks: [], manualStatus: "pending", scoreForms: {},
      bgTasks: [],
    };
  },
  computed: {
    filteredRunCases() {
      if (!this.caseFilter) return this.runCases.slice(0, 300);
      const q = this.caseFilter.toLowerCase();
      return this.runCases.filter(c =>
        (c.external_key || "").toLowerCase().includes(q) ||
        JSON.stringify(c.output || "").toLowerCase().includes(q)).slice(0, 300);
    },
  },
  methods: {
    // ---------------- infra ----------------
    saveToken() { localStorage.setItem("aite_token", this.token); },
    async api(path, opts = {}) {
      const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
      if (this.token) headers["Authorization"] = "Bearer " + this.token;
      const resp = await fetch(this.apiBase + path, { ...opts, headers });
      if (!resp.ok) {
        let detail = resp.statusText;
        try { detail = (await resp.json()).detail || detail; } catch (e) { /* noop */ }
        throw new Error(`${resp.status}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
      }
      return resp.json();
    },
    go(view, arg) {
      this.view = view;
      location.hash = arg ? `#/${view}/${arg}` : `#/${view}`;
      if (view === "runs") this.loadRuns();
      if (view === "trends") this.loadTrends();
      if (view === "suites" || view === "cases") this.loadSuites().then(() => { if (view === "cases" && this.caseSuite) this.loadCases(); });
      if (view === "models") this.loadModels();
      if (view === "manual") this.loadManual();
      if (view === "tasks") this.loadTasks();
      if (view === "run-detail" && arg) this.openRun(arg);
    },
    fmtTime(iso) { return iso ? new Date(iso).toLocaleString() : "—"; },
    fmt4(v) { return v == null ? "—" : Number(v).toFixed(4); },
    shorten(s, n) { s = String(s ?? ""); return s.length > n ? s.slice(0, n) + "…" : s; },
    outText(c, n = 160) { return this.shorten(typeof c.output === "string" ? c.output : JSON.stringify(c.output), n); },
    templateYaml() { return LAUNCH_TEMPLATE; },

    // ---------------- runs ----------------
    async loadRuns() {
      const [runs, stats] = await Promise.all([
        this.api("/runs?limit=100"), this.api("/stats").catch(() => null),
      ]);
      this.runs = runs.items; this.stats = stats;
    },
    async openRun(id) {
      this.view = "run-detail"; location.hash = `#/run-detail/${id}`;
      const [run, cases] = await Promise.all([
        this.api(`/runs/${id}`), this.api(`/runs/${id}/cases?limit=1000`),
      ]);
      run.summaries = run.summaries || [];
      this.run = run;
      this.runCases = cases.items.map(c => ({ ...c, _open: false }));
    },
    async launchRun() {
      this.launching = true; this.launchError = ""; this.launchProgress = "提交中…";
      try {
        const res = await this.api("/runs", {
          method: "POST",
          body: JSON.stringify({ config_yaml: this.launchYaml, limit: this.launchLimit || null }),
        });
        this.watchTask(res.task_id);
      } catch (e) { this.launchError = String(e.message || e); this.launching = false; }
    },
    watchTask(taskId) {
      const es = new EventSource(this.apiBase + `/tasks/${taskId}/stream`);
      es.onmessage = (ev) => {
        const data = JSON.parse(ev.data);
        if (data.type === "progress") {
          const pct = data.total ? Math.round((data.done / data.total) * 100) : 0;
          this.launchProgress = `${data.phase || ""} ${data.done}/${data.total} ${data.note || ""}`;
          this.launchPct = pct;
        } else if (data.type === "run_finished" || data.type === "done") {
          this.launchProgress = `完成：${data.status || ""}`;
          this.launchPct = 100;
          es.close(); this.launching = false; this.loadRuns();
          if (data.run_id) setTimeout(() => this.openRun(data.run_id), 800);
        } else if (data.type === "error") {
          this.launchError = data.error; es.close(); this.launching = false;
        }
      };
      es.onerror = () => { es.close(); this.launching = false; };
    },
    async runSuite(name) {
      try {
        const res = await this.api("/runs", { method: "POST", body: JSON.stringify({ suite_name: name }) });
        this.go("tasks"); this.watchTask(res.task_id);
      } catch (e) { alert("启动失败: " + e.message); }
    },

    // ---------------- trends ----------------
    async loadTrends() {
      const q = new URLSearchParams();
      if (this.trendQuery.suite) q.set("suite", this.trendQuery.suite);
      if (this.trendQuery.metric) q.set("metric", this.trendQuery.metric);
      q.set("days", this.trendQuery.days || 30);
      const res = await this.api("/trends?" + q.toString());
      this.trendRows = res.items;
      this.renderTrendChart();
      this.renderRadar();
    },
    renderTrendChart() {
      const el = document.getElementById("trendChart");
      if (!el || typeof echarts === "undefined") return;
      const byMetric = {};
      for (const r of this.trendRows) {
        (byMetric[r.metric] = byMetric[r.metric] || []).push(r);
      }
      const series = Object.entries(byMetric).map(([metric, rows]) => ({
        name: metric, type: "line", smooth: true, symbolSize: 7,
        data: rows.map(r => [r.created_at, r.value]),
      }));
      echarts.init(el).setOption({
        tooltip: { trigger: "axis" },
        legend: { top: 0 },
        grid: { left: 50, right: 20, top: 36, bottom: 40 },
        xAxis: { type: "time" },
        yAxis: { type: "value", scale: true },
        series,
      }, true);
    },
    renderRadar() {
      const el = document.getElementById("radarChart");
      if (!el || typeof echarts === "undefined") return;
      const latestRun = this.runs[0];
      const overall = latestRun && latestRun.summaries
        ? latestRun.summaries.filter(s => !s.dataset && !s.group) : [];
      if (!overall.length) {
        echarts.init(el).setOption({
          title: { text: "先打开一个 Run 详情后再查看雷达图", left: "center", top: "middle",
                   textStyle: { color: "#8a919f", fontSize: 13, fontWeight: "normal" } } }, true);
        return;
      }
      echarts.init(el).setOption({
        tooltip: {},
        radar: {
          indicator: overall.map(s => ({ name: s.metric, max: Math.max(s.threshold || 0, s.value || 0, 1) })),
        },
        series: [{ type: "radar", areaStyle: { opacity: 0.25 },
          data: [{ name: latestRun.run_name, value: overall.map(s => s.value ?? 0) }] }],
      }, true);
    },

    // ---------------- suites / cases / models ----------------
    async loadSuites() { const r = await this.api("/suites"); this.suites = r.items; },
    editSuite(s) {
      const yaml = s.config_yaml || yamlStringify(s.config) || LAUNCH_TEMPLATE;
      this.suiteEditor = { open: true, name: s.name || "", yaml, error: "" };
    },
    yamlStringify(cfg) {
      if (!cfg || !Object.keys(cfg).length) return "";
      try { return jsYamlDump(cfg); } catch (e) { return ""; }
    },
    async saveSuite() {
      this.suiteEditor.error = "";
      try {
        await this.api("/suites", { method: "POST", body: JSON.stringify({
          name: this.suiteEditor.name, config_yaml: this.suiteEditor.yaml }) });
        this.suiteEditor.open = false; this.loadSuites();
      } catch (e) { this.suiteEditor.error = String(e.message || e); }
    },
    async loadCases() {
      if (!this.caseSuite) { this.cases = []; return; }
      const r = await this.api(`/cases?suite=${encodeURIComponent(this.caseSuite)}&limit=300`);
      this.cases = r.items;
    },
    async loadModels() { const r = await this.api("/models"); this.models = r.items; },
    newModel() {
      this.modelEditor = { open: true, name: "", type: "openai_compatible",
        base_url: "", model: "", api_key_env: "" };
    },
    async saveModel() {
      await this.api("/models", { method: "POST", body: JSON.stringify(this.modelEditor) });
      this.modelEditor.open = false; this.loadModels();
    },
    async deleteModel(id) {
      if (!confirm("确认删除该模型配置？")) return;
      await this.api(`/models/${id}`, { method: "DELETE" }); this.loadModels();
    },

    // ---------------- manual eval ----------------
    async loadManual() {
      const q = this.manualStatus ? `?status=${this.manualStatus}` : "";
      const r = await this.api(`/manual-eval/tasks${q}`);
      this.manualTasks = r.items;
      for (const t of this.manualTasks) {
        if (!this.scoreForms[t.id]) {
          this.scoreForms[t.id] = { score: null, reviewer: "", comment: "" };
        }
      }
    },
    async submitScore(t) {
      const f = this.scoreForms[t.id];
      if (f.score == null) { alert("请填写评分（0-10）"); return; }
      await this.api(`/manual-eval/tasks/${t.id}/score`, {
        method: "POST",
        body: JSON.stringify({ score: f.score, reviewer: f.reviewer, comment: f.comment,
                               passed: f.score >= 7 ? true : false }),
      });
      this.loadManual();
    },
    async exportManual() {
      const r = await this.api("/manual-eval/export");
      const rows = r.items.map(x => [x.title, x.score, x.passed, x.reviewer, x.comment || "",
        (x.created_at || "").replace("T", " ")]);
      const csv = "\uFEFF" + ["标题,评分,通过,评审人,备注,时间", ...rows.map(r2 => r2.join(","))].join("\n");
      const blob = new Blob([csv], { type: "text/csv" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = "manual_eval.csv"; a.click();
    },

    // ---------------- tasks ----------------
    async loadTasks() { const r = await this.api("/tasks?limit=100"); this.bgTasks = r.items; },
  },
  mounted() {
    const route = location.hash.replace(/^#\//, "").split("/");
    if (route[0] === "run-detail" && route[1]) this.openRun(route[1]);
    else if (["trends", "suites", "cases", "models", "manual", "tasks"].includes(route[0])) this.go(route[0]);
    else this.go("runs");
  },
});

// tiny YAML dump for suite configs shown in editor (format-only, validation is server-side)
function jsYamlDump(obj) {
  const lines = [];
  const dump = (val, indent) => {
    for (const [k, v] of Object.entries(val || {})) {
      if (v === null || v === undefined) { lines.push(`${indent}${k}: null`); continue; }
      if (typeof v === "object" && !Array.isArray(v)) {
        lines.push(`${indent}${k}:`); dump(v, indent + "  ");
      } else if (Array.isArray(v)) {
        lines.push(`${indent}${k}:`);
        for (const item of v) lines.push(`${indent}  - ${JSON.stringify(item)}`);
      } else if (typeof v === "string" && /[:{}\n,#]/.test(v)) {
        lines.push(`${indent}${k}: ${JSON.stringify(v)}`);
      } else {
        lines.push(`${indent}${k}: ${v}`);
      }
    }
  };
  dump(obj, "");
  return lines.join("\n");
}

const LAUNCH_TEMPLATE = `run_name: my-first-run
description: 通过 Web 启动的评测

target:
  type: openai_compatible
  base_url: http://localhost:11434/v1     # OpenAI/DeepSeek/vLLM/Ollama 均可
  model: qwen2.5:7b
  api_key_env: OPENAI_API_KEY
  params: {temperature: 0.2, max_tokens: 512}
  rate_limit: {rpm: 120, concurrency: 8}
  stream: true

datasets:
  - type: builtin
    id: mmlu
    sample: {n: 10, seed: 42}

metrics:
  - choice_accuracy: {threshold: 0.6}
  - latency_p95_ms: {threshold: 30000}

pass_strategy: all_of
report: [html, json]
output_dir: ./reports
storage: {url: sqlite+aiosqlite:///./aite.db}
`;

app.mount("#app");
