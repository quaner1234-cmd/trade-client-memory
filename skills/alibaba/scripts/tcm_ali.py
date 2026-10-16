#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阿里巴巴国际站询盘整理 Skill — CLI.

Subcommands: init, doctor, status, extract, ingest, report, render, brief,
verify, audit.

Design goals (from the verified production sync):
  * everything repeatable is deterministic code, never LLM tokens
  * append-only: nothing is ever deleted or overwritten
  * no writes to the platform ever (read-only)
  * no real customer data can leak into this skill dir or the release package
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tcm_ali_lib as lib  # noqa: E402

CST = timezone(timedelta(hours=8))
SKILL_DIR = Path(__file__).resolve().parent.parent

AGENT_BEGIN = "<!-- TCM:ALI:AGENT:BEGIN -->"
AGENT_END = "<!-- TCM:ALI:AGENT:END -->"

# --- JS used against the seller console (read-only selectors) ----------------
LIST_JS = r"""(() => {
  const out = [];
  for (const a of document.querySelectorAll('a[href*="maDetail"]')) {
    const m = (a.getAttribute('href')||'').match(/imInquiryId=(\d+)/);
    if (!m) continue;
    const nameEl = a.querySelector('.aui2-grid-name');
    const msgEl = a.querySelector('.buyer-chat-msg');
    const pads = [...a.querySelectorAll('.m-header-padding')].map(e => (e.textContent||'').replace(/\s+/g,' ').trim());
    out.push({
      inquiry_id: m[1],
      name: nameEl ? nameEl.textContent.trim() : '',
      last_msg: msgEl ? msgEl.textContent.trim() : '',
      updated: pads.find(t => /Updated on/i.test(t)) || '',
      href: a.getAttribute('href') || ''
    });
  }
  return JSON.stringify(out);
})()"""

MSG_JS = r"""(() => {
  const items = [];
  for (const el of document.querySelectorAll('.message-item-wrapper')) {
    let info = {};
    try { info = JSON.parse(el.getAttribute('data-expinfo') || '{}'); } catch(e) {}
    if (!info.messageId) continue;
    const cls = el.className || '';
    const side = cls.includes('item-left') ? 'buyer' : (cls.includes('item-right') ? 'seller' : 'system');
    items.push({
      side: side,
      messageId: String(info.messageId),
      messageType: info.messageType,
      sendTime: info.sendTime,
      dataOriginal: el.getAttribute('data-original') || ''
    });
  }
  return JSON.stringify({count: items.length, items: items});
})()"""

SHOWMORE_JS = r"""(() => {
  const els = [...document.querySelectorAll('a.show-more, .show-more')].filter(e => e.offsetParent !== null);
  let n = 0;
  for (const b of els.slice(0, 3)) { try { b.click(); n++; } catch(e){} }
  return n;
})()"""

URL_SNIPPET_JS = "JSON.stringify({url: location.href.slice(0,160), logged: !/login|signin/i.test(location.href)})"

LIST_URL = "https://message.alibaba.com/message/default.htm#feedback/all"


# ---------------------------------------------------------------------------
# paths & logging

class Paths:
    def __init__(self, base: str):
        self.base = Path(base).expanduser().resolve()
        self.data = self.base / "内部数据"
        self.raw = self.data / "raw"
        self.clients = self.base / "客户档案"
        self.pending = self.clients / "_待确认"
        self.state = self.data / "state.json"
        self.fetch = self.data / "fetch-status.json"
        self.log = self.data / "run.log"
        self.overview = self.clients / "客户概览.md"

    def ensure(self):
        for d in (self.data, self.raw, self.clients, self.pending):
            d.mkdir(parents=True, exist_ok=True)
        return self


def log(p: Paths, msg: str):
    try:
        p.data.mkdir(parents=True, exist_ok=True)
        line = f"[{datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        with open(p.log, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def default_state(owner: str = "") -> dict:
    return {
        "version": 1,
        "owner": owner,
        "created_at": datetime.now(CST).isoformat(timespec="seconds"),
        "floor_watermark": None,
        "customers": {},        # slug -> {name, iid, dir, first_seen, last_seen, seen_ids, confirmed}
        "iid_to_slug": {},      # alibaba inquiry id -> slug
        "pending": {},          # iid -> {name, reason, first_seen}
        "runs": [],
    }


