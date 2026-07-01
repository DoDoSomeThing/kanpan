#!/usr/bin/env python3
"""
kanpan panel — CLI 看盤面板

用法：
  python panel.py 2330                     # 用預設 cache（cache/kline_cache.json.gz）
  python panel.py 2330 --cache 路徑        # 指定 cache

輸出：VP PANEL（VP Score / 歷史統計 / 四區塊 / 評語）。
歷史統計來自 research/score_stats.json（先跑 research/score_history.py 產生）。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import load_bars, compute_panel, comment, verdict, data_freshness, consistency_check, state_layer, wilson_ci
from inst import get_inst, fmt_row, consensus
from playbook import detect_playbook, playbook_view, load_stats as load_pb_stats
from position import load_positions, position_risk, attach_alpha, _load_bench
import behavior as BH

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CACHES = [
    os.path.join(HERE, "cache", "kline_cache.json.gz"),   # kanpan 自己的 cache(fetch_data.py 下載)
]
STATS_PATH = os.path.join(HERE, "research", "score_stats.json")


def find_cache(arg):
    if arg:
        return arg
    for p in DEFAULT_CACHES:
        if os.path.exists(p):
            return p
    sys.exit("找不到 K 線 cache。用 --cache 指定，或先跑 data/fetch_data.py")


def load_stats():
    if os.path.exists(STATS_PATH):
        return json.load(open(STATS_PATH, encoding="utf-8"))
    return None


def bucket_label(score, stats):
    for b in stats["buckets"]:
        if b["lo"] <= score <= b["hi"]:
            return b
    return None


def render(sid, p, stats):
    line = "=" * 30
    out = [line, f"  kanpan 看盤 — {sid}", line, ""]
    # 判讀燈號（綁回測勝率）
    b0 = bucket_label(p["vp_score"], stats) if stats else None
    v = verdict(p, b0["win20"] if (b0 and b0["n"] > 0) else None)
    out += [f"〔{v.get('frame', '現況研判·非預測')}〕",
            f"{v['light']} {v['tone']}　{v['conf']}",
            f"操作研判: {v['action']}", ""]
    # 個股 V3 趨勢燈（always-on，唯一跨市場驗證的規則；不用建倉就看得到）
    tr = p.get("trend")
    if tr and tr["broken"]:
        out += [f"🚦 V3 趨勢燈: 🔴 紅燈　MA60 {tr['ma60']:.2f} < MA120 {tr['ma120']:.2f}（趨勢轉空 → 守/減碼、別接刀）", ""]
    elif tr:
        out += [f"🚦 V3 趨勢燈: 🟢 綠燈　MA60 {tr['ma60']:.2f} > MA120 {tr['ma120']:.2f}（趨勢偏多 → 可續抱）", ""]
    else:
        out += ["🚦 V3 趨勢燈: ⚪ 資料不足（需 120 日）", ""]
    # L1 狀態層（純重排現有資料，一眼看現況）
    sl = state_layer(p)
    ck = "  ".join(f"{'☑' if x['ok'] else '☒'} {x['k']}" for x in sl["checklist"])
    out += [f"狀態: 趨勢 {sl['trend']}｜籌碼 {sl['chips_light']}{sl['chips']}｜動能 {sl['momentum']}",
            f"      {ck}", ""]
    out.append(f"當前分數: {p['vp_score']} / 100")
    out.append("（趨勢40% + 動能20% + 量能20% + 位置20%）")
    out.append(f"資料日: {p['date']}　收盤 {p['close']}")
    if p.get("ref_date"):
        out.append(f"資料基準: {p['ref_date']} 收盤（全欄位同一根 bar）")
    fr = p.get("freshness")
    if fr and fr.get("stale"):
        out.append(f"⚠ 價格資料延遲 {fr['lag']} 日（最後 {fr['last']}，應有 {fr['expected']}）訊號僅供參考")
    ifr = p.get("inst_fresh")
    if ifr and ifr.get("stale"):
        out.append(f"⚠ 法人資料延遲 {ifr['lag']} 日（最後 {ifr['last']}；T86 約16:00公布，盤中本就落後）")
    cons = p.get("consistency")
    if cons and not cons.get("ok"):
        for s in cons["mismatch"]:
            out.append(f"⚠ {s['name']} 資料停在 {s['date']}，與基準 {cons['ref']} 不一致")
    out.append("")
    if stats:
        b = bucket_label(p["vp_score"], stats)
        if b and b["n"] > 0:
            ci = wilson_ci(b["win20"], b["n"])
            ci_txt = f"（95% CI {ci[0]}~{ci[1]}%）" if ci else ""
            out += [
                f"歷史統計（分數 {b['lo']}~{b['hi']} 的過去表現，{stats['period']}）:",
                f"  樣本數:   {b['n']:,}",
                f"  5日勝率:  {b['win5']}%　10日: {b['win10']}%　20日: {b['win20']}%",
                f"  20日勝率區間: {b['win20']}% {ci_txt}",
                f"  平均報酬: {b['avg20']:+.1f}% (20日)",
                f"  最大回撤: {b['mdd']:.1f}%",
            ]
        else:
            out.append("歷史統計: 此分數區間樣本不足")
    else:
        out.append("歷史統計: 未產生（先跑 research/score_history.py）")
    out += ["", "-" * 30]
    out.append(f"趨勢分數: {p['trend_score']}/100（均線結構）")
    out.append(f"結構:     {p['structure']}")
    out.append(f"週線:     {p['weekly']}｜{p['resonance']}")
    out.append(f"動能:     RSI {p['rsi']}　{p['momentum']}")
    vr = f"{p['vol_ratio']}倍" if p["vol_ratio"] is not None else "—"
    out.append(f"量能:     {vr}　{p['vol_tag']}｜{p['skew_tag']}")
    if p["pos_pct"] is not None:
        hi_lo = "偏高" if p["pos_pct"] >= 70 else "偏低" if p["pos_pct"] <= 30 else "中段"
        out.append(f"位置:     60日區間 {p['pos_pct']}%（{hi_lo}）")
    if p.get("bias20") is not None:
        out.append(f"乖離率:   月線{p['bias20']:+}%｜季線{p['bias60']:+}%（{p['bias_tag']}）")
    if p.get("vah"):
        out.append(f"參考價位: 壓力 {p['vah']}｜中軸 {p['poc']}｜支撐 {p['val']}")
    if p.get("ccp") is not None:
        out.append(f"收盤位置: {p['ccp']}%（{p['ccp_tag']}）")
    if p.get("round_level"):
        cl = p.get("round_cluster")
        cl_txt = f"｜叢集 {' / '.join(cl)}" if cl else ""
        out.append(f"整數關卡: {p['round_level']}（距 {p['round_dist']:+}%，{p['round_tag']}）{cl_txt}")
    if p.get("poc_consist") is not None:
        out.append(f"POC一致: 動態{p['dyn_poc']}≈靜態{p['poc']}（差{p['poc_consist']}%，{p['poc_tag']}）")
    oh = p.get("overhead")
    if oh:
        flag = "⚠️逼近" if oh["near"] else "上方"
        out.append(f"上方套牢: {flag} {oh['nearest']}（+{oh['dist_pct']}%，量占{int(oh['vol_share']*100)}%）")
    bo = p.get("breakout")
    if bo and bo["state"] != "none":
        ico = "🟢" if bo["ok"] else "🟡"
        out.append(f"突破帶量: {ico} {bo['v']}")
    inst = p.get("inst")
    if inst:
        out += ["", f"法人買賣超（{inst['date']}）:"]
        out.append("  " + fmt_row("外資", inst["foreign"]))
        out.append("  " + fmt_row("投信", inst["trust"]))
        out.append("  " + fmt_row("自營", inst["dealer"]))
        ic = p.get("inst_consensus")
        if ic:
            out.append(f"  {ic['light']} 法人共識: {ic['status']}（主導{ic['leader']}，合計{ic['net']:+,}張｜{ic['detail']}）")
    evo = p.get("evo") or {}
    if evo:
        out += ["", "-" * 30, "A–G 拆解（仿 Evolution Module）:"]
        ico = lambda ok: "✅" if ok is True else "🔴" if ok is False else "⚪"
        for key in ("A", "B", "C_top", "C_bot", "D", "E", "F", "G", "H", "BO"):
            e = evo.get(key)
            if e:
                out.append(f"  {ico(e['ok'])} {e['k']}: {e['v']}")
    pb = p.get("playbook")
    if pb:
        out += ["", "-" * 30, "L2 規則化劇本（綁回測·防呆）:"]
        if pb["cards"]:
            for cd in pb["cards"]:
                st = cd.get("stat")
                out.append(f"  ▸ {cd['name']}｜{cd['cond']}")
                if st:
                    out.append(f"    訓練 {st['n_train']:,}筆勝率{st['win_train']}%"
                               f"｜驗證 {st['n_val']:,}筆勝率{st['win_val']}%"
                               f"｜平均{st['avg20']:+}% 最差{st['mdd']}%")
                tag = {"ok": "✅可參考", "low_sample": "⚠樣本不足", "unvalidated": "⚠未驗證",
                       "overfit": "⚠過擬合", "no_edge": "🔴無 edge"}.get(cd["status"], cd["status"])
                out.append(f"    {tag}{('：' + cd['msg']) if cd['msg'] else ''}")
        else:
            out.append("  今日無模板觸發")
        if pb["no_trade"]:
            out.append("  不交易原因: " + "　".join(f"☑{r['label']}" for r in pb["no_trade"]))
    pos = p.get("position")
    if pos:
        out += ["", "-" * 30, "L3 持倉風控:"]
        out.append(f"  進場 {pos['entry_price']}｜現價 {pos['cur_price']}｜未實現 {pos['unreal_pct']:+}%")
        if pos.get("bench_pct") is not None:
            out.append(f"  同期 {pos['bench_sid']} {pos['bench_pct']:+}%｜超額 α {pos['alpha_pct']:+}%")
        out.append(f"  生效出場 {pos['effective_exit']}（{pos['effective_by']}）｜距觸發 {pos['dist_pct']:+}%　{pos['light']} {pos['state']}")
        out.append("  （趨勢燈見上方；持倉卡=手動建倉才顯示的細部風控,選配）")
    bw = p.get("behavior")
    if bw:
        out += ["", "-" * 30, "⚠ 行為守門:"]
        for w in bw:
            out.append(f"  • {w['msg']}")
    out += ["", "-" * 30, "評語:", comment(p), "", line]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sid")
    ap.add_argument("--cache", default=None)
    a = ap.parse_args()
    cache = find_cache(a.cache)
    bars = load_bars(a.sid.upper(), cache)
    p = compute_panel(bars)
    try:
        p["inst"] = get_inst(a.sid.upper())
    except Exception:
        p["inst"] = None
    tv = bars[-1]["volume"] / 1000 if bars and bars[-1].get("volume") else None
    p["inst_consensus"] = consensus(p["inst"], total_vol=tv) if p.get("inst") else None
    # 功能A：法人(T86)資料源各自比對新鮮度(T86 約 16:00 公布，盤中本就落後一日)
    p["inst_fresh"] = data_freshness(p["inst"]["date"]) if p.get("inst") else None
    # 功能七：各資料源 vs 基準 bar 一致性
    p["consistency"] = consistency_check(p.get("ref_date"), p.get("inst"))
    # L2 規則化劇本（最後一根已收盤 K）
    p["playbook"] = playbook_view(detect_playbook(bars, -1), load_pb_stats())
    # L3 持倉風控 + 同期 0050 α（該檔有持倉才回）
    sid = a.sid.upper()
    risk = position_risk(sid, bars[-1]["close"], today_high=bars[-1].get("high"))
    p["position"] = attach_alpha(risk, _load_bench(cache)) if risk else None
    # P3 行為守門：追高/凹單(單股) + 頻率(全域跨檔，餵全部 closed)
    pos_d = load_positions()
    p["behavior"] = BH.behavior_checks(
        p=p, risk=p["position"], closed_records=pos_d["closed"])
    print(render(sid, p, load_stats()))


if __name__ == "__main__":
    main()
