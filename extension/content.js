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

  function instRow(name, x) {
    // 一列一法人：外資: +3,927 張（連買3日），買紅賣綠(台股習慣)
    const cls = x.net > 0 ? "kp-buy" : x.net < 0 ? "kp-sell" : "";
    let s = (x.net > 0 ? "+" : "") + x.net.toLocaleString() + " 張";
    if (x.streak > 1) s += `（連買${x.streak}日）`;
    else if (x.streak < -1) s += `（連賣${-x.streak}日）`;
    return `<div class="kp-row"><span>${name}</span><span class="${cls}">${s}</span></div>`;
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
        <div class="kp-sec">歷史統計（分數 ${b.lo}~${b.hi} 的過去表現）</div>
        <div class="kp-row"><span>樣本數</span><span>${b.n.toLocaleString()}</span></div>
        <div class="kp-row"><span>5/10/20日勝率</span><span>${b.win5}% / ${b.win10}% / ${b.win20}%</span></div>
        <div class="kp-row"><span>平均報酬(20日)</span><span>${b.avg20 > 0 ? "+" : ""}${b.avg20}%</span></div>
        <div class="kp-row"><span>最大回撤</span><span class="kp-weak">${b.mdd}%</span></div>
        <div class="kp-note">${b.period}</div>`
        : `<div class="kp-note">歷史統計未產生（research/score_history.py）</div>`;
      const posTag = d.pos_pct == null ? "" :
        d.pos_pct >= 70 ? "（偏高）" : d.pos_pct <= 30 ? "（偏低）" : "（中段）";
      const v = d.verdict;
      const vCls = v ? (v.net >= 3 ? "kp-v-bull" : v.net <= -3 ? "kp-v-bear" : "kp-v-mid") : "";
      const verdict = v ? `
        <div class="kp-verdict ${vCls}">
          <div class="vt">${v.light} ${v.tone}</div>
          <div class="vc">${v.conf}</div>
          <div class="va">📋 ${v.action}</div>
        </div>` : "";
      const evoIco = ok => ok === true ? "✅" : ok === false ? "🔴" : "⚪";
      const E = d.evo || {};
      const evoRows = ["A", "B", "C_top", "C_bot", "D", "E", "F", "G"]
        .filter(k => E[k])
        .map(k => `<div>${evoIco(E[k].ok)} <span class="ek">${E[k].k}</span>：${E[k].v}</div>`)
        .join("");
      const evo = evoRows ? `
        <div class="kp-hr"></div>
        <div class="kp-sec">A–G 拆解</div>
        <div class="kp-evo">${evoRows}</div>` : "";
      p.innerHTML = `
        <span class="kp-close">✕</span>
        <div class="kp-title">kanpan 看盤　<b>${d.sid}</b> ${live}</div>
        <div class="kp-hr"></div>
        ${verdict}
        <div class="kp-score ${scoreColor(d.vp_score)}">當前分數　<b>${d.vp_score}</b><span class="kp-note"> /100</span></div>
        <div class="kp-note">趨勢40%+動能20%+量能20%+位置20%</div>
        <div class="kp-note">${d.date}　收盤/現價 ${d.close}</div>
        <div class="kp-hr"></div>
        ${hist}
        <div class="kp-hr"></div>
        <div class="kp-row"><span>趨勢分數</span><span>${d.trend_score}/100（均線結構）</span></div>
        <div class="kp-row"><span>結構</span><span><b>${d.structure}</b></span></div>
        <div class="kp-row"><span>週線</span><span>${d.weekly ?? "—"}</span></div>
        <div class="kp-row"><span>日週共振</span><span>${d.resonance ?? "—"}</span></div>
        <div class="kp-row"><span>動能</span><span>RSI ${d.rsi ?? "—"}　${d.momentum}</span></div>
        <div class="kp-row"><span>量能</span><span>${d.vol_ratio ?? "—"}倍 ${d.vol_tag}</span></div>
        <div class="kp-row"><span>量堆積</span><span>${d.skew_tag ?? "—"}</span></div>
        <div class="kp-row"><span>位置</span><span>60日區間 ${d.pos_pct ?? "—"}%${posTag}</span></div>
        ${d.vah ? `<div class="kp-row"><span>參考價位</span><span>壓 ${d.vah}｜軸 ${d.poc}｜支 ${d.val}</span></div>` : ""}
        ${d.ccp != null ? `<div class="kp-row"><span>收盤位置</span><span>${d.ccp}% ${d.ccp_tag}</span></div>` : ""}
        ${d.round_level ? `<div class="kp-row"><span>整數關卡</span><span>${d.round_level}（${d.round_dist > 0 ? "+" : ""}${d.round_dist}% ${d.round_tag}）</span></div>` : ""}
        ${d.poc_consist != null ? `<div class="kp-row"><span>POC一致</span><span>動${d.dyn_poc}≈靜${d.poc} ${d.poc_tag}</span></div>` : ""}
        ${evo}
        ${d.inst ? `
        <div class="kp-hr"></div>
        <div class="kp-sec">法人買賣超（${d.inst.date}）</div>
        ${instRow("外資", d.inst.foreign)}
        ${instRow("投信", d.inst.trust)}
        ${instRow("自營", d.inst.dealer)}` : ""}
        ${d.vol_note ? `<div class="kp-note">${d.vol_note}</div>` : ""}
        <div class="kp-hr"></div>
        <div class="kp-sec">評語</div>
        <div class="kp-comment">${(d.comment || "").replace(/\n/g, "<br>")}</div>`;
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
