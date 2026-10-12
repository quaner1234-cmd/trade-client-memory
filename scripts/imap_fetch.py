#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""imap_fetch.py — read-only IMAP fetcher for the Trade Client Memory skill.

Designed to be run BY THE USER (double-click launcher or their own terminal),
never with a password passed through chat. Read-only guarantees:

  * mailbox is opened with EXAMINE (read-only session)
  * only BODY.PEEK is fetched — flags (seen/replied) are never changed
  * no delete/move/store calls exist in this script
  * the password is read once via getpass and never written anywhere

Untrusted SEARCH (v4.2): the WorkBuddy/yunyou experiment showed server-side
SEARCH silently dropping results. Two defenses:

  * `resolve_folders` parses raw LIST bytes, strips quoting, decodes
    modified-UTF-7 for display, and SELECT always uses the raw server name —
    so the Chinese sent folder (&XfJT0ZAB- = 已发送) works
  * strategies: `search` (trust the server), `enum` (walk sequence numbers,
    UID+INTERNALDATE, date-filtered), `auto` = search, except it falls back
    to enum when SEARCH returns 0 on a non-empty folder, and it prefers enum
    for hosts known unreliable (yunyou.top) — the enum scan is capped by
    --enum-window and any truncation is recorded as incomplete coverage

Honest completion: fetch-status.json carries per-folder
{total, scanned, fetched, truncated} plus errors[]; any skip, failure,
missing sent folder or truncation => complete:false and a
「读取完成(部分,有遗漏)」 stage — a partial fetch is never full success.

The core is `run_fetch(M, ...)`: takes a connected+logged-in imaplib client,
so tests can drive it against a fake server (tests/fake_imap.py).

Usage:
  python imap_fetch.py --host imap.exmail.qq.com --user you@company.com \
      [--folders auto] [--since 2026-09-01] [--limit 120] [--strategy auto] \
      [--enum-window 1000] [--out 内部数据/inbox] [--status 内部数据/fetch-status.json]
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import imaplib
import json
import re
import socket
import ssl
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
SENT_PATTERNS = ("sent", "已发送", "已發送", "sent items", "sent messages")
# 刺猬云邮实测(2026-09-30):HEADER/TEXT SEARCH 会静默漏结果 → enum 优先
UNRELIABLE_HOST_SUFFIXES = ("yunyou.top",)
INTERNALDATE_RE = re.compile(rb'INTERNALDATE "([^"]+)"')
UID_RE = re.compile(rb"UID (\d+)")


def validate_limit(value) -> int:
    n = int(value)
    if n < 1:
        raise ValueError(f"--limit must be >= 1 (got {n})")
    return n


def make_mail_name(user: str, folder: str, uid: str) -> str:
    account_tag = hashlib.sha256(user.strip().lower().encode()).hexdigest()[:8]
    folder_tag = hashlib.sha256(folder.encode()).hexdigest()[:6]
    return (f"imap-{SAFE_NAME.sub('-', user)[:24]}-{account_tag}"
            f"-{SAFE_NAME.sub('-', folder)[:20]}-{folder_tag}-{uid}.eml")


def write_mail(msg_bytes: bytes, out_dir: Path, user: str, folder: str, uid: str) -> Path:
    path = out_dir / make_mail_name(user, folder, uid)
    path.write_bytes(msg_bytes)
    return path


def plan_folder_uids(uids_all: list[bytes], limit: int) -> tuple[list[bytes], int]:
    """(planned_uids, server_total): progress/ETA are measured against the
    plan so --limit and the progress line always agree."""
    total = len(uids_all)
    planned = uids_all[-limit:] if total > limit else uids_all
    return planned, total


def eta_seconds(elapsed: float, done: int, planned: int) -> float | None:
    if done <= 0 or planned <= done:
        return None
    return elapsed / done * (planned - done)


