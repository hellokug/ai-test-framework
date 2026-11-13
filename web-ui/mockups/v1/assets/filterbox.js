/* v1 统一筛选框组件（filterbox）——全平台筛选框统一格式（2026-11-12 owner 定）：
   大框点击展开筛选项面板 → 点选项即选中标签（多选）→ 选中项以 tag 回填框内。
   后续页面的筛选框一律用本组件，不再各页自造 chips 行。

   用法：
     const fb = fbMount(挂载元素, 分组, 选中变化回调, { placeholder });
     // 分组：[{ key, label, options: [{ value, label, count? }] }]
     // 回调参数：{ <groupKey>: [value, ...] }（空数组 = 该维不限）
     fb.setGroups(groups)   // 数据刷新后更新选项与计数（保留仍存在的选中项）
     fb.get()               // 当前选中
     fb.clear()             // 重置为全不限 */
(function () {
  if (document.getElementById("fbx-style")) return;
  const style = document.createElement("style");
  style.id = "fbx-style";
  style.textContent = `
.fbx{position:relative}
.fbx-box{display:flex;gap:8px;align-items:center;flex-wrap:wrap;min-height:40px;
  padding:7px 12px;border:1px solid var(--line);border-radius:10px;background:var(--card);
  cursor:pointer;user-select:none}
.fbx-box:hover{border-color:color-mix(in srgb,var(--ac) 45%,transparent)}
.fbx-box.open{border-color:var(--ac);box-shadow:0 0 0 3px color-mix(in srgb,var(--ac) 12%,transparent)}
.fbx-ico{color:var(--ac);font-size:13px;flex:none}
.fbx-title{font-weight:600;font-size:13px;flex:none}
.fbx-hint{color:var(--tx3);font-size:12.5px}
.fbx-n{font-size:11.5px;font-weight:700;color:var(--ac);background:color-mix(in srgb,var(--ac) 12%,transparent);
  border-radius:99px;padding:1px 8px;flex:none}
.fbx-tags{display:flex;gap:6px;flex-wrap:wrap;flex:1;min-width:120px}
.fbx-tags .tag{cursor:default;display:inline-flex;gap:5px;align-items:center}
.fbx-tags .tag b{cursor:pointer;color:var(--tx3);font-weight:400}
.fbx-tags .tag b:hover{color:var(--bad)}
.fbx-panel{position:absolute;left:0;right:0;top:calc(100% + 6px);z-index:80;
  background:var(--card);border:1px solid var(--line);border-radius:12px;
  box-shadow:0 14px 34px rgba(0,0,0,.14);padding:14px 16px 10px;display:none}
.fbx-panel.open{display:block}
.fbx-group{margin-bottom:10px}
.fbx-group>span{display:block;font-size:11.5px;font-weight:700;color:var(--tx3);
  letter-spacing:.04em;margin-bottom:7px}
.fbx-opts{display:flex;gap:7px;flex-wrap:wrap}
.fbx-opt{border:1px solid var(--line);background:var(--chipbg);color:var(--tx2);
  border-radius:99px;padding:4px 12px;font-size:12.5px;cursor:pointer;
  display:inline-flex;gap:6px;align-items:center}
.fbx-opt small{color:var(--tx3);font-weight:600}
.fbx-opt:hover{border-color:color-mix(in srgb,var(--ac) 45%,transparent);color:var(--tx)}
.fbx-opt.on{color:var(--ac);border-color:color-mix(in srgb,var(--ac) 50%,transparent);
  background:color-mix(in srgb,var(--ac) 10%,transparent);font-weight:600}
.fbx-opt.on::before{content:"✓";font-size:11px}
.fbx-foot{display:flex;gap:10px;align-items:center;border-top:1px solid var(--line);
  padding-top:9px;margin-top:2px}
.fbx-foot .link{color:var(--tx3);font-size:12px;cursor:pointer}
.fbx-foot .link:hover{color:var(--tx)}`;
  document.head.appendChild(style);
})();

