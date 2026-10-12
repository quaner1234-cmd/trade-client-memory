#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tcm.py — Trade Client Memory (外贸客户记忆助手) deterministic core. v3

v3 redesign driven by the real WorkBuddy mailbox experiment:
  * user-visible output is 客户档案/<公司 - 国家>/客户沟通记录.md + 附件/
    plus one 客户概览.md — not JSON, not loose .eml
  * attachments are extracted (hash-deduped, signature-logo flagged) and
    linked from the timeline; missing payloads are stated, never fabricated
  * bodies are cleaned for display: duplicate historical quotes collapsed,
    signatures/disclaimers cut, point-by-point quoted answers preserved
  * status summaries (chat brief + 客户概览.md) come from ONE source:
    state["reports"], written by the Agent via `report`
  * user data lives in <base>/客户档案 and <base>/内部数据 — isolated from
    the skill installation, git repo and release ZIP
  * fetch-status.json bridges the terminal fetch (imap_fetch.py) and the
    Agent: progress, ETA, coverage and a clear "邮件读取完成" flag

Commands:
  init ingest owner map-customer report render brief done status verify
  doctor launcher

Stdlib only. Read-only toward mail servers; this script only processes
files already on disk.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

STATE_VERSION = 2
FREE_DOMAINS = {
    "gmail.com", "googlemail.com", "163.com", "126.com", "qq.com", "foxmail.com",
    "hotmail.com", "outlook.com", "live.com", "yahoo.com", "icloud.com",
}
REQUIRED_MSG_FIELDS = ("id", "hash", "file", "from", "to", "date", "subject")
AGENT_SECTIONS = ("## 客户情况", "## 关键已确认事实", "## 当前阶段", "## 下一行动")
PRIORITY_ORDER = ("urgent", "action", "waiting", "unconfirmed")
PRIORITY_TITLES = {
    "urgent": "需要业务员行动(紧急)",
    "action": "需要业务员行动",
    "waiting": "等待客户",
    "unconfirmed": "信息待核实",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def die(msg: str, code: int = 2) -> None:
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(code)


def out(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# --------------------------------------------------------------------- paths --

def guard_base(base: Path) -> None:
    """Refuse to put user data inside the skill installation directory /
    its git worktree — real customer mail must never be able to leak into a
    commit or the release ZIP by accident. Fictional examples under
    samples/ are the only exception (they are part of the repo)."""
    skill_root = Path(__file__).resolve().parent.parent
    try:
        rel = base.relative_to(skill_root)
    except ValueError:
        return
    if rel.parts and rel.parts[0] == "samples":
        return
    die(f"拒绝:数据目录 {base} 位于 Skill 安装目录内,真实邮件可能被误提交进 "
        f"Git/发布包。请把数据目录放到 Skill 目录之外(如 桌面/文档/项目文件夹),"
        f"或使用 launcher 生成的双击文件所在目录。")


def paths(args) -> tuple[Path, Path, Path]:
    base = Path(args.base).resolve()
    guard_base(base)
    return base, base / "内部数据", base / "客户档案"


def load_state(base: Path) -> dict:
    f = base / "内部数据" / "state.json"
    if not f.exists():
        die(f"state.json not found under {base}; run 'tcm.py init' first")
    try:
        state = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"state.json is corrupt: {e}")
    if state.get("version") != STATE_VERSION:
        die(f"unsupported state version {state.get('version')!r}; this is a v2+ data dir")
    return state


def save_state(base: Path, state: dict, bump: bool = True) -> None:
    if bump:
        state["updated_at"] = now_iso()
    tmp = base / "内部数据" / "state.json.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(base / "内部数据" / "state.json")


def record_run(state: dict, cmd: str, **info) -> None:
    state.setdefault("runs", []).append({"at": now_iso(), "cmd": cmd, **info})


def rel_path(base: Path, p: Path) -> str:
    """Provenance path that never leaks machine-specific absolutes: relative
    to the data base when possible, otherwise just the file name."""
    try:
        return p.resolve().relative_to(base).as_posix()
    except ValueError:
        return p.name


# ------------------------------------------------------------------- parsing --

class _TextFromHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _TextFromHTML()
    p.feed(html)
    return re.sub(r"\n{3,}", "\n\n", "".join(p.parts)).strip()


QUOTE_HEADER = re.compile(
    r"^(-{2,}\s*)?(原始邮件|原邮件|Original Message|Forwarded message|转发邮件|Forwarded by)", re.I)
QUOTE_FROM_LINE = re.compile(r"^-?\s*(From|发件人)\s*:", re.I)
QUOTE_META_LINE = re.compile(r"^\s*(Date|Sent|Subject|To|主题|日期|发送时间|收件人)\s*:", re.I)
SIG_GREETING = re.compile(
    r"^(best regards|kind regards|warm regards|regards|sincerely|thanks and regards|"
    r"many thanks|best wishes|此致敬礼|顺祝商祺|祝好|祝商祺)\s*[!,。]?\s*$", re.I)
STD_SIG_DELIM = re.compile(r"^-- ?$")
DISCLAIMER_START = re.compile(
    r"^(this e-?mail|本邮件|本邮件及其附件|confidentiality notice)", re.I)
MARKER_SIG = "*[签名区已折叠]*"
MARKER_QUOTE = "> …(历史引用已折叠,完整内容见本记录中对应邮件)"


def clean_body(raw: str) -> str:
    """Display cleaning for one mail body.

    Deterministic rules: collapse runs of >=2 consecutive quoted lines (the
    historical copy already exists as its own timeline entry), PRESERVE
    interleaved quoted lines (customers type point-by-point answers between
    them), cut signature blocks and disclaimer boilerplate. The raw body is
    always preserved internally; only the display is cleaned. If cleaning
    would leave nothing readable, fall back to the raw text.
    """
    if not raw or not raw.strip():
        return raw or ""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        if STD_SIG_DELIM.match(s) or SIG_GREETING.match(s):
            out.append(MARKER_SIG)
            break
        if DISCLAIMER_START.match(s):
            out.append("*[免责声明已折叠]*")
            break
        if QUOTE_HEADER.match(s):
            out.append(MARKER_QUOTE)
            break
        if QUOTE_FROM_LINE.match(s) and any(
                QUOTE_META_LINE.match(lines[j].strip()) for j in range(i + 1, min(i + 4, n))):
            out.append(MARKER_QUOTE)
            break
        if s.startswith(">"):
            j = i
            while j < n and lines[j].strip().startswith(">"):
                j += 1
            if j - i >= 2:          # pure historical quote run
                out.append(MARKER_QUOTE)
                i = j
                continue
            out.append(lines[i].rstrip())  # single quoted line: keep (reply context)
            i += 1
            continue
        out.append(lines[i].rstrip())
        i += 1
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    content_lines = [l for l in result.split("\n")
                     if not re.fullmatch(r"\s*(\*\[[^\]]+\]\*|> ?…\([^\)]*\))\s*", l)]
    if not any(re.search(r"[A-Za-z0-9\u4e00-\u9fff]", l) for l in content_lines):
        return text.strip()
    return result


def pick_body(msg) -> tuple[str, str, dict[str, str]]:
    """Extract display body + cid image map.

    An empty/whitespace text/plain part must NOT shadow a full HTML body
    (58 of 120 real sent mails had exactly this); fall back to HTML then.
    cid-referenced <img> tags become [图片:filename] tokens so the renderer
    can link them to extracted attachments."""
    cid_map: dict[str, str] = {}
    plain: str | None = None
    html: str | None = None
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            ctype = part.get_content_type()
            cid = (part.get("Content-ID") or "").strip().strip("<>")
            if ctype.startswith("image/") and cid:
                continue  # inline image part: handled via attachments, not text
            try:
                payload = part.get_content()
            except Exception:
                continue
            if not isinstance(payload, str):
                continue
            if ctype == "text/plain" and plain is None and payload.strip():
                plain = payload
            elif ctype == "text/html" and html is None:
                html = payload
    else:
        try:
            payload = msg.get_content()
        except Exception:
            return "", "unknown", cid_map
        if isinstance(payload, str) and msg.get_content_type() == "text/plain":
            plain = payload

    if plain is not None:
        return plain, "plain", cid_map
    if html:
        for part in msg.walk():
            cid = (part.get("Content-ID") or "").strip().strip("<>")
            if cid and part.get_filename():
                cid_map[cid] = part.get_filename()
        html_for_text = re.sub(
            r'<img[^>]*src=["\']cid:([^"\']+)["\'][^>]*>',
            lambda m: f"[图片:{cid_map.get(m.group(1), m.group(1))}]", html, flags=re.I)
        return html_to_text(html_for_text), "html", cid_map
    return "", "none", cid_map


def norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "-", (name or "").strip()).strip("-.") or "unnamed"