def write_status(path: Path, *, stage: str, folder: str, done: int, total: int,
                 started: str, error: str | None = None, finish: bool = False,
                 coverage: dict | None = None, since: str | None = None,
                 complete: bool | None = None, errors: list[str] | None = None) -> None:
    try:
        doc = {
            "stage": stage, "folder": folder, "done": done, "total": total,
            "started_at": started,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "finished": bool(finish), "ok": bool(finish and not error),
            "error": error,
        }
        if complete is not None:
            doc["complete"] = bool(complete)
        if errors is not None:
            doc["errors"] = list(errors)
        if coverage is not None:
            doc["coverage"] = coverage
        if since:
            doc["since"] = since
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def finalize_status(path: Path, *, started: str, coverage: dict,
                    planned_total: int, fetched_total: int, errors: list[str],
                    since: str | None) -> dict:
    """Final write: any error/omission => complete false + 部分 stage."""
    complete = not errors and fetched_total >= planned_total
    stage = "邮件读取完成" if complete else "读取完成(部分,有遗漏)"
    write_status(path, stage=stage, folder="-", done=fetched_total,
                 total=planned_total, started=started,
                 error="; ".join(errors) if errors else None,
                 finish=True, coverage=coverage, since=since,
                 complete=complete, errors=errors)
    return {"complete": complete, "planned": planned_total, "fetched": fetched_total,
            "errors": errors, "coverage": coverage}


def fmt_secs(s: float) -> str:
    s = int(s)
    return f"{s // 60:02d}:{s % 60:02d}"


def decode_imap_name(b) -> str:
    """Decode an IMAP4 modified-UTF-7 mailbox name (e.g. &XfJT0ZAB- = 已发送)."""
    if isinstance(b, str):
        b = b.encode("utf-8", "replace")
    try:
        s = b.decode("ascii")
    except (UnicodeDecodeError, AttributeError):
        return b.decode("utf-8", "replace")
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "&":
            j = s.find("-", i + 1)
            if j == -1:
                out.append(s[i:])
                break
            b64 = s[i + 1:j]
            if not b64:
                out.append("&")
            else:
                try:
                    pad = "=" * ((4 - len(b64) % 4) % 4)
                    out.append(base64.b64decode(b64.replace(",", "/") + pad)
                               .decode("utf-16-be"))
                except Exception:
                    out.append(s[i:j + 1])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _strip_quotes(name: bytes) -> bytes:
    if len(name) >= 2 and name[:1] == b'"' and name[-1:] == b'"':
        return name[1:-1]
    return name


def resolve_folders(M: imaplib.IMAP4_SSL, spec: str) -> list[tuple[str, str]]:
    """[(raw_server_name, display_name)]. LIST responses are parsed from the
    raw bytes (imaplib yields tuples whose last element is the mailbox name,
    often quoted and modified-UTF-7 encoded); SELECT must use the raw name."""
    if spec.lower() != "auto":
        return [(f.strip(), f.strip()) for f in spec.split(",") if f.strip()]
    folders: list[tuple[str, str]] = [("INBOX", "INBOX")]
    try:
        status, data = M.list()
        if status == "OK":
            for item in data or []:
                if not item:
                    continue
                if isinstance(item, tuple):
                    name_b = item[-1] or b""
                elif isinstance(item, bytes):
                    # e.g. b'(\\HasNoChildren) "/" "&XfJT0ZAB-"'
                    m = re.search(rb'"([^"]*)"\s*$', item)
                    if m:
                        name_b = m.group(1)
                    else:
                        name_b = item.rsplit(b" ", 1)[-1]
                else:
                    continue
                raw = _strip_quotes(name_b).decode("latin-1")
                display = decode_imap_name(_strip_quotes(name_b))
                if display.lower() != "inbox" and any(
                        p in display.lower() for p in SENT_PATTERNS):
                    folders.append((raw, display))
                    break
    except Exception:
        pass
    return folders


def pick_strategy(host: str, strategy: str, search_count: int,
                  exists_count: int) -> str:
    """Effective per-folder strategy. auto = search, EXCEPT: known unreliable
    providers go straight to enum, and a SEARCH that returns 0 on a
    non-empty folder is the exact yunyou failure signature -> enum. A
    legitimately empty folder stays on search (no wasteful walk)."""
    if strategy == "enum":
        return "enum"
    if strategy == "search":
        return "search"
    host_l = (host or "").lower()
    if any(host_l.endswith(s) for s in UNRELIABLE_HOST_SUFFIXES):
        return "enum"
    if search_count == 0 and exists_count > 0:
        return "enum"
    return "search"