function fbMount(mount, groups, onChange, opts = {}) {
  if (typeof mount === "string") mount = document.getElementById(mount);
  if (!mount) return null;
  mount.classList.add("fbx");
  const state = {};                       // groupKey -> Set(values)
  (groups || []).forEach(g => { state[g.key] = new Set(); });

  const box = document.createElement("div");
  box.className = "fbx-box";
  const panel = document.createElement("div");
  panel.className = "fbx-panel";
  mount.append(box, panel);

  const sel = () => Object.fromEntries(Object.entries(state)
    .map(([k, v]) => [k, [...v]]).filter(([, v]) => v.length));
  const emit = () => onChange && onChange(sel());

  function renderBox() {
    const tags = Object.entries(state).flatMap(([k, vals]) => {
      const g = (groups || []).find(x => x.key === k);
      return [...vals].map(v => {
        const o = ((g || {}).options || []).find(x => x.value === v);
        return { k, v, label: (o || {}).label || v };
      });
    });
    box.innerHTML = `<span class="fbx-ico">⚙</span><span class="fbx-title">筛选</span>` +
      (tags.length
        ? `<span class="fbx-n">${tags.length}</span><span class="fbx-tags">${
          tags.map(t => `<span class="tag">${t.label}<b data-fbx-rm="${t.k}:${t.v}">✕</b></span>`).join("")
        }</span>`
        : `<span class="fbx-hint">${opts.placeholder || "点击选择筛选项（可多选）"}</span>`);
  }
  function renderPanel() {
    panel.innerHTML = (groups || []).map(g => `
      <div class="fbx-group"><span>${g.label}</span><div class="fbx-opts">${
        g.options.map(o => `<button type="button" class="fbx-opt${
          state[g.key].has(o.value) ? " on" : ""}" data-fbx-opt="${g.key}:${o.value}">${o.label
          }${o.count !== undefined ? ` <small>${o.count}</small>` : ""}</button>`).join("")
      }</div></div>`).join("") +
      `<div class="fbx-foot"><span class="link" data-fbx-clear>重置</span>` +
      `<span class="link" data-fbx-close style="margin-left:auto">收起 ▴</span></div>`;
  }
  function render() { renderBox(); renderPanel(); }

  box.addEventListener("click", e => {
    const rm = e.target.closest("[data-fbx-rm]");
    if (rm) {
      const [k, v] = rm.dataset.fbxRm.split(":");
      state[k].delete(v);
      render(); emit();
      e.stopPropagation();
      return;
    }
    const open = panel.classList.toggle("open");
    box.classList.toggle("open", open);
  });
  panel.addEventListener("click", e => {
    const opt = e.target.closest("[data-fbx-opt]");
    if (opt) {
      const [k, v] = opt.dataset.fbxOpt.split(":");
      state[k].has(v) ? state[k].delete(v) : state[k].add(v);
      render(); emit();
      return;
    }
    if (e.target.closest("[data-fbx-clear]")) {
      Object.values(state).forEach(s => s.clear());
      render(); emit();
      return;
    }
    if (e.target.closest("[data-fbx-close]")) {
      panel.classList.remove("open");
      box.classList.remove("open");
    }
  });
  document.addEventListener("click", e => {
    // composedPath 是 dispatch 时定格的祖先链：选项点击后 renderPanel 重建了 DOM，
    // 原目标已脱离文档，mount.contains 会误判「点击外部」把面板收起
    if (!e.composedPath().includes(mount)) {
      panel.classList.remove("open");
      box.classList.remove("open");
    }
  });

  render();
  return {
    get: sel,
    set(selection) {
      Object.keys(state).forEach(k => { state[k] = new Set((selection || {})[k] || []); });
      render();
    },
    setGroups(next) {
      groups = next || [];
      Object.keys(state).forEach(k => {
        const g = groups.find(x => x.key === k);
        if (!g) delete state[k];
        else {
          const ok = new Set(g.options.map(o => o.value));
          state[k] = new Set([...state[k]].filter(v => ok.has(v)));
        }
      });
      groups.forEach(g => { if (!(g.key in state)) state[g.key] = new Set(); });
      render();
    },
    clear() {
      Object.values(state).forEach(s => s.clear());
      render(); emit();
    },
  };
}