def load_state(p: Paths) -> dict:
    st = lib.load_json(p.state, None)
    return st if isinstance(st, dict) else default_state()


def save_state(p: Paths, st: dict):
    lib.save_json(p.state, st)


# ---------------------------------------------------------------------------
# browser bridge

class Bridge:
    """Thin wrapper over whichever browser bridge is available.

    Verified: Hermes `bsk` CLI. Anything else is untested — `doctor` reports it
    and every extract that cannot reach the page stops instead of guessing.
    """

    def __init__(self, runner=None, log_fn=None):
        self.runner = runner
        self.log_fn = log_fn or (lambda m: None)
        self.session = None
        self.name = "none"
        self.exe = ""

    @classmethod
    def detect(cls, log_fn=None):
        bsk = os.environ.get("TCM_ALI_BSK") or shutil.which("bsk") or shutil.which("bsk.exe")
        if bsk:
            b = cls(log_fn=log_fn)
            b.name = "bsk"
            b.exe = bsk
            return b
        b = cls(log_fn=log_fn)
        b.name = "none"
        return b

    def _run(self, *args, timeout=120):
        cmd = [self.exe, *args, "--json"]
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)

    def start(self):
        r = self._run("session", "start", "--no-focus", "--name", "tcm-ali")
        try:
            self.session = json.loads(r.stdout)["session_id"]
        except Exception:
            self.log_fn(f"session start failed: {(r.stdout or r.stderr or '')[:200]}")
            return None
        return self.session

    def stop(self):
        if not self.session:
            return
        try:
            self._run("session", "stop", self.session, timeout=60)
        except Exception:
            pass
        self.session = None

    def js(self, expr, timeout=120):
        if not self.session:
            return None
        r = self._run("js", expr, "--session", self.session, timeout=timeout)
        if r.returncode != 0:
            return None
        out = (r.stdout or "").strip()
        try:
            return json.loads(out)
        except Exception:
            return out or None

    def goto(self, url, timeout=180):
        if not self.session:
            return False
        r = self._run("navigate", url, "--session", self.session, timeout=timeout)
        return r.returncode == 0


def _poll_list(br, tries=6, interval=4):
    for _ in range(tries):
        v = br.js(LIST_JS)
        if isinstance(v, list) and v:
            return v
        time.sleep(interval)
    return []


# ---------------------------------------------------------------------------
# commands

def cmd_init(p: Paths, args, br=None):
    p.ensure()
    if not p.state.exists():
        save_state(p, default_state(args.owner or ""))
    print(f"已初始化：<base> = {p.base}")
    print(f"  归档目录：<base>/客户档案/")
    print(f"  状态数据：<base>/内部数据/")
    return 0


def cmd_doctor(p: Paths, args, br=None):
    ok = True
    p.ensure()
    writable = True
    try:
        probe = p.data / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as e:
        writable = False
        print(f"❌ <base> 不可写：{e}")
        ok = False
    if writable:
        print("✅ <base> 可写")

    bridge = Bridge.detect()
    if bridge.name == "bsk":
        print(f"✅ 浏览器桥接可用：bsk（{bridge.exe}）")
    else:
        print("⚠️  未检测到浏览器桥接（bsk）。")
        print("   → 本 Skill 在 Hermes + bsk 环境验证过；其他环境未验证。")
        print("   → 如使用其他桥接，请设置 TCM_ALI_BSK=/path/to/bridge 并先跑离线测试。")
        ok = False

    st = p.state if p.state.exists() else None
    print(f"{'✅' if st else '—'} 状态文件：{'已存在' if st else '未初始化（先跑 init）'}")
    if st:
        s = load_state(p)
        print(f"   已建档客户 {len(s.get('customers', {}))} 个，待确认 {len(s.get('pending', {}))} 个")
    return 0 if ok else 1