def safe_folder(name: str) -> str:
    """Folder display name: keep spaces (readable for salespeople), strip only
    filesystem-illegal characters."""
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", (name or "").strip()).strip() or "未命名客户"


def parse_mail_file(path: Path) -> dict | None:
    raw = path.read_bytes()
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    from_addrs = getaddresses(msg.get_all("From", []))
    to_addrs = getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []))

    def addr(a):
        return {"name": norm(a[0]), "email": a[1].strip().lower()} if a and (a[0] or a[1]) else None

    from_clean = [a for a in map(addr, from_addrs) if a]
    to_clean = [a for a in map(addr, to_addrs) if a]
    date_raw = msg.get("Date")
    try:
        dt = parsedate_to_datetime(date_raw) if date_raw else None
        date_iso = dt.date().isoformat() if dt else None
        date_full = dt.isoformat() if dt else None
    except Exception:
        date_iso = date_full = None

    body, body_fmt, cid_map = pick_body(msg)
    subject = str(msg.get("Subject", "") or "").replace("\n", " ").strip()
    msgid = norm(str(msg.get("Message-ID", "") or "")).strip("<>")

    attachments = []
    for idx, part in enumerate(msg.walk()):
        if part.get_content_maintype() == "multipart":
            continue
        fname = part.get_filename()
        cid = (part.get("Content-ID") or "").strip().strip("<>")
        disposition = part.get_content_disposition()
        if not fname and not cid and disposition != "attachment":
            continue
        entry = {"filename": fname, "cid": cid or None,
                 "disposition": disposition or "attachment",
                 "content_type": part.get_content_type(), "error": None}
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                raise ValueError("no decodable payload")
            entry["size"] = len(payload)
            entry["sha256"] = hashlib.sha256(payload).hexdigest()
            entry["_payload"] = payload
        except Exception as e:
            entry["error"] = f"载荷未获取: {e}"
            entry["size"] = None
            entry["sha256"] = None
        attachments.append(entry)

    header_score = sum(bool(msg.get(h)) for h in ("From", "To", "Subject", "Date", "Message-ID"))
    if header_score < 2 or not (from_clean or to_clean):
        return None

    return {"from": from_clean[0] if from_clean else None, "to": to_clean,
            "date": date_iso, "date_full": date_full, "subject": subject,
            "message_id": msgid, "body": body, "body_format": body_fmt,
            "cid_map": cid_map, "attachments": attachments}


def compute_identity(parsed: dict) -> tuple[str, str | None]:
    """(canonical_hash, alt_hash): Message-ID-derived hash when present with
    the content-basis hash as alternate, so re-exports that gain/lose the
    Message-ID header still dedupe (both directions, see ingest)."""
    basis = json.dumps({
        "f": parsed["from"]["email"] if parsed["from"] else "",
        "t": sorted(a["email"] for a in parsed["to"]),
        "d": parsed["date_full"] or parsed["date"] or "",
        "s": parsed["subject"],
        "b": re.sub(r"\s+", " ", parsed["body"])[:4000],
    }, ensure_ascii=False, sort_keys=True)
    basis_hash = hashlib.sha256(basis.encode("utf-8")).hexdigest()
    if parsed["message_id"]:
        mid_hash = hashlib.sha256(("mid:" + parsed["message_id"].lower()).encode()).hexdigest()
        return mid_hash, basis_hash
    return basis_hash, None


