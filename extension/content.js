// kanpan — TradingView content script
// 讀目前台股代號 → 打本機 kanpan api(8771) → 右側真分割面板。描述現況，非買賣建議。
(() => {
  const API = "http://127.0.0.1:8771/panel";
  const POSAPI = "http://127.0.0.1:8771/position";
  const PANEL_W = 300;
  let lastSid = null;
  let panel = null;
  let tab = null;          // 收合後的小標籤
  let collapsed = false;   // 收合狀態
  let liveTimer = null;    // 盤中自動重抓計時器
  const REFRESH_MS = 20000;// 盤中每 20 秒更新(MIS 即時價約 5~20 秒跳一次)

  // 台股代號（含 ETF 0050、興櫃 00400A）；期貨(TXF1!)/美股(NVDA)不符 → 略過。
  // TV 點自選換股「網址不更新」→ 讀畫面即時元件，網址當後備。
  const CODE = /^([0-9]{4,6}[A-Z]?)\b/;
  const TWMKT = /(?:TWSE|TPEX|TWO|ROCO):([0-9]{4,6}[A-Z]?)\b/;
  // Yahoo 股市：tw.stock.yahoo.com/quote/2330.TW（上市.TW / 上櫃.TWO），代號在網址
  const YAHOO = /\/quote\/([0-9]{4,6}[A-Z]?)(?:\.TW[O]?)?/;

  function fromText(s) {
    const m = (s || "").trim().match(CODE);
    return m ? m[1] : null;
  }

  function getTicker() {
    // Yahoo 股市：代號在網址、換股會變 → 優先
    if (location.hostname.includes("stock.yahoo.com")) {
      const m = decodeURIComponent(location.href).match(YAHOO);
      return m ? m[1] : null;
    }
    // TradingView：點自選換股網址不更新 → 先讀畫面即時元件
    const sels = ["#header-toolbar-symbol-search",
                  '[data-name="legend-source-title"]',
                  'button[aria-label*="symbol" i]'];
    for (const sel of sels) {
      const el = document.querySelector(sel);
      const t = fromText(el && el.textContent);
      if (t) return t;
    }
    const m = decodeURIComponent(location.href).match(TWMKT);
    return m ? m[1] : null;
  }

  function dockPage(on) {
    // 真分割：網頁縮左 = 面板實際寬度（clamp 響應式），面板補右邊，關閉還原
    const w = (on && panel) ? Math.round(panel.getBoundingClientRect().width) || PANEL_W : PANEL_W;
    document.documentElement.style.setProperty(
      "margin-right", on ? w + "px" : "", "important");
    document.body.style.setProperty(
      "width", on ? `calc(100vw - ${w}px)` : "", "important");
    document.body.style.setProperty("overflow-x", on ? "hidden" : "", "important");
    setTimeout(() => window.dispatchEvent(new Event("resize")), 50);
  }

  function ensurePanel() {
    if (panel) return panel;
    panel = document.createElement("div");
    panel.id = "kp-panel";
    document.body.appendChild(panel);
    if (!collapsed) dockPage(true);
    if (collapsed) panel.style.display = "none";
    return panel;
  }

  function ensureTab() {
    if (tab) return tab;
    tab = document.createElement("div");
    tab.id = "kp-tab";
    tab.textContent = "◀ kanpan";
    tab.style.display = "none";
    tab.onclick = expand;
    document.body.appendChild(tab);
    return tab;
  }

  function collapse() {
    collapsed = true;
    if (liveTimer) { clearTimeout(liveTimer); liveTimer = null; }   // 收合停更新
    if (panel) panel.style.display = "none";
    ensureTab().style.display = "block";
    dockPage(false);
  }

  function expand() {
    collapsed = false;
    if (tab) tab.style.display = "none";
    if (panel) {
      panel.style.display = "";
      dockPage(true);
      if (lastSid) fetchAndRender(lastSid);   // 展開立刻刷新並續排程
    }
  }

  function instRow(name, x) {
    // 一列一法人：外資: +3,927 張（連買3日），買紅賣綠(台股習慣)
    const cls = x.net > 0 ? "kp-buy" : x.net < 0 ? "kp-sell" : "";
    let s = (x.net > 0 ? "+" : "") + x.net.toLocaleString() + " 張";
    if (x.streak > 1) s += `（連買${x.streak}日）`;
    else if (x.streak < -1) s += `（連賣${-x.streak}日）`;
    return `<div class="kp-row"><span>${name}</span><span class="${cls}">${s}</span></div>`;
  }

  function head(d, live) {
    return `<div class="kp-head">
        <span class="kp-brand"><span class="rk">🚀</span>kanpan VP</span>
        <span><span class="kp-sym">${d.sid || ""}</span>${live}` +
        `<span class="kp-min" title="收合">▸</span><span class="kp-close" title="關閉">✕</span></span>
      </div>`;
  }

  function posCard(pos) {
    if (!pos) return "";
    const cls = pos.light === "🟢" ? "g" : pos.light === "🟡" ? "y" : "r";
    const sh = pos.shares != null ? ` × ${pos.shares}張` : "";
    const uCls = pos.unreal_pct >= 0 ? "kp-buy" : "kp-sell";
    return `<div class="kp-pos ${cls}">
      <div class="ph">持倉 ${pos.sid}　${pos.light} ${pos.state}</div>
      <div class="pm">進場 ${pos.entry_price}${sh}｜現價 <span class="px">${pos.cur_price}</span>｜未實現 <span class="${uCls}">${pos.unreal_pct > 0 ? "+" : ""}${pos.unreal_pct}%</span></div>
      <div class="pm">生效出場：<span class="px">${pos.effective_exit}</span>（${pos.effective_by}，硬停損${pos.hard_stop} / Trail高點${pos.peak_price}−8%=${pos.trail_stop}）</div>
      <div class="pm">距觸發 <span class="px">${pos.dist_pct > 0 ? "+" : ""}${pos.dist_pct}%</span></div>
      <div class="pa"><button class="kp-pclose" data-sid="${pos.sid}" data-px="${pos.cur_price}">平倉（現價 ${pos.cur_price}）</button></div>
    </div>`;
  }

  function posForm(sid, curPrice) {
    return `<div class="kp-pos">
      <div class="ph">建立持倉 ${sid}</div>
      <div class="pf">
        <input class="kp-pentry" type="number" step="0.01" placeholder="進場價" value="${curPrice != null ? curPrice : ""}">
        <input class="kp-pshares" type="number" step="0.001" placeholder="張數" value="1">
        <button class="kp-popen" data-sid="${sid}">建倉</button>
      </div>
    </div>`;
  }

  async function posPost(body) {
    const r = await fetch(POSAPI, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return r.json();
  }

  function bindPos(p, sid) {
    const ob = p.querySelector(".kp-popen");
    if (ob) ob.onclick = async () => {
      const entry = parseFloat(p.querySelector(".kp-pentry").value);
      const shares = parseFloat(p.querySelector(".kp-pshares").value);
      if (!(entry > 0) || !(shares > 0)) { alert("進場價/張數要 > 0"); return; }
      ob.disabled = true; ob.textContent = "建倉中…";
      const res = await posPost({ action: "open", sid: ob.dataset.sid, entry_price: entry, shares });
      if (res.error) { alert(res.error); ob.disabled = false; ob.textContent = "建倉"; return; }
      fetchAndRender(sid);
    };
    const cb = p.querySelector(".kp-pclose");
    if (cb) cb.onclick = async () => {
      if (!confirm(`平倉 ${cb.dataset.sid} @ ${cb.dataset.px}？`)) return;
      cb.disabled = true; cb.textContent = "平倉中…";
      const res = await posPost({ action: "close", sid: cb.dataset.sid, exit_price: parseFloat(cb.dataset.px) });
      if (res.error) { alert(res.error); cb.disabled = false; return; }
      fetchAndRender(sid);
    };
  }

  function render(d) {
    const p = ensurePanel();
    if (d.error) {
      p.innerHTML = head(d, "") + `<div class="kp-body">
        <div class="kp-err">${d.error}</div>
        <div class="kp-foot">後端沒開？跑 python api.py</div></div>`;
      bindClose(p);
      return;
    }
    const live = d.live
      ? `<span class="kp-live">● 即時 ${d.live_time}</span>`
      : `<span class="kp-static">收盤</span>`;

    // 判讀燈號（主角）
    const v = d.verdict;
    const vCls = v ? (v.net >= 3 ? "kp-v-bull" : v.net <= -3 ? "kp-v-bear" : "kp-v-mid") : "";
    const verdict = v ? `
      <div class="kp-verdict ${vCls}">
        <div class="vf">${v.frame || "現況研判·非預測"}</div>
        <div class="vt">${v.light} ${v.tone}</div>
        <div class="vc">${v.conf}　|　分數 ${d.vp_score}/100　${d.structure}</div>
        <div class="va">📋 ${v.action}</div>
      </div>` : "";

    // L1 狀態層（純重排現有資料：趨勢/籌碼/動能 + checklist）
    const sl = d.state_layer;
    const ck = sl ? sl.checklist.map(x =>
      `<span class="kp-ck ${x.ok ? "on" : "off"}">${x.ok ? "☑" : "☒"} ${x.k}</span>`).join("") : "";
    const stateCard = sl ? `
      <div class="kp-state">
        <div class="kp-srow"><span>趨勢 <b>${sl.trend}</b></span><span>籌碼 <b>${sl.chips_light}${sl.chips}</b></span><span>動能 <b>${sl.momentum}</b></span></div>
        <div class="kp-cks">${ck}</div>
      </div>` : "";

    // 功能A 資料新鮮度 + 功能七 一致性警示
    const fr = d.freshness, ifr = d.inst_fresh, cons = d.consistency;
    const consWarn = (cons && !cons.ok)
      ? cons.mismatch.map(s => `<div class="kp-err">⚠ ${s.name} 停在 ${s.date}，與基準 ${cons.ref} 不一致</div>`).join("")
      : "";
    const freshWarn =
      (d.ref_date ? `<div class="kp-note">資料基準 ${d.ref_date} 收盤</div>` : "") +
      ((fr && fr.stale) ? `<div class="kp-err">⚠ 價格延遲 ${fr.lag} 日（最後 ${fr.last}）僅供參考</div>` : "") +
      ((ifr && ifr.stale) ? `<div class="kp-note" style="color:#e8a33d">⚠ 法人延遲 ${ifr.lag} 日（T86 約16:00公布）</div>` : "") +
      consWarn;

    // A–G 拆解（字母徽章；含 D收盤位置/E整數/F RollingPOC，故下方不再重複）
    const ico = ok => ok === true ? "✅" : ok === false ? "🔴" : "⚪";
    const E = d.evo || {};
    const badge = { A: "A", B: "B", C_top: "C", C_bot: "C", D: "D", E: "E", F: "F", G: "G", H: "H", BO: "量" };
    const agRows = ["A", "B", "C_top", "C_bot", "D", "E", "F", "G", "H", "BO"]
      .filter(k => E[k])
      .map(k => `<div class="kp-ag"><span class="bd">${badge[k]}</span>` +
                `<span class="lbl">${E[k].k}</span>` +
                `<span class="val"><span class="ic">${ico(E[k].ok)}</span>${E[k].v}</span></div>`)
      .join("");
    const ag = agRows ? `<div class="kp-sec">A–G 拆解</div>${agRows}` : "";

    // 數據（精簡：不重複 A–G 已涵蓋的 收盤位置/整數/POC一致）
    const posTag = d.pos_pct == null ? "" :
      d.pos_pct >= 70 ? "偏高" : d.pos_pct <= 30 ? "偏低" : "中段";
    const data = `
      <div class="kp-sec">數據</div>
      <div class="kp-row"><span>動能</span><span>RSI ${d.rsi ?? "—"}　${d.momentum}</span></div>
      <div class="kp-row"><span>量能</span><span>${d.vol_ratio ?? "—"}x ${d.vol_tag}</span></div>
      <div class="kp-row"><span>位置(60日)</span><span>${d.pos_pct ?? "—"}% ${posTag}</span></div>
      ${d.bias20 != null ? `<div class="kp-row"><span>乖離(月/季)</span><span>${d.bias20 > 0 ? "+" : ""}${d.bias20}% / ${d.bias60 > 0 ? "+" : ""}${d.bias60 ?? "—"}%　${d.bias_tag}</span></div>` : ""}
      <div class="kp-row"><span>週線/共振</span><span>${(d.weekly || "—").replace(/[()（）].*/, "")}｜${d.resonance ?? "—"}</span></div>
      ${d.vah ? `<div class="kp-row"><span>參考價位</span><span>壓 ${d.vah}｜軸 ${d.poc}｜支 ${d.val}</span></div>` : ""}`;

    // 歷史統計（收合）
    const b = d.hist_bucket;
    const hist = (b && b.n > 0) ? `
      <details><summary>歷史統計（分數 ${b.lo}~${b.hi}）</summary>
        <div class="kp-row"><span>樣本數</span><span>${b.n.toLocaleString()}</span></div>
        <div class="kp-row"><span>5/10/20日勝率</span><span>${b.win5}/${b.win10}/${b.win20}%</span></div>
        <div class="kp-row"><span>平均報酬20日</span><span>${b.avg20 > 0 ? "+" : ""}${b.avg20}%</span></div>
        <div class="kp-row"><span>最大回撤</span><span class="kp-sell">${b.mdd}%</span></div>
        <div class="kp-note">${b.period}</div></details>` : "";

    // 法人
    const ic = d.inst_consensus;
    const icCls = ic ? (ic.status === "一致偏多" ? "kp-buy" : ic.status === "一致偏空" ? "kp-sell" : "") : "";
    const inst = d.inst ? `
      <div class="kp-sec">法人（${d.inst.date}）</div>
      ${instRow("外資", d.inst.foreign)}
      ${instRow("投信", d.inst.trust)}
      ${instRow("自營", d.inst.dealer)}
      ${ic ? `<div class="kp-row"><span>法人共識</span><span class="${icCls}">${ic.light} ${ic.status}（主導${ic.leader}${ic.neutral && ic.neutral.length ? "，" + ic.neutral.join("/") + "中性" : ""}）</span></div>` : ""}` : "";

    p.innerHTML = head(d, live) + `<div class="kp-body">
      ${d.position ? posCard(d.position) : posForm(d.sid, d.close)}
      ${freshWarn}
      ${verdict}
      ${stateCard}
      ${ag}
      ${data}
      ${inst}
      ${d.vol_note ? `<div class="kp-note">${d.vol_note}</div>` : ""}
      ${hist}
      <div class="kp-sec">評語</div>
      <div class="kp-comment">${(d.comment || "").replace(/\n/g, "<br>")}</div>
    </div>`;
    bindClose(p);
    bindPos(p, d.sid);
  }

  function bindClose(p) {
    const c = p.querySelector(".kp-close");
    if (c) c.onclick = () => {
      if (liveTimer) { clearTimeout(liveTimer); liveTimer = null; }
      p.remove(); panel = null; lastSid = null;
      if (tab) tab.style.display = "none";
      collapsed = false; dockPage(false);
    };
    const m = p.querySelector(".kp-min");
    if (m) m.onclick = collapse;
    if (collapsed) p.style.display = "none";
  }

  async function fetchAndRender(sid) {
    let d;
    try {
      d = await (await fetch(`${API}?sid=${sid}`)).json();
    } catch (e) {
      render({ error: "連不上本機後端 (127.0.0.1:8771)" });
      return;
    }
    render(d);
    // 盤中且面板展開 → 排程下次自動更新(同檔才續抓)
    if (liveTimer) { clearTimeout(liveTimer); liveTimer = null; }
    if (d && d.live && !collapsed) {
      liveTimer = setTimeout(() => {
        if (lastSid === sid && panel && !collapsed) fetchAndRender(sid);
      }, REFRESH_MS);
    }
  }

  function tick() {
    const sid = getTicker();
    if (sid && sid !== lastSid) {
      lastSid = sid;
      fetchAndRender(sid);
    }
  }
  setInterval(tick, 1000);
  tick();
})();