def cmd_status(p: Paths, args, br=None):
    if not p.state.exists():
        print("尚未初始化。先跑：python scripts/tcm_ali.py --base <base> init")
        return 1
    s = load_state(p)
    fs = lib.load_json(p.fetch, None) if p.fetch.exists() else None
    print(f"<base>：{p.base}")
    print(f"负责人显示名：{s.get('owner') or '（未设置，ingest 时用 --owner 指定）'}")
    print(f"水位线：{s.get('floor_watermark') or '（未设置）'}")
    print(f"客户数：{len(s.get('customers', {}))}；待确认数：{len(s.get('pending', {}))}")
    if isinstance(fs, dict):
        if not fs.get("finished"):
            print("⚠️  上次抓取未完成 — 直接重跑 extract，已抓到的不会重复。")
        else:
            print(f"上次抓取：{fs.get('finished_at', '?')} 完成")
    runs = s.get("runs") or []
    if runs:
        r = runs[-1]
        print(f"最近一次运行：{r.get('at')} — 新增 {r.get('fresh', 0)} 条")
    return 0


def cmd_extract(p: Paths, args, br=None):
    """Read-only scrape. Never writes conclusions when not logged in."""
    p.ensure()
    bridge = br or Bridge.detect(log_fn=lambda m: log(p, m))
    if bridge.name == "none":
        print("❌ 无可用浏览器桥接，无法读取平台。先跑 doctor。")
        return 1

    st = load_state(p)
    since = args.since or st.get("floor_watermark")
    ts = datetime.now(CST).strftime("%Y%m%d-%H%M%S")
    lib.save_json(p.fetch, {"finished": False, "started_at": datetime.now(CST).isoformat(timespec="seconds")})
    log(p, f"=== extract start (max_conv={args.max_conv}, since={since}) ===")

    sid = bridge.start()
    if not sid:
        lib.save_json(p.fetch, {"finished": False, "note": "session start failed"})
        print("❌ 无法启动浏览器会话。请确认 Chrome 可自动化控制。")
        return 1

    convs = []
    try:
        bridge.goto(LIST_URL)
        rows = _poll_list(bridge)
        if not rows:
            probe = bridge.js(URL_SNIPPET_JS)
            lib.save_json(p.fetch, {"finished": False, "note": "list not loaded", "probe": probe})
            print("⚠️  列表未加载：通常是**未登录阿里卖家后台**或页面改版。")
            print("   → 请在 Chrome 里登录 message.alibaba.com 后重试；未登录不等于没有新询盘。")
            return 1

        lib.save_json(p.raw / f"list-{ts}.json", rows)
        print(f"列表加载 {len(rows)} 个会话")

        cand = []
        now = datetime.now(CST)
        for r in rows:
            iid = str(r.get("inquiry_id") or "").strip()
            if not iid:
                continue
            d, _t = lib.parse_updated(r.get("updated", ""), now)
            if since and d and d < since:
                continue
            cust = (st.get("customers") or {}).get((st.get("iid_to_slug") or {}).get(iid, ""))
            # "Updated on" has day precision, so equality does NOT mean "no news":
            # a same-day message must still be re-read (ingest dedupes by message
            # id, so re-reading is idempotent). Only strictly older rows skip.
            if cust and not args.force and d and cust.get("last_seen") and d < cust["last_seen"][:10]:
                continue
            cand.append(r)
        print(f"待处理会话 {len(cand)} 个（上限 {args.max_conv}）")

        for r in cand[: args.max_conv]:
            iid = str(r.get("inquiry_id"))
            href = r.get("href") or ""
            if href.startswith("/"):
                href = "https://message.alibaba.com" + href
            bridge.goto(href)
            got = []
            for _ in range(5):
                v = bridge.js(MSG_JS)
                if isinstance(v, dict) and v.get("count"):
                    got = v.get("items") or []
                    break
                time.sleep(4)
            bridge.js(SHOWMORE_JS)  # reveal older messages if present
            time.sleep(2)
            v2 = bridge.js(MSG_JS)
            if isinstance(v2, dict) and len(v2.get("items") or []) > len(got):
                got = v2["items"]
            lib.save_json(p.raw / f"conv-{iid}-{ts}.json",
                          {"inquiry_id": iid, "name": r.get("name", ""),
                           "updated": r.get("updated", ""), "items": got})
            print(f"  会话 {iid}：{len(got)} 条")
            convs.append(iid)
        lib.save_json(p.fetch, {"finished": True,
                                "finished_at": datetime.now(CST).isoformat(timespec="seconds"),
                                "conversations": convs})
        print(f"\n✅ 抓取完成，{len(convs)} 个会话原文已存入 <base>/内部数据/raw/")
        return 0
    finally:
        bridge.stop()


