// kanpan — TradingView content script
// 讀目前台股代號 → 打本機 kanpan api(8771) → 右側真分割面板。描述現況，非買賣建議。
(() => {
  const API = "http://127.0.0.1:8771/panel";
  const PANEL_W = 300;
  let lastSid = null;
  let panel = null;

  // 台股代號（含 ETF 0050、興櫃 00400A）；期貨(TXF1!)/美股(NVDA)不符 → 略過。
  // TV 點自選換股「網址不更新」→ 讀畫面即時元件，網址當後備。
  const CODE = /^([0-9]{4,6}[A-Z]?)\b/;
  const TWMKT = /(?:TWSE|TPEX|TWO|ROCO):([0-9]{4,6}[A-Z]?)\b/;

  function fromText(s) {
    const m = (s || "").trim().match(CODE);
    return m ? m[1] : null;
  }

  function getTicker() {
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
    // 真分割：網頁縮左 PANEL_W，面板補右邊（不蓋住 TV），關閉還原
    document.documentElement.style.setProperty(
      "margin-right", on ? PANEL_W + "px" : "", "important");
    document.body.style.setProperty(
      "width", on ? `calc(100vw - ${PANEL_W}px)` : "", "important");
    document.body.style.setProperty("overflow-x", on ? "hidden" : "", "important");
    setTimeout(() => window.dispatchEvent(new Event("resize")), 50);
  }

  function ensurePanel() {
    if (panel) return panel;
    panel = document.createElement("div");
    panel.id = "kp-panel";
    document.body.appendChild(panel);
    dockPage(true);
    return panel;
  }

  function scoreColor(s) {
    if (s >= 70) return "kp-good";
    if (s >= 50) return "kp-mid";
    return "kp-weak";
  }

  function render(d) {
    const p = ensurePanel();
    if (d.error) {
      p.innerHTML = `<span class="kp-close">✕</span>
        <div class="kp-title">kanpan</div><div class="kp-hr"></div>
        <div class="kp-err">${d.error}</div>
        <div class="kp-foot">後端沒開？跑 python api.py</div>`;
    } else {
      const live = d.live
        ? `<span class="kp-live">● 即時 ${d.live_time}</span>`
        : `<span class="kp-static">收盤</span>`;
      const b = d.hist_bucket;
      const hist = (b && b.n > 0) ? `
        <div class="kp-sec">歷史統計（Score ${b.lo}~${b.hi}）</div>
        <div class="kp-row"><span>樣本數</span><span>${b.n.toLocaleString()}</span></div>
        <div class="kp-row"><span>5/10/20日勝率</span><span>${b.win5}% / ${b.win10}% / ${b.win20}%</span></div>
        <div class="kp-row"><span>平均報酬(20日)</span><span>${b.avg20 > 0 ? "+" : ""}${b.avg20}%</span></div>
        <div class="kp-row"><span>最大回撤</span><span class="kp-weak">${b.mdd}%</span></div>
        <div class="kp-note">${b.period}，歷史統計非預測</div>`
        : `<div class="kp-note">歷史統計未產生（research/score_history.py）</div>`;
      p.innerHTML = `
        <span class="kp-close">✕</span>
        <div class="kp-title">kanpan　<b>${d.sid}</b> ${live}</div>
        <div class="kp-hr"></div>
        <div class="kp-score ${scoreColor(d.vp_score)}">VP Score　<b>${d.vp_score}</b></div>
        <div class="kp-note">${d.date}　收盤/現價 ${d.close}</div>
        <div class="kp-hr"></div>
        ${hist}
        <div class="kp-hr"></div>
        <div class="kp-row"><span>Trend</span><span>${d.trend_score}/100</span></div>
        <div class="kp-row"><span>Structure</span><span><b>${d.structure}</b></span></div>
        <div class="kp-row"><span>Momentum</span><span>RSI ${d.rsi ?? "—"}　${d.momentum}</span></div>
        <div class="kp-row"><span>Volume</span><span>${d.vol_ratio ?? "—"}倍　${d.vol_tag}</span></div>
        <div class="kp-row"><span>Position</span><span>60日區間 ${d.pos_pct ?? "—"}%</span></div>
        ${d.vol_note ? `<div class="kp-note">${d.vol_note}</div>` : ""}
        <div class="kp-hr"></div>
        <div class="kp-sec">評語</div>
        <div class="kp-comment">${(d.comment || "").replace(/\n/g, "<br>")}</div>
        <div class="kp-hr"></div>
        <div class="kp-foot">${d.disclaimer}</div>`;
    }
    p.querySelector(".kp-close").onclick = () => {
      p.remove(); panel = null; lastSid = null;
      dockPage(false);
    };
  }

  async function fetchAndRender(sid) {
    try {
      const r = await fetch(`${API}?sid=${sid}`);
      render(await r.json());
    } catch (e) {
      render({ error: "連不上本機後端 (127.0.0.1:8771)" });
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