def parse_internaldate(raw: bytes) -> datetime | None:
    m = INTERNALDATE_RE.search(raw or b"")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1).decode("ascii"),
                                 "%d-%b-%Y %H:%M:%S %z")
    except Exception:
        return None


def uids_by_enumeration(M, seq_lo: int, seq_hi: int, since_dt: datetime | None,
                        log) -> tuple[list[bytes], int]:
    """Walk sequence numbers seq_lo..seq_hi (read-only UID+INTERNALDATE),
    date-filter, return (matching_uids, scanned). Immune to server SEARCH
    bugs; capped by the caller's window."""
    matched: list[bytes] = []
    scanned = 0
    for seq in range(seq_lo, seq_hi + 1):
        scanned += 1
        try:
            st, data = M.fetch(str(seq), "(UID INTERNALDATE)")
        except Exception as e:
            raise  # connection-level: caller decides (fatal vs record)
        payload = b""
        if data and data[0]:
            payload = data[0][0] if isinstance(data[0], tuple) else data[0]
        um = UID_RE.search(payload)
        if not um:
            continue
        if since_dt is not None:
            idate = parse_internaldate(payload)
            if idate is None or idate.date() < since_dt.date():
                continue
        matched.append(um.group(1))
        if scanned % 25 == 0:
            log(f"[检查邮件 {scanned}/{seq_hi - seq_lo + 1}] 已按日期筛出 {len(matched)} 封…")
    return matched, scanned