def _read_extracted(p: Paths, st: dict):
    """Load raw files not yet ingested (tracked by ingested file list)."""
    done = set(st.get("ingested_raws") or [])
    out = []
    for f in sorted(p.raw.glob("conv-*.json")):
        if f.name in done:
            continue
        d = lib.load_json(f, None)
        if isinstance(d, dict):
            d["_file"] = f.name
            out.append(d)
    return out


def cmd_ingest(p: Paths, args, br=None):
    p.ensure()
    st = load_state(p)
    if args.owner:
        st["owner"] = args.owner
    owner = st.get("owner") or "我方"
    raws = _read_extracted(p, st)
    if not raws:
        print("没有新的抓取原文。先跑 extract。")
        return 0

    created, updated, pending_n, new_msgs = [], [], [], 0
    for conv in raws:
        iid = str(conv.get("inquiry_id") or "")
        name = (conv.get("name") or "").strip() or f"未命名-{iid}"
        items = conv.get("items") or []
        msgs = [m for m in (lib.normalize_message(x, name, owner) for x in items) if m]
        msgs = lib.dedupe_msgs(msgs)
        if not msgs:
            continue

        slug = (st.get("iid_to_slug") or {}).get(iid)
        # name-based match only when unambiguous
        if not slug:
            hits = [s for s, c in (st.get("customers") or {}).items()
                    if c.get("name", "").strip().lower() == name.lower()]
            if len(hits) == 1:
                slug = hits[0]
            elif len(hits) > 1:
                _write_pending(p, st, iid, name, msgs, "ambiguous-name")
                pending_n.append(name)
                continue

        if slug:
            cdir, seen = _append_existing(p, st, slug, msgs)
            if cdir:
                updated.append(name)
                new_msgs += len(seen)
        else:
            if lib.has_buyer_substance(msgs):
                slug = _create_client(p, st, name, iid, msgs)
                created.append(name)
                new_msgs += len(msgs)
            else:
                _write_pending(p, st, iid, name, msgs, "no-buyer-substance")
                pending_n.append(name)
        st.setdefault("ingested_raws", [])
        st["ingested_raws"].append(conv["_file"])

    st.setdefault("runs", []).append({
        "at": datetime.now(CST).isoformat(timespec="seconds"),
        "created": len(created), "updated": len(updated),
        "pending": len(pending_n), "new_messages": new_msgs,
    })
    st["runs"] = st["runs"][-50:]
    save_state(p, st)
    log(p, f"ingest: created={len(created)} updated={len(updated)} pending={len(pending_n)} fresh={new_msgs}")
    print(f"✅ ingest 完成：新建 {len(created)}，更新 {len(updated)}，待确认 {len(pending_n)}，新消息 {new_msgs} 条")
    for n in created:
        print(f"   + 新客户：{n}")
    for n in pending_n:
        print(f"   ? 待确认：{n}")
    return 0


def _client_dirname(name: str, country: str) -> str:
    c = (country or "未核实").replace("（平台字段，未核实）", "")
    return f"{name} - {c}".strip(" -")


def _create_client(p: Paths, st: dict, name: str, iid: str, msgs: list) -> str:
    slug = lib.slugify(name)
    base_slug = slug
    n = 2
    while slug in (st.get("customers") or {}):
        slug = f"{base_slug}-{n}"
        n += 1
    cdir = p.clients / _client_dirname(name, lib.country_hint(msgs))
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "附件").mkdir(exist_ok=True)
    rec = cdir / "客户沟通记录.md"
    if not rec.exists():
        rec.write_text(_record_header(name, iid, msgs), encoding="utf-8")
    _append_messages(rec, msgs)
    prog = cdir / "客户进展.md"
    if not prog.exists():
        prog.write_text(_progress_template(name), encoding="utf-8")
    st["customers"][slug] = {
        "name": name, "iid": iid, "dir": cdir.name,
        "first_seen": (msgs[0].get("sent_at") or "")[:10],
        "last_seen": (msgs[-1].get("sent_at") or "")[:10],
        "seen_ids": [m["source_id"] for m in msgs][-400:],
        "confirmed": False, "country": lib.country_hint(msgs),
    }
    st.setdefault("iid_to_slug", {})[iid] = slug
    return slug