# ----------------------------------------------------------------- commands --

def cmd_init(args) -> None:
    base, inner, archive = paths(args)
    for sub in ("inbox", "messages", "attachments"):
        (inner / sub).mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)
    sf = inner / "state.json"
    if not sf.exists():
        sf.write_text(json.dumps({
            "version": STATE_VERSION, "created_at": now_iso(), "owner": None,
            "messages": {}, "identity_index": {}, "customers": {},
            "reports": {}, "folders": {}, "coverage": {}, "runs": [],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out({"ok": True, "base": str(base),
         "note": "把 .eml 放入 内部数据/inbox/,或用 launcher 连接邮箱后运行 ingest"})


def cmd_ingest(args) -> None:
    base, inner, _ = paths(args)
    state = load_state(base)
    sources = [Path(p) for p in args.sources] or [inner / "inbox"]
    files: list[Path] = []
    for s in sources:
        s = s.resolve()
        if s.is_dir():
            files.extend(sorted(p for p in s.iterdir()
                                if p.is_file() and p.suffix.lower() in (".eml", ".txt", ".mime")))
        elif s.is_file():
            files.append(s)
        else:
            die(f"source not found: {s}")
    if not files:
        out({"ok": True, "new": 0, "skipped": 0, "unrecognized": 0, "messages": [],
             "note": f"no .eml/.txt files under {sources}"})
        return

    owner = (args.owner or state.get("owner") or "").strip().lower() or None
    if args.owner:
        if "@" not in owner:
            die(f"not an email address: {args.owner}")
        state["owner"] = owner  # persist; later ingests keep direction marking
    msgs_dir = inner / "messages"
    att_dir = inner / "attachments"
    msgs_dir.mkdir(exist_ok=True)
    att_dir.mkdir(exist_ok=True)
    report, unrecognized = [], []
    new = skipped = 0
    for f in files:
        try:
            parsed = parse_mail_file(f)
        except Exception as e:
            unrecognized.append({"file": f.name, "reason": f"parse error: {e}"})
            continue
        if parsed is None:
            unrecognized.append({"file": f.name,
                                 "reason": "未识别为邮件(缺少邮件头);请从邮箱客户端另存为 .eml"})
            continue
        mid, alt = compute_identity(parsed)
        idx = state.setdefault("identity_index", {})
        prev_key = ""
        if mid in state["messages"]:
            prev_key = mid
        elif alt and idx.get(alt):
            prev_key = idx[alt]
        elif idx.get(mid):
            prev_key = idx[mid]
        prev = state["messages"].get(prev_key) if prev_key else None
        if prev:
            skipped += 1
            report.append({"id": prev["id"], "file": f.name, "status": "skipped",
                           "reason": "already ingested", "subject": parsed["subject"]})
            continue
        # store attachment payloads
        for a_idx, att in enumerate(parsed["attachments"]):
            if att.get("_payload") is not None:
                fname = safe_name(att.get("filename") or f"attachment-{a_idx + 1}")
                dest_dir = att_dir / mid[:12]
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / f"{a_idx:02d}-{fname}"
                dest.write_bytes(att.pop("_payload"))
                att["stored_path"] = rel_path(base, dest)
            else:
                att["error"] = att.get("error") or "载荷未获取"
        from_email = parsed["from"]["email"] if parsed["from"] else ""
        direction = "sent" if (owner and from_email == owner) else "received"
        rec = {
            "id": mid[:12], "hash": mid, "file": rel_path(base, f), "ingested_at": now_iso(),
            "from": parsed["from"], "to": parsed["to"], "date": parsed["date"],
            "date_full": parsed["date_full"], "subject": parsed["subject"],
            "message_id": parsed["message_id"], "direction": direction,
            "body_format": parsed["body_format"],
            "attachments": parsed["attachments"],
            "free_domain": from_email.split("@")[-1] in FREE_DOMAINS if from_email else True,
            "processed": False,
        }
        (msgs_dir / f"msg-{mid[:12]}.json").write_text(
            json.dumps({"meta": rec, "body": parsed["body"],
                        "cid_map": parsed["cid_map"]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        state["messages"][mid] = rec
        idx[mid] = mid
        if alt:
            idx[alt] = mid
        new += 1
        report.append({"id": mid[:12], "file": f.name, "status": "new",
                       "from": from_email, "date": parsed["date"],
                       "subject": parsed["subject"], "direction": direction,
                       "free_domain": rec["free_domain"]})
    retro = apply_owner_directions(base, state, owner) if owner else 0
    record_run(state, "ingest", new=new, skipped=skipped, files=len(files),
               directions_revised=retro)
    # pick up coverage written by imap_fetch.py (fetch-status.json)
    fs = inner / "fetch-status.json"
    if fs.exists():
        try:
            fd = json.loads(fs.read_text(encoding="utf-8"))
            if fd.get("coverage"):
                state["coverage"] = {"folders": fd["coverage"],
                                     "since": fd.get("since"),
                                     "recorded_at": fd.get("updated_at")}
        except Exception:
            pass
    save_state(base, state)
    out({"ok": True, "new": new, "skipped": skipped, "unrecognized": len(unrecognized),
         "messages": report, "unrecognized_files": unrecognized})


def apply_owner_directions(base: Path, state: dict, email: str) -> int:
    changed = 0
    for rec in state["messages"].values():
        frm = (rec.get("from") or {}).get("email")
        new_dir = "sent" if frm == email else "received"
        if frm and rec["direction"] != new_dir:
            rec["direction"] = new_dir
            changed += 1
            mf = base / "内部数据" / "messages" / f"msg-{rec['id']}.json"
            if mf.exists():
                doc = json.loads(mf.read_text(encoding="utf-8"))
                doc["meta"]["direction"] = new_dir
                mf.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    return changed


def cmd_owner(args) -> None:
    base, _, _ = paths(args)
    state = load_state(base)
    email = args.email.strip().lower()
    if "@" not in email:
        die(f"not an email address: {email}")
    state["owner"] = email
    changed = apply_owner_directions(base, state, email)
    record_run(state, "owner", email=email, retro_directions=changed)
    save_state(base, state)
    out({"ok": True, "owner": email, "directions_revised": changed})


def valid_slug(s: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", s))


def cmd_map_customer(args) -> None:
    base, _, _ = paths(args)
    state = load_state(base)
    slug = args.slug.strip().lower()
    if not valid_slug(slug):
        die(f"slug must match [a-z0-9][a-z0-9-]{{1,40}}: {slug}")
    emails = [e.strip().lower() for e in args.emails.split(",") if e.strip()]
    if not emails:
        die("need at least one email address")
    for e in emails:
        if "@" not in e:
            die(f"not an email address: {e}")
    for e in emails:
        old = state["customers"].get(e, {})
        if old.get("slug") and old["slug"] != slug:
            die(f"{e} already belongs to another client: {old['slug']}")
        state["customers"][e] = {
            "slug": slug,
            "name": args.name or old.get("name"),
            "company": args.company or old.get("company"),
            "country": args.country or old.get("country"),
            "confirmed": not args.unconfirmed,
        }
    record_run(state, "map-customer", slug=slug, emails=emails)
    save_state(base, state)
    out({"ok": True, "slug": slug, "emails": emails,
         "unconfirmed": bool(args.unconfirmed)})


def customer_meta(state: dict, slug: str) -> dict:
    meta = {"name": None, "company": None, "country": None, "confirmed": True}
    for c in state["customers"].values():
        if c.get("slug") == slug:
            for k in meta:
                if c.get(k) is not None and meta[k] in (None, True) and k != "confirmed":
                    meta[k] = c.get(k)
            meta["confirmed"] = meta["confirmed"] and c.get("confirmed", True)
    return meta


def cmd_report(args) -> None:
    base, _, _ = paths(args)
    state = load_state(base)
    slug = args.slug.strip().lower()
    if slug not in {c.get("slug") for c in state["customers"].values()}:
        die(f"unknown customer slug: {slug} (run map-customer first)")
    if args.priority not in PRIORITY_ORDER:
        die(f"priority must be one of {PRIORITY_ORDER}")
    state.setdefault("reports", {})[slug] = {
        "stage": args.stage or "未标注",
        "situation": args.situation or "",
        "next": args.next or "",
        "priority": args.priority,
        "updated": now_iso(),
    }
    record_run(state, "report", slug=slug, priority=args.priority)
    save_state(base, state)
    out({"ok": True, "slug": slug, "priority": args.priority})


def cmd_done(args) -> None:
    base, _, _ = paths(args)
    state = load_state(base)
    known = {rec["id"]: rec for rec in state["messages"].values()}
    missing = [i for i in args.ids if i not in known]
    if missing:
        die(f"unknown message id(s): {missing}")
    marked = 0
    for i in args.ids:
        rec = known[i]
        if not rec["processed"]:
            rec["processed"] = True
            marked += 1
            mf = base / "内部数据" / "messages" / f"msg-{i}.json"
            if mf.exists():
                doc = json.loads(mf.read_text(encoding="utf-8"))
                doc["meta"]["processed"] = True
                mf.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    record_run(state, "done", count=marked)
    save_state(base, state)
    out({"ok": True, "marked": marked, "ids": args.ids})


# ------------------------------------------------------------------ rendering --

def merged_coverage(base: Path, state: dict) -> dict:
    """Coverage from state, refreshed by the live fetch-status.json written by
    imap_fetch.py (the file is always the freshest source)."""
    cov = dict(state.get("coverage") or {})
    fs = base / "内部数据" / "fetch-status.json"
    if fs.exists():
        try:
            fd = json.loads(fs.read_text(encoding="utf-8"))
            if fd.get("coverage"):
                cov = {"folders": fd["coverage"], "since": fd.get("since"),
                       "recorded_at": fd.get("updated_at")}
        except Exception:
            pass
    return cov


def render_coverage(state: dict) -> str:
    cov = state.get("coverage") or {}
    folders = cov.get("folders") or {}
    if not folders:
        return ("覆盖范围:仅下列已导入的邮件(内部数据/inbox 与 launcher 拉取结果),"
                "未统计整个邮箱;不能视为客户的完整历史。")
    parts, total_all, fetched_all = [], 0, 0
    for fname, info in folders.items():
        total = info.get("total")
        fetched = info.get("fetched", 0)
        total_all += total or 0
        fetched_all += fetched
        if total is not None and total > fetched:
            parts.append(f"{fname} 已读 {fetched}/{total}")
        else:
            parts.append(f"{fname} 已读 {fetched}")
    since = f"({cov.get('since')} 起)" if cov.get("since") else ""
    omitted = total_all - fetched_all
    tail = f"——邮箱中另有约 {omitted} 封更早/未读邮件未纳入本记录" if omitted > 0 else ""
    return f"覆盖范围:{', '.join(parts)} {since}{tail}。"


def render_overview_md(state: dict) -> str:
    reports = state.get("reports", {})
    all_slugs = sorted({c.get("slug") for c in state["customers"].values()} - {None})
    lines = ["# 客户概览", "",
             f"更新:{state.get('updated_at') or '-'} · 客户数:{len(all_slugs)}", "",
             render_coverage(state), ""]
    used = set()
    for key in PRIORITY_ORDER:
        items = sorted(
            ((slug, r) for slug, r in reports.items() if r.get("priority") == key),
            key=lambda kv: kv[1].get("updated") or "", reverse=True)
        if not items:
            continue
        lines.append(f"## {PRIORITY_TITLES[key]}\n")
        for slug, r in items:
            meta = customer_meta(state, slug)
            title = meta["company"] or meta["name"] or slug
            lines.append(f"**{title} · {meta['country'] or '国家未知'}|{r.get('stage', '')}**")
            lines.append(f"当前情况:{r.get('situation', '')}")
            lines.append(f"下一步:{r.get('next', '')}")
            lines.append("")
            used.add(slug)
    others = [s for s in all_slugs if s not in used]
    if others:
        lines.append("## 尚未有状态摘要\n")
        for slug in others:
            meta = customer_meta(state, slug)
            lines.append(f"- {meta['company'] or meta['name'] or slug}"
                         f"({meta['country'] or '国家未知'})——已完成邮件归档,等待 Agent 填写状态")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_brief_text(state: dict) -> str:
    reports = state.get("reports", {})
    lines = []
    for key in PRIORITY_ORDER:
        items = sorted(
            ((slug, r) for slug, r in reports.items() if r.get("priority") == key),
            key=lambda kv: kv[1].get("updated") or "", reverse=True)
        for slug, r in items:
            meta = customer_meta(state, slug)
            title = meta["company"] or meta["name"] or slug
            lines.append(f"**{title} · {meta['country'] or '国家未知'}|{r.get('stage', '')}**")
            lines.append(f"当前情况:{r.get('situation', '')}")
            lines.append(f"下一步:{r.get('next', '')}")
            lines.append("")
    return "\n".join(lines).strip()


def cmd_render(args) -> None:
    base, inner, archive = paths(args)
    state = load_state(base)
    state["coverage"] = merged_coverage(base, state)
    email2slug = {e: c["slug"] for e, c in state["customers"].items()}
    by_id = {rec["id"]: rec for rec in state["messages"].values()}
    slug_mail_ids: dict[str, set] = defaultdict(set)
    for rec in by_id.values():
        slug = email2slug.get((rec.get("from") or {}).get("email"))
        if slug:
            slug_mail_ids[slug].add(rec["id"])
            continue
        for t in rec.get("to", []):
            slug = email2slug.get(t.get("email"))
            if slug:
                slug_mail_ids[slug].add(rec["id"])

    renamed, rendered, skipped_manual = [], [], []
    for slug, mail_ids in sorted(slug_mail_ids.items()):
        meta = customer_meta(state, slug)
        display = meta["company"] or meta["name"] or slug
        folder_name = safe_folder(f"{display} - {meta['country'] or '未知国家'}")
        prev = state.get("folders", {}).get(slug)
        cdir = archive / folder_name
        if prev and prev != folder_name:
            old = archive / prev
            if old.is_dir() and not cdir.exists():
                shutil.move(str(old), str(cdir))
                renamed.append({"slug": slug, "from": prev, "to": folder_name})
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "附件").mkdir(exist_ok=True)
        state.setdefault("folders", {})[slug] = folder_name

        # attachment dedupe + signature-logo detection across this customer
        hash_mails: dict[str, set] = defaultdict(set)
        inline_hashes: set[str] = set()
        atts_by_mail: dict[str, list] = {}
        for mid in mail_ids:
            mf = inner / "messages" / f"msg-{mid}.json"
            doc = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {"meta": by_id[mid], "body": ""}
            atts_by_mail[mid] = doc["meta"].get("attachments", [])
            for a in atts_by_mail[mid]:
                if a.get("sha256"):
                    hash_mails[a["sha256"]].add(mid)
                    if a.get("cid") or a.get("disposition") == "inline":
                        inline_hashes.add(a["sha256"])
        logo_hashes = {h for h, ids in hash_mails.items()
                       if len(ids) >= 3 and h in inline_hashes}

        seen_hash: dict[str, str] = {}
        used_names: set[str] = set()

        def dest_for(a: dict) -> str:
            h = a["sha256"]
            if h in seen_hash:
                return seen_hash[h]
            name = safe_name(a.get("filename") or "attachment")
            stem, dot, ext = name.rpartition(".")
            if not dot:
                stem, ext = name, ""
            cand, k = name, 2
            while cand in used_names:
                cand = f"{stem}-{k}{dot}{ext}" if dot else f"{name}-{k}"
                k += 1
            src = base / a["stored_path"]
            if src.exists():
                (cdir / "附件" / cand).write_bytes(src.read_bytes())
            seen_hash[h] = cand
            used_names.add(cand)
            return cand

        def body_of(mid: str) -> str:
            mf = inner / "messages" / f"msg-{mid}.json"
            doc = json.loads(mf.read_text(encoding="utf-8"))
            cleaned = clean_body(doc.get("body", ""))
            if not cleaned.strip():
                cleaned = (doc.get("body") or "").strip() or "(正文为空)"
            # resolve [图片:filename] tokens to extracted attachments of this mail
            def img_link(m: re.Match) -> str:
                fname = safe_name(m.group(1))
                for a in atts_by_mail.get(mid, []):
                    if a.get("sha256") and safe_name(a.get("filename") or "") == fname:
                        return f"![{fname}](附件/{dest_for(a)})"
                return m.group(0)
            return re.sub(r"\[图片:([^\]]+)\]", img_link, cleaned)

        mails = sorted(mail_ids, key=lambda m: (by_id[m].get("date_full") or by_id[m].get("date") or "", m))
        entries = []
        for mid in mails:
            rec = by_id[mid]
            frm = rec.get("from") or {}
            if rec.get("direction") == "sent":
                arrow = f"→ {', '.join((a.get('name') or a.get('email') or '?') for a in rec.get('to', [])) or '?'}"
                dirlabel = "已发送"
            else:
                arrow = "→ 我方"
                dirlabel = "收件"
            head = (f"### {rec.get('date') or '日期未知'} · "
                    f"{frm.get('name') or frm.get('email') or '?'} {arrow} · "
                    f"{rec.get('subject') or '(无主题)'}")
            lines = [head, "", f"`msg-{mid}` · {dirlabel}", "", body_of(mid), ""]
            atts = atts_by_mail.get(mid, [])
            if atts:
                lines.append("**附件**")
                lines.append("")
                for a in atts:
                    if a.get("error") or not a.get("stored_path"):
                        lines.append(f"- 附件未取得:{a.get('filename') or '(未命名附件)'}"
                                     f"(源邮件中声明,载荷未能提取)")
                        continue
                    dest = dest_for(a)
                    rel = f"附件/{dest}"
                    if a["sha256"] in logo_hashes:
                        lines.append(f"- 签名图片(疑似,已存档不预览):[{a.get('filename')}]({rel})")
                    elif a.get("content_type", "").startswith("image/"):
                        lines.append(f"![{a.get('filename')}]({rel})")
                    else:
                        lines.append(f"- [{a.get('filename')}]({rel})")
                lines.append("")
            else:
                lines.append("附件:无")
                lines.append("")
            entries.append("\n".join(lines))

        comm_path = cdir / "客户沟通记录.md"
        agent_default = "\n".join([s + "\n\n(待整理)" for s in AGENT_SECTIONS]) + "\n"
        if comm_path.exists():
            existing = comm_path.read_text(encoding="utf-8")
            m = re.search(r"(<!-- TCM:AGENT:BEGIN -->\n)(.*?)(\n<!-- TCM:AGENT:END -->)",
                          existing, re.S)
            if m:
                agent = m.group(2)
            elif existing.strip():
                skipped_manual.append(folder_name)
                continue  # never clobber a manual file
            else:
                agent = agent_default
        else:
            agent = agent_default
        doc_out = (f"# {folder_name}\n\n{render_coverage(state)}\n\n"
                   f"时间线由助手按邮件自动维护;下方 Agent 区域内的文字可自由编辑,更新时不会被覆盖。\n\n"
                   f"<!-- TCM:AGENT:BEGIN -->\n{agent}\n<!-- TCM:AGENT:END -->\n\n"
                   f"## 沟通时间线(按时间排序)\n\n" + "\n\n---\n\n".join(entries) + "\n")
        comm_path.write_text(doc_out, encoding="utf-8")
        rendered.append(folder_name)

    (archive / "客户概览.md").write_text(render_overview_md(state), encoding="utf-8")
    save_state(base, state, bump=False)
    out({"ok": True, "rendered": rendered, "renamed": renamed,
         "skipped_manual": skipped_manual, "overview": "客户概览.md"})


def cmd_brief(args) -> None:
    base, _, _ = paths(args)
    state = load_state(base)
    out({"ok": True, "text": render_brief_text(state),
         "overview_file": "客户档案/客户概览.md"})


# ----------------------------------------------------------------- inspection --

def cmd_status(args) -> None:
    base, inner, archive = paths(args)
    state = load_state(base)
    unprocessed = [
        {"id": r["id"], "from": (r.get("from") or {}).get("email"),
         "date": r["date"], "subject": r["subject"], "direction": r["direction"],
         "free_domain": r["free_domain"]}
        for r in sorted(state["messages"].values(), key=lambda x: x.get("date") or "")
        if not r["processed"]
    ]
    customers: dict[str, dict] = {}
    for e, c in state["customers"].items():
        customers.setdefault(c["slug"], {"emails": [], "name": c.get("name"),
                                         "company": c.get("company"),
                                         "country": c.get("country"),
                                         "confirmed": c.get("confirmed", True),
                                         "folder": state.get("folders", {}).get(c["slug"])})
        customers[c["slug"]]["emails"].append(e)
    out({"ok": True, "base": str(base), "owner": state.get("owner"),
         "total_messages": len(state["messages"]), "unprocessed": unprocessed,
         "customers": customers, "reports": state.get("reports", {}),
         "coverage": merged_coverage(base, state)})


def cmd_verify(args) -> None:
    base, inner, archive = paths(args)
    state = load_state(base)
    problems: list[str] = []
    warnings: list[str] = []

    by_id: dict[str, dict] = {}
    for state_key, rec in state["messages"].items():
        if rec["hash"] != state_key:
            problems.append(f"state key does not match record id {rec['id']}")
        if rec["id"] in by_id:
            problems.append(f"short id collision between two messages: {rec['id']}")
        by_id[rec["id"]] = rec
        for field in REQUIRED_MSG_FIELDS:
            if field not in rec:
                problems.append(f"message {rec.get('id', '?')} missing field {field}")

    msgs_dir = inner / "messages"
    for rec in by_id.values():
        mf = msgs_dir / f"msg-{rec['id']}.json"
        if not mf.exists():
            problems.append(f"missing message file: {mf.name}")
            continue
        doc = json.loads(mf.read_text(encoding="utf-8"))
        if doc.get("meta", {}).get("id") != rec["id"]:
            problems.append(f"{mf.name}: meta id mismatch")
        if not str(doc.get("body", "")).strip() and doc.get("body") != "":
            problems.append(f"{mf.name}: empty body")
    for mf in msgs_dir.glob("msg-*.json"):
        if mf.stem.removeprefix("msg-") not in by_id:
            problems.append(f"orphan message file not in state: {mf.name}")

    emails_by_slug: dict[str, list[str]] = {}
    for e, c in state["customers"].items():
        emails_by_slug.setdefault(c["slug"], []).append(e)
    for slug in emails_by_slug:
        if not valid_slug(slug):
            problems.append(f"invalid slug: {slug}")

    # customer folders on disk must all be mapped
    for d in archive.iterdir() if archive.exists() else []:
        if d.is_dir() and d.name not in set(state.get("folders", {}).values()):
            problems.append(f"customer folder '{d.name}' has no mapping in state (run map-customer)")

    # evidence refs
    ref_re = re.compile(r"msg-([0-9a-f]{12})")
    for md in archive.rglob("*.md"):
        for ref in set(ref_re.findall(md.read_text(encoding="utf-8", errors="replace"))):
            if ref not in by_id:
                problems.append(f"{md.relative_to(archive)} cites msg-{ref} which does not exist")

    # false-success guards
    slug_of_email = {e: c["slug"] for e, c in state["customers"].items()}
    slugs_with_mail = {slug for slug in {
        slug_of_email.get((r.get("from") or {}).get("email"))
        for r in by_id.values()
        if r["processed"] and r["direction"] == "received"
    } if slug}
    for rec in by_id.values():
        if not rec["processed"] or rec["direction"] != "received":
            continue
        frm = (rec.get("from") or {}).get("email")
        slug = slug_of_email.get(frm)
        if not slug:
            problems.append(
                f"processed mail msg-{rec['id']} from unmapped sender {frm}: "
                f"map-customer it, or leave it unprocessed (done() is only for merged mail)")
            continue
        folder = state.get("folders", {}).get(slug)
        comm = archive / folder / "客户沟通记录.md" if folder else None
        if not folder or not comm or not comm.exists():
            problems.append(f"customer {slug} has processed mail msg-{rec['id']} "
                            f"but 客户沟通记录.md was never rendered (run render)")
            continue
        text = comm.read_text(encoding="utf-8")
        if f"msg-{rec['id']}" not in text:
            problems.append(f"processed mail msg-{rec['id']} from customer {slug} "
                            f"is not cited in its 沟通记录")
        missing_sections = [s for s in AGENT_SECTIONS if s not in text]
        if missing_sections:
            problems.append(f"{slug} 沟通记录 missing required section(s): "
                            f"{', '.join(missing_sections)}")
        link_targets = re.findall(r"\]\((附件/[^)]+)\)", text)
        for t in link_targets:
            if not (archive / folder / t).exists():
                problems.append(f"{slug}: attachment link {t} points to a missing file")

    # sent mail must be archived too: a vanished quote/reply would make the
    # record lie about who already acted ("已发=已行动" depends on it)
    for rec in by_id.values():
        if not rec["processed"] or rec["direction"] != "sent":
            continue
        tos = [a.get("email") for a in rec.get("to", []) if a.get("email")]
        slugs = sorted({slug_of_email[t] for t in tos if t in slug_of_email})
        if not slugs:
            problems.append(
                f"processed sent mail msg-{rec['id']} addressed to unmapped recipients "
                f"({', '.join(tos) or '?'}): archive it to the right customer or leave "
                f"it unprocessed")
            continue
        for slug in slugs:
            folder = state.get("folders", {}).get(slug)
            comm = archive / folder / "客户沟通记录.md" if folder else None
            if not folder or not comm or not comm.exists():
                problems.append(f"customer {slug} has processed sent mail msg-{rec['id']} "
                                f"but 客户沟通记录.md was never rendered (run render)")
            elif f"msg-{rec['id']}" not in comm.read_text(encoding="utf-8"):
                problems.append(f"processed sent mail msg-{rec['id']} to customer {slug} "
                                f"is not cited in its 沟通记录")
    for slug in slugs_with_mail:
        if slug not in state.get("reports", {}):
            problems.append(f"customer {slug} has archived mail but no status report "
                            f"(run: report {slug} ...) — the overview cannot be built")
    reports = state.get("reports", {})
    if reports:
        ov = archive / "客户概览.md"
        if not ov.exists():
            problems.append("客户档案/客户概览.md missing while status reports exist (run render)")
        elif ov.read_text(encoding="utf-8") != render_overview_md(state):
            problems.append("客户概览.md does not match current status reports "
                            "(hand-edited?); re-run render to regenerate")
    processed_received = [r for r in by_id.values()
                          if r["processed"] and r["direction"] == "received"]
    if processed_received and not slugs_with_mail:
        problems.append("processed received mail(s) exist but no client was mapped/rendered "
                        "(false finish)")

    unprocessed_count = sum(1 for r in by_id.values() if not r["processed"])
    if unprocessed_count:
        warnings.append(f"{unprocessed_count} message(s) not yet merged; "
                        f"finish them or stop deliberately before reporting completion")
    for slug, c in emails_by_slug.items():
        if c and all(state["customers"][e].get("confirmed", True) is False for e in c):
            continue
        if slug not in reports and slug in slugs_with_mail:
            pass  # already a hard problem above
    dump = json.dumps(state, ensure_ascii=False)
    if "D:\\" in dump or "C:\\Users\\" in dump or "/Users/" in dump:
        problems.append("state.json leaks absolute machine paths")

    out({"ok": not problems, "problems": problems, "warnings": warnings,
         "counts": {"messages": len(by_id), "customers": len(emails_by_slug),
                    "reports": len(reports)},
         "unconfirmed_identities": sorted({e for e, c in state["customers"].items()
                                           if not c.get("confirmed", True)})})
    if problems:
        sys.exit(1)


def cmd_doctor(args) -> None:
    base, inner, _ = paths(args)
    checks: dict = {"python": sys.version.split()[0], "base": str(base)}
    try:
        probe = inner / ".tcm-write-probe"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
        checks["data_writable"] = True
    except Exception:
        checks["data_writable"] = False
    # generic discovery only: a read-only mail CLI on PATH, or an explicit
    # TCM_MAIL_READ env override. No author-specific paths are baked in.
    checks["mail_read_cli_path"] = shutil.which("mail-read")
    override = os.environ.get("TCM_MAIL_READ")
    checks["mail_read_cli_override"] = (
        override if override and Path(override).exists() else None)
    checks["python_keyring"] = False
    try:
        import keyring  # noqa: F401
        checks["python_keyring"] = True
    except Exception:
        pass
    connector = bool(checks["mail_read_cli_path"] or checks["mail_read_cli_override"])
    out({"ok": True, "checks": checks, "connector_available": connector,
         "connector_note": (
             "发现现成的只读邮件 CLI,SKILL.md Phase 1 将优先复用。"
         ) if connector else "未发现本机邮件连接器;将准备通用 IMAP 接入(launcher + imap_fetch),"
         "或使用 .eml 导入。两者都不影响后续流程。",
         "paths": {"eml_import": "always available",
                   "imap_fetch": str(Path(__file__).parent / "imap_fetch.py")}})


LAUNCHER_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{3,}$")
LAUNCHER_USER_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
LAUNCHER_SINCE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def cmd_launcher(args) -> None:
    # every parameter is embedded into shell scripts: validate the shape
    # first so no quoting trick can turn into command injection
    if not LAUNCHER_HOST_RE.match(args.host or ""):
        die(f"host 格式不合法(只允许字母/数字/点/连字符):{args.host!r}")
    if not LAUNCHER_USER_RE.match(args.user or ""):
        die(f"邮箱地址格式不合法:{args.user!r}")
    if args.since and not LAUNCHER_SINCE_RE.match(args.since):
        die(f"--since 格式应为 YYYY-MM-DD:{args.since!r}")
    if args.limit is not None and args.limit < 1:
        die("--limit 最小为 1")
    base, inner, _ = paths(args)
    for s in (str(base), str(inner)):
        if '"' in s or "%" in s:
            die(f"数据目录路径含 .bat 不安全字符(引号/百分号),请换个目录:{s}")
    inner.mkdir(parents=True, exist_ok=True)
    skill_scripts = Path(__file__).resolve().parent
    fetch_py = skill_scripts / "imap_fetch.py"
    if not fetch_py.exists():
        die("imap_fetch.py not found next to tcm.py")
    fetch_argv = ["--host", args.host, "--user", args.user]
    if args.since:
        fetch_argv += ["--since", args.since]
    if args.limit:
        fetch_argv += ["--limit", str(args.limit)]
    fetch_argv += ["--out", str(inner / "inbox"), "--status", str(inner / "fetch-status.json")]
    sh_args = " ".join(shlex.quote(a) for a in fetch_argv)
    bat_args = " ".join(f'"{a}"' for a in fetch_argv)  # values validated: no quotes/percent

    banner = ('echo "=== 外贸客户记忆助手 · 连接邮箱(只读)===" '
              '&& echo "" '
              f'&& echo "服务器: {args.host}   账号: {args.user}" '
              '&& echo "提示:输入授权码时屏幕不会显示任何字符,输入完成后按 Enter 回车。" '
              '&& echo ""')
    sh = (
        "#!/bin/bash\n"
        'cd "$(dirname "$0")"\n'
        + banner + "\n"
        + f"python3 {shlex.quote(str(fetch_py))} {sh_args}\n"
        "st=$?\n"
        'echo ""\n'
        'if [ $st -eq 0 ]; then\n'
        '  echo "✔ 邮件读取完成。回到 Agent 窗口,告诉它:邮件读取完成"\n'
        'else\n'
        '  echo "✘ 读取未完成。把本窗口的错误信息发给 Agent,它会给出恢复办法(密码不会留存)。"\n'
        'fi\n'
        'read -n 1 -s -r -p "按任意键关闭窗口..."\n'
    )
    bat_banner = banner.replace(' && echo "', '\necho "').replace('" &&', '"')
    bat = (
        "@echo off\r\n"
        "cd /d %~dp0\r\n"
        "chcp 65001 >nul\r\n"
        + bat_banner + "\r\n"
        f'where py >nul 2>nul\r\n'
        f'if %errorlevel%==0 (py -3 "{fetch_py}" {bat_args}) else (python "{fetch_py}" {bat_args})\r\n'
        'echo ""\r\n'
        'echo 完成后回到 Agent 窗口,告诉它:邮件读取完成\r\n'
        "pause\r\n"
    )
    cmd_path = base / "连接邮箱.command"
    bat_path = base / "连接邮箱.bat"
    cmd_path.write_text(sh, encoding="utf-8")
    bat_path.write_text(bat, encoding="utf-8")
    try:
        os.chmod(cmd_path, 0o755)
    except OSError:
        pass
    out({"ok": True, "command_file": str(cmd_path), "bat_file": str(bat_path),
         "usage": "用户双击即可;输入授权码(不回显);完成后回 Agent 说「邮件读取完成」"})


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Trade Client Memory deterministic core")
    ap.add_argument("--base", default=".", help="user data base dir (default: current dir); "
                                               "creates 内部数据/ and 客户档案/ under it")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)

    p = sub.add_parser("ingest")
    p.add_argument("sources", nargs="*", help=".eml/.txt files or directories (default 内部数据/inbox)")
    p.add_argument("--owner", help="your email address; marks sent mail and is persisted")
    p.set_defaults(fn=cmd_ingest)

    p = sub.add_parser("owner")
    p.add_argument("email")
    p.set_defaults(fn=cmd_owner)

    p = sub.add_parser("map-customer")
    p.add_argument("slug")
    p.add_argument("emails", help="comma-separated email addresses for this client")
    p.add_argument("--name"); p.add_argument("--company"); p.add_argument("--country")
    p.add_argument("--unconfirmed", action="store_true",
                   help="identity uncertain; flagged 待确认")
    p.set_defaults(fn=cmd_map_customer)

    p = sub.add_parser("report")
    p.add_argument("slug")
    p.add_argument("--stage", required=True)
    p.add_argument("--situation", required=True)
    p.add_argument("--next", required=True)
    p.add_argument("--priority", required=True, help="urgent|action|waiting|unconfirmed")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("launcher")
    p.add_argument("--host", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--since"); p.add_argument("--limit", type=int)
    p.set_defaults(fn=cmd_launcher)

    p = sub.add_parser("done")
    p.add_argument("ids", nargs="+")
    p.set_defaults(fn=cmd_done)

    sub.add_parser("render").set_defaults(fn=cmd_render)
    sub.add_parser("brief").set_defaults(fn=cmd_brief)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("verify").set_defaults(fn=cmd_verify)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