def run_fetch(M, *, host: str, folders_spec: str = "auto", since: str | None = None,
              limit: int = 120, strategy: str = "auto", enum_window: int = 1000,
              out_dir: Path, status_path: Path, started: str,
              log=lambda m: None) -> dict:
    """Fetch inbox+sent into out_dir. Takes a connected, logged-in client.
    Never raises for mailbox-level problems — records them and completes
    with complete=false. Returns the finalize result."""
    since_dt = None
    if since:
        y, m, d = (int(x) for x in since.split("-"))
        since_dt = datetime(y, m, d, tzinfo=timezone.utc)

    errors: list[str] = []
    coverage: dict[str, dict] = {}
    planned_total = 0
    fetched_total = 0
    t0 = time.monotonic()
    out_dir.mkdir(parents=True, exist_ok=True)

    def progress_write(stage: str, folder: str) -> None:
        write_status(status_path, stage=stage, folder=folder, done=fetched_total,
                     total=planned_total, started=started, coverage=coverage,
                     since=since, complete=False, errors=errors)

    try:
        folders = resolve_folders(M, folders_spec)
        log(f"将读取文件夹:{', '.join(d for _, d in folders)}(只读,不会改动任何邮件)")
        if folders_spec.lower() == "auto" and len(folders) < 2:
            errors.append("未找到已发送文件夹:只拉取了收件箱,收发不完整;"
                          "请确认邮箱的已发送文件夹名称,或用 --folders 指定")
        for raw, display in folders:
            progress_write("检查邮件", display)
            try:
                status, sel = M.select(mailbox=f'"{raw}"' if " " in raw else raw,
                                       readonly=True)
            except Exception as e:
                errors.append(f"文件夹 {display} 无法打开: {e}")
                log(f"⚠ 打不开文件夹 {display}({e});记为遗漏,继续其他文件夹。")
                coverage[display] = {"total": None, "scanned": 0, "fetched": 0,
                                     "skipped": True}
                continue
            if status != "OK" or not sel or not sel[0]:
                errors.append(f"文件夹 {display} 无法打开")
                log(f"⚠ 打不开文件夹 {display};记为遗漏,继续其他文件夹。")
                coverage[display] = {"total": None, "scanned": 0, "fetched": 0,
                                     "skipped": True}
                continue
            count = int(sel[0])

            uids_all: list[bytes] = []
            scanned = 0
            truncated = False
            eff = "search"
            if strategy in ("auto", "search"):
                criteria = f'(SINCE "{since}")' if since else "ALL"
                try:
                    status, data = M.uid("SEARCH", None, criteria)
                    uids_all = data[0].split() if status == "OK" else []
                    eff = pick_strategy(host, strategy, len(uids_all), count)
                except Exception:
                    uids_all = []
                    eff = pick_strategy(host, strategy, -1, count)
            else:
                eff = "enum"
            if eff == "enum":
                window = max(0, min(enum_window, count))
                seq_lo = count - window + 1
                try:
                    uids_all, scanned = uids_by_enumeration(
                        M, seq_lo if window else count + 1, count, since_dt, log)
                except Exception as e:
                    errors.append(f"文件夹 {display} 枚举中断: {e}")
                    log(f"⚠ 文件夹 {display} 枚举中断({e});记为遗漏,继续其他文件夹。")
                    coverage[display] = {"total": count, "scanned": 0, "fetched": 0,
                                         "skipped": True}
                    continue
                truncated = count > scanned
                if truncated:
                    errors.append(f"文件夹 {display}:枚举窗口 {enum_window} 封,"
                                  f"未覆盖更早的 {count - scanned} 封,覆盖不完整")

            planned, total = plan_folder_uids(uids_all, limit)
            # coverage "total" must reflect the server's folder size (so the
            # omitted-count in the customer records is truthful), while the
            # progress plan can be smaller (limit / enum window)
            cov_total = count if eff == "enum" else total
            coverage[display] = {"total": cov_total, "scanned": scanned or total,
                                 "fetched": 0}
            if truncated:
                coverage[display]["truncated"] = True
            planned_total += len(planned)
            if not planned:
                log(f"[0/0] {display} · 该范围内没有邮件,继续下一个文件夹。")
                continue
            log(f"[0/{len(planned)}] {display} · 开始读取正文…")
            for i, uid in enumerate(planned, 1):
                progress_write("读取正文", display)
                try:
                    status, parts = M.uid("FETCH", uid, "(BODY.PEEK[])")
                    raw_bytes = next((p[1] for p in parts if isinstance(p, tuple)), None) \
                        if status == "OK" and parts else None
                    if not raw_bytes:
                        raise ValueError("服务器未返回邮件内容")
                except (imaplib.IMAP4.abort, OSError, ssl.SSLError, socket.timeout) as e:
                    errors.append(f"文件夹 {display} 连接中断于第 {uid.decode()} 封: {e}")
                    log(f"✘ 连接中断,已取到 {fetched_total} 封。稍后重新运行即可补齐,"
                        f"不会重复下载。")
                    result = finalize_status(status_path, started=started,
                                             coverage=coverage,
                                             planned_total=planned_total,
                                             fetched_total=fetched_total,
                                             errors=errors, since=since)
                    result["fatal"] = True
                    return result
                except Exception as e:
                    errors.append(f"文件夹 {display} 第 {uid.decode()} 封读取失败: {e}")
                    log(f"[{i}/{len(planned)}] ⚠ 一封读取失败,已记为遗漏,继续。")
                    continue
                write_mail(raw_bytes, out_dir, getattr(M, "_tcm_user", "?"), display, uid.decode())
                coverage[display]["fetched"] += 1
                fetched_total += 1
                elapsed = time.monotonic() - t0
                eta = eta_seconds(elapsed, i, len(planned))
                eta_txt = f"预计剩余 {fmt_secs(eta)}" if eta is not None else "预计剩余:统计中"
                log(f"[{i}/{len(planned)}] {display} · 已用时 {fmt_secs(elapsed)} · {eta_txt}")
            log(f"[{len(planned)}/{len(planned)}] {display} · 计划内读取完成。")
    except Exception as e:  # last-resort: keep the status file truthful
        errors.append(f"未预期的中断: {e}")
    result = finalize_status(status_path, started=started, coverage=coverage,
                             planned_total=planned_total, fetched_total=fetched_total,
                             errors=errors, since=since)
    result["fatal"] = result["complete"] is False and not fetched_total and not planned_total
    return result


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="只读 IMAP 拉取(收件+已发送)为 .eml 文件")
    ap.add_argument("--host", required=True, help="IMAP 服务器,如 imap.exmail.qq.com")
    ap.add_argument("--user", required=True, help="完整邮箱地址")
    ap.add_argument("--folders", default="auto",
                    help='要拉取的文件夹,逗号分隔;"auto"=收件箱+自动探测的已发送(默认)')
    ap.add_argument("--since", help="只拉取该日期之后的邮件 YYYY-MM-DD")
    ap.add_argument("--limit", type=validate_limit, default=120,
                    help="每个文件夹最多拉取多少封(默认 120,最小 1)")
    ap.add_argument("--strategy", default="auto", choices=["auto", "search", "enum"],
                    help="auto=已知不可靠服务商优先逐封枚举、SEARCH 可疑时自动回退;"
                         "enum=强制逐封枚举(有窗口上限)")
    ap.add_argument("--enum-window", type=validate_limit, default=1000,
                    help="枚举策略最多检查最近多少封(默认 1000;超出部分记为覆盖不完整)")
    ap.add_argument("--out", default=str(Path.cwd() / "内部数据" / "inbox"))
    ap.add_argument("--status", default=None,
                    help="进度状态文件(默认 <out>/../fetch-status.json)")
    args = ap.parse_args()

    out_dir = Path(args.out)
    status_path = Path(args.status) if args.status else out_dir.parent / "fetch-status.json"

    print("=== 外贸客户记忆助手 · 连接邮箱(只读)===")
    print(f"服务器:{args.host} · 账号:{args.user}")
    print("提示:输入授权码时屏幕不会显示任何字符,输入完成后按 Enter 回车。")
    print()
    pw = getpass.getpass(f"请输入 {args.user} 的 IMAP 授权码: ").strip()
    if not pw:
        print("未输入授权码,已退出。什么都没有被修改,重新运行即可。")
        sys.exit(2)

    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_status(status_path, stage="连接邮箱", folder="-", done=0, total=0,
                 started=started, since=args.since, complete=False, errors=[])

    ctx = ssl.create_default_context()
    try:
        M = imaplib.IMAP4_SSL(args.host, 993, ssl_context=ctx)
        M._tcm_user = args.user  # for .eml filenames inside run_fetch
    except (socket.timeout, OSError, ssl.SSLError) as e:
        write_status(status_path, stage="连接失败", folder="-", done=0, total=0,
                     started=started, error=str(e), complete=False,
                     errors=["连接服务器失败"])
        print(f"✘ 连不上服务器({args.host}:993)。")
        print("  可能原因:网络/代理未开、服务器地址不对。")
        print("  下一步:确认网络后重新运行;如果反复失败,把本窗口信息发给 Agent。")
        sys.exit(2)

    try:
        M.login(args.user, pw)
    except imaplib.IMAP4.error as e:
        write_status(status_path, stage="登录失败", folder="-", done=0, total=0,
                     started=started, error=str(e), complete=False, errors=["登录失败"])
        print("✘ 登录被服务器拒绝。")
        print("  最常见原因:授权码输错、邮箱没有开启 IMAP 服务、或需要使用")
        print("  「客户端专用密码」而不是登录密码。密码刚才只在你这台电脑上")
        print("  使用了一次,没有被保存。")
        print("  下一步:到邮箱网页版设置里生成/重置授权码,然后重新运行本文件。")
        sys.exit(2)

    result: dict = {}
    try:
        result = run_fetch(M, host=args.host, folders_spec=args.folders,
                           since=args.since, limit=args.limit,
                           strategy=args.strategy, enum_window=args.enum_window,
                           out_dir=out_dir, status_path=status_path,
                           started=started, log=print)
    finally:
        try:
            M.logout()
        except Exception:
            pass
    print()
    if result.get("complete"):
        print(f"✔ 邮件读取完成:共 {result['fetched']} 封,保存在 {out_dir}")
        print("  回到 Agent 窗口,告诉它:邮件读取完成")
    else:
        print(f"⚠ 邮件读取完成(部分):计划 {result['planned']} 封,成功 {result['fetched']} 封;"
              f"{len(result['errors'])} 条问题已记录(含遗漏与覆盖范围)。")
        print("  状态文件已如实标记 complete=false,不会当作完整成功。")
        print("  下一步:回 Agent 说「邮件读取完成(部分)」,Agent 会检查遗漏并建议"
              "重新运行补齐;重新运行不会重复下载已取到的邮件。")
        if result.get("fatal"):
            sys.exit(2)


if __name__ == "__main__":
    main()