def _append_existing(p: Paths, st: dict, slug: str, msgs: list):
    c = st["customers"][slug]
    cdir = p.clients / c["dir"]
    if not cdir.exists():
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "附件").mkdir(exist_ok=True)
    seen = set(lib.norm_msg_id(x) for x in (c.get("seen_ids") or []))
    fresh = [m for m in msgs if m["source_id"] not in seen]
    rec = cdir / "客户沟通记录.md"
    if not rec.exists():
        rec.write_text(_record_header(c["name"], c.get("iid", ""), msgs), encoding="utf-8")
    if fresh:
        _append_messages(rec, fresh)
        c["seen_ids"] = list(dict.fromkeys((c.get("seen_ids") or []) +
                                           [m["source_id"] for m in fresh]))[-400:]
        c["last_seen"] = (fresh[-1].get("sent_at") or "")[:10]
    return cdir, fresh


def _write_pending(p: Paths, st: dict, iid: str, name: str, msgs: list, reason: str):
    p.pending.mkdir(parents=True, exist_ok=True)
    f = p.pending / f"{lib.slugify(name)}-{iid}.md"
    nb, ns_, ny = lib.dir_counts(msgs)
    body = [
        f"# 待确认线索：{name}",
        "",
        f"- 平台会话 ID：`{iid}`",
        f"- 原因：{ {'ambiguous-name': '库中同名客户多于一个，无法唯一确定归属', 'no-buyer-substance': '可见消息无买家实质内容（仅系统分配/移交/我方追问）'}.get(reason, reason) }",
        f"- 可见消息：{len(msgs)} 条（买家 {nb} / 我方 {ns_} / 系统 {ny}）",
        f"- 买家实质消息：{'有' if lib.has_buyer_substance(msgs) else '无'}",
        f"- 国家：{lib.country_hint(msgs)}",
        f"- 首次记录：{datetime.now(CST).strftime('%Y-%m-%d %H:%M')}",
        "",
        "## 消息原文",
        "",
    ]
    for m in msgs:
        who = {"received": "客户", "sent": "我方", "system": "系统"}.get(m["direction"], "?")
        body.append(f"- `[{(m.get('sent_at') or '')[:16].replace('T', ' ')}]` **{who}**（id={m['source_id']}）：{(m.get('body') or '')[:400]}")
    body += ["", "## 需要人工判断", "",
             "- [ ] 该客户是否已有档案？若有，填入正确归属",
             "- [ ] 是否构成有效询盘？",
             "- [ ] 确认后：把内容并入正式客户档案，或标记为无效线索", ""]
    f.write_text("\n".join(body), encoding="utf-8")
    st.setdefault("pending", {})[iid] = {"name": name, "reason": reason,
                                         "at": datetime.now(CST).strftime("%Y-%m-%d %H:%M")}


def _record_header(name: str, iid: str, msgs: list) -> str:
    nb, ns_, ny = lib.dir_counts(msgs)
    return "\n".join([
        f"# {name} ｜ 客户沟通记录",
        "",
        f"- 来源：阿里巴巴国际站（会话 ID `{iid}`）",
        f"- 覆盖范围：本档案由脚本追加生成，仅含**已抓取并处理**的消息；更早历史可能不在其中。",
        f"- 统计：{len(msgs)} 条（买家 {nb} / 我方 {ns_} / 系统 {ny}）",
        f"- 国家：{lib.country_hint(msgs)}（平台字段，未核实）",
        "- 说明：平台显示的数量不等于确认订单；公司/品牌未凭图片或名字推定。",
        "",
        "## 沟通时间线",
        "",
    ])


def _progress_template(name: str) -> str:
    return "\n".join([
        f"# {name} ｜ 客户进展",
        "",
        AGENT_BEGIN,
        "## 客户情况",
        "（待 Agent 填写）",
        "",
        "## 当前阶段",
        "初始询盘",
        "",
        "## 下一行动",
        "（待 Agent 填写：谁需要做什么）",
        AGENT_END,
        "",
    ])


def _append_messages(rec: Path, msgs: list):
    """Append-only: never rewrite existing lines."""
    existing = rec.read_text(encoding="utf-8") if rec.exists() else ""
    have = set(re.findall(r"id=([0-9A-Za-z]+)", existing))
    lines = []
    for m in msgs:
        if m["source_id"] in have:
            continue
        who = {"received": "客户", "sent": "我方", "system": "系统"}.get(m["direction"], "?")
        stamp = (m.get("sent_at") or "")[:16].replace("T", " ")
        body = (m.get("body") or "").replace("\n", " / ")
        lines.append(f"- `[{stamp}]` **{who}**（id={m['source_id']}）：{body}")
        for u in (m.get("images") or []):
            lines.append(f"  - 图片：{u}")
    if lines:
        with open(rec, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def cmd_report(p: Paths, args, br=None):
    st = load_state(p)
    slug = args.slug
    c = (st.get("customers") or {}).get(slug)
    if not c:
        print(f"❌ 未找到 slug：{slug}。可用：{', '.join(st.get('customers', {})) or '（无）'}")
        return 1
    cdir = p.clients / c["dir"]
    prog = cdir / "客户进展.md"
    if not prog.exists():
        prog.write_text(_progress_template(c["name"]), encoding="utf-8")
    txt = prog.read_text(encoding="utf-8")

    block = "\n".join([
        "## 客户情况",
        args.situation or "（未填写）",
        "",
        "## 当前阶段",
        args.stage or "初始询盘",
        "",
        "## 下一行动",
        args.next or "（未填写：谁需要做什么）",
    ])
    if AGENT_BEGIN in txt and AGENT_END in txt:
        pre, rest = txt.split(AGENT_BEGIN, 1)
        _mid, post = rest.split(AGENT_END, 1)
        new = pre + AGENT_BEGIN + "\n" + block + "\n" + AGENT_END + post
    else:
        new = txt.rstrip() + "\n\n" + AGENT_BEGIN + "\n" + block + "\n" + AGENT_END + "\n"
    prog.write_text(new, encoding="utf-8")

    c["stage"] = args.stage
    c["priority"] = args.priority
    c["updated_at"] = datetime.now(CST).strftime("%Y-%m-%d %H:%M")
    save_state(p, st)
    print(f"✅ 已更新进展：{c['name']}（{args.stage} / {args.priority}）")
    return 0


def cmd_render(p: Paths, args, br=None):
    st = load_state(p)
    order = {"urgent": 0, "action": 1, "waiting": 2, "unconfirmed": 3}
    rows = []
    for slug, c in sorted((st.get("customers") or {}).items(),
                          key=lambda kv: (order.get(kv[1].get("priority", "waiting"), 9), kv[1]["name"])):
        prog = (p.clients / c["dir"] / "客户进展.md")
        stage = c.get("stage") or "初始询盘"
        sit, nxt = "", ""
        if prog.exists():
            t = prog.read_text(encoding="utf-8")
            m1 = re.search(r"## 客户情况\n(.*?)(?=\n## |\Z)", t, re.S)
            m2 = re.search(r"## 下一行动\n(.*?)(?=\n## |" + re.escape(AGENT_END) + r"|\Z)", t, re.S)
            sit = (m1.group(1).strip() if m1 else "").replace("\n", " ")[:120]
            nxt = (m2.group(1).strip() if m2 else "").replace("\n", " ")[:120]
        rows.append((c["name"], c.get("country", "未核实"), stage,
                     c.get("priority", "waiting"), c.get("last_seen", ""), sit, nxt))

    lines = ["# 客户概览", "",
             f"> 生成时间：{datetime.now(CST).strftime('%Y-%m-%d %H:%M')}　"
             f"> 客户 {len(rows)} 个　待确认 {len(st.get('pending', {}))} 个",
             "> 数据来源：阿里巴巴国际站（只读同步）。数量字段不代表确认订单。", "",
             "| 客户 | 国家 | 阶段 | 优先级 | 最后更新 | 客户情况 | 下一行动 |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for n, c_, s, pr, ls, sit, nxt in rows:
        lines.append(f"| {n} | {c_} | {s} | {pr} | {ls} | {sit or '—'} | {nxt or '—'} |")
    pend = st.get("pending") or {}
    if pend:
        lines += ["", "## 待确认线索", ""]
        for iid, v in pend.items():
            lines.append(f"- **{v.get('name')}**（会话 `{iid}`）— {v.get('reason')}　{v.get('at')}")
    p.clients.mkdir(parents=True, exist_ok=True)
    p.overview.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"✅ 概览已生成：{p.overview}（{len(rows)} 个客户）")
    return 0


def cmd_brief(p: Paths, args, br=None):
    st = load_state(p)
    order = {"urgent": 0, "action": 1, "waiting": 2, "unconfirmed": 3}
    items = sorted((st.get("customers") or {}).values(),
                   key=lambda c: (order.get(c.get("priority", "waiting"), 9), c["name"]))
    if not items:
        print("还没有客户档案。先跑 extract → ingest。")
        return 0
    for c in items:
        prog = p.clients / c["dir"] / "客户进展.md"
        sit = nxt = ""
        if prog.exists():
            t = prog.read_text(encoding="utf-8")
            m1 = re.search(r"## 客户情况\n(.*?)(?=\n## |\Z)", t, re.S)
            m2 = re.search(r"## 下一行动\n(.*?)(?=\n## |" + re.escape(AGENT_END) + r"|\Z)", t, re.S)
            sit = (m1.group(1).strip() if m1 else "")
            nxt = (m2.group(1).strip() if m2 else "")
        print(f"> **{c['name']} · {c.get('country','未核实')}｜{c.get('stage','初始询盘')}**（{c.get('priority','waiting')}）")
        print(f"> 当前情况：{sit or '（待填写）'}")
        print(f"> 下一步：{nxt or '（待填写）'}")
        print()
    pend = st.get("pending") or {}
    if pend:
        print(f"— 另有 {len(pend)} 条待确认线索，见 客户档案/_待确认/")
    return 0


def cmd_verify(p: Paths, args, br=None):
    fails, warns = [], []
    st = load_state(p)
    if not p.state.exists():
        print("❌ 未初始化")
        return 1
    for slug, c in (st.get("customers") or {}).items():
        cdir = p.clients / c["dir"]
        if not cdir.exists():
            fails.append(f"客户目录缺失：{c['dir']}")
            continue
        rec = cdir / "客户沟通记录.md"
        if not rec.exists():
            fails.append(f"缺 客户沟通记录.md：{c['dir']}")
        prog = cdir / "客户进展.md"
        if not prog.exists():
            fails.append(f"缺 客户进展.md：{c['dir']}")
        else:
            t = prog.read_text(encoding="utf-8")
            if AGENT_BEGIN not in t or AGENT_END not in t:
                fails.append(f"客户进展.md 缺 Agent 区域标记：{c['dir']}")
            elif "待 Agent 填写" in t or "（未填写" in t:
                warns.append(f"进展未填写：{c['name']}")
        if rec.exists():
            t = rec.read_text(encoding="utf-8")
            for bad in (re.findall(r"[A-Za-z]:\\\\[^\s）)]+", t) +
                        re.findall(r"/(?:Users|home)/[^\s）)]+", t)):
                fails.append(f"绝对路径泄漏于 {c['dir']}：{bad[:60]}")
    # pending must be explained
    for iid, v in (st.get("pending") or {}).items():
        f = list(p.pending.glob(f"*-{iid}.md"))
        if not f:
            fails.append(f"待确认线索 {iid} 无对应文件（遗漏）")
    if st.get("owner") is None:
        warns.append("未设置负责人显示名（ingest --owner）")

    for w in warns:
        print(f"⚠️  {w}")
    for f in fails:
        print(f"❌ {f}")
    if fails:
        print(f"\nverify 失败：{len(fails)} 项")
        return 1
    print("✅ verify 通过（" + (f"{len(warns)} 项提醒" if warns else "无问题") + "）")
    return 0


PRIVACY_PATTERNS = [
    ("电子邮件", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")),
    # 手机号必须是"孤立"的：前后不是数字，也不紧跟 id=/-/冒号，避免把
    # 平台会话号、消息 ID 这类长数字误判成手机号（曾误报样例里的 13xxxxxxxxx）。
    ("国内手机号", re.compile(r"(?<![0-9A-Za-z=_/-])1[3-9]\d{9}(?![0-9A-Za-z-])")),
    ("Notion token", re.compile(r"ntn_[A-Za-z0-9]{20,}")),
    ("32位委托地址", re.compile(r"[0-9a-f]{32}@delegate\.alibaba\.com")),
    ("阿里云 Key", re.compile(r"(LTAI|sk-)[A-Za-z0-9]{12,}")),
    ("URL 中的 token", re.compile(r"[?&](?:token|access_token|key)=[A-Za-z0-9_\-]{8,}")),
]

# 平台自带的长数字 ID（会话号/消息号）不是隐私，但真实号码是的：出现在
# inquiry_id / messageId / source_id / id= 上下文的一律放行。
RE_NUMERIC_ID_CTX = re.compile(
    r"(inquiry_id|messageId|source_id|\bid\b|imInquiryId|_file)\s*[\"']?\s*[:=]\s*[\"']?\d{6,}", re.I)


def cmd_audit(p: Paths, args, br=None):
    """Scan skill dir (default) for anything that must not ship."""
    root = Path(args.audit_path).resolve() if getattr(args, "audit_path", None) else SKILL_DIR
    hits = []
    skip_dirs = {".git", "__pycache__", "内部数据", "客户档案"}
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        if any(part in skip_dirs for part in f.relative_to(root).parts):
            continue
        if f.suffix.lower() in {".png", ".pdf", ".zip", ".jpg", ".jpeg"}:
            continue
        try:
            t = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        # 按行判断：长数字若处在平台 ID 上下文（会话号/消息号）则不算隐私
        rel = f.relative_to(root)
        for label, rx in PRIVACY_PATTERNS:
            for m in rx.finditer(t):
                val = m.group(0)
                if "@example." in val or "example.com" in val or "@example.org" in val:
                    continue
                line_start = t.rfind("\n", 0, m.start()) + 1
                line_end = t.find("\n", m.end())
                line = t[line_start: line_end if line_end != -1 else len(t)]
                if label in ("国内手机号",) and RE_NUMERIC_ID_CTX.search(line):
                    continue
                line_no = t[: m.start()].count("\n") + 1
                hits.append((str(rel), line_no, label, val[:40]))
    # runtime data must never live inside the skill
    for name in ("客户档案", "内部数据"):
        if (root / name).exists():
            hits.append((name, 0, "运行时数据", "skill 目录内不得存在真实数据目录"))
    if hits:
        print(f"❌ 隐私扫描发现 {len(hits)} 处：")
        for path, ln, label, val in hits[:60]:
            print(f"   {path}:{ln}  [{label}] {val}")
        return 1
    print(f"✅ 隐私扫描通过：{root} 内未发现客户数据/凭证痕迹")
    return 0


# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="tcm_ali", description="阿里巴巴国际站询盘整理")
    ap.add_argument("--base", default=".", help="用户数据根目录（默认当前目录）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").add_argument("--owner", default="")
    sub.add_parser("doctor")
    sub.add_parser("status")

    e = sub.add_parser("extract")
    e.add_argument("--max-conv", type=int, default=20)
    e.add_argument("--since", default=None)
    e.add_argument("--force", action="store_true")

    i = sub.add_parser("ingest")
    i.add_argument("--owner", default=None)

    r = sub.add_parser("report")
    r.add_argument("slug")
    r.add_argument("--stage", default="初始询盘")
    r.add_argument("--priority", default="action",
                   choices=["urgent", "action", "waiting", "unconfirmed"])
    r.add_argument("--situation", default=None)
    r.add_argument("--next", dest="next_", default=None)

    sub.add_parser("render")
    sub.add_parser("brief")
    sub.add_parser("verify")
    a = sub.add_parser("audit")
    a.add_argument("--audit-path", dest="audit_path", default=None)

    args = ap.parse_args(argv)
    p = Paths(args.base)

    if args.cmd == "report":
        args.next = args.next_
    fn = {
        "init": cmd_init, "doctor": cmd_doctor, "status": cmd_status,
        "extract": cmd_extract, "ingest": cmd_ingest, "report": cmd_report,
        "render": cmd_render, "brief": cmd_brief,
        "verify": cmd_verify, "audit": cmd_audit,
    }[args.cmd]
    return fn(p, args)


if __name__ == "__main__":
    sys.exit(main())
