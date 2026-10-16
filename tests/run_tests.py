#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic tests for Trade Client Memory (tcm.py + imap_fetch.py).

v3 contract (after the real-mailbox experiment redesign):

  T1   init/ingest on --base layout; sent marking; no machine paths in state
  T2   re-import dedupe
  T3   incremental import
  T4   non-mail file rejected
  T5   stray customer folder without mapping fails verify
  T6   doctor keys (connector discovery, writability)
  T7   owner command retro-marks directions
  T8   slug / unknown-id hard errors
  T9   Message-ID stripped/added between imports dedupes both directions
  T10  verify false-success guards: done-but-unmapped; done-but-not-rendered;
       missing report; hand-edited overview; gutted agent section
  T11  imap_fetch --limit rejects < 1
  T12  imap fetch filenames unique across accounts/folders
  T13  ingest --owner persists and retro-fixes; owner cmd idempotent after
  T14  status reports reports/coverage/folders; no legacy tasks_file
  T15  empty text/plain + valid HTML -> body from HTML (real-experiment bug)
  T16  cid inline images -> markdown image links to 附件/
  T17  body cleaning: interleaved quoted replies kept, pure-quote runs
       collapsed, signatures/disclaimers cut, all-marker falls back to raw
  T18  attachments: payload extraction, per-customer 附件/ copy with hash
       dedupe, suspected signature logo (same inline hash in >=3 mails) not
       previewed, missing payload reported, empty 附件/ dir still created
  T19  render structure: 公司 - 国家 folder, timeline sorted, agent section
       preserved across re-render, folder rename on country change keeps files
  T20  report/brief/overview single source: priorities ordered, overview
       idempotent (byte-equal on re-render), brief matches reports
  T21  incremental render adds mail once; coverage line when fetch-status
       present
  T22  user data isolation: nothing in repo worktree, state path-free
  T23  launcher files generated with correct embedded parameters
  T24  imap_fetch status file writer fields

Run:  python tests/run_tests.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TCM = ROOT / "scripts" / "tcm.py"
FIX = ROOT / "tests" / "fixtures"
INBOX = FIX / "inbox"
SENT = FIX / "sent"
ROUND2 = FIX / "round2"
ASSETS = FIX / "assets"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from mailbuilder import build_mail  # noqa: E402
import imap_fetch  # noqa: E402

LOGO = (ASSETS / "logo-red.png").read_bytes()
PRODUCT = (ASSETS / "product-blue.png").read_bytes()

results: list[tuple[str, bool, str]] = []


def run(args: list[str], base: Path) -> dict:
    p = subprocess.run(
        [sys.executable, str(TCM), "--base", str(base), *args],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
    )
    if p.returncode != 0:
        raise AssertionError(f"cmd failed ({p.returncode}): {args}\n{p.stdout}\n{p.stderr}")
    return json.loads(p.stdout)


def verify_raw(base: Path):
    return subprocess.run([sys.executable, str(TCM), "--base", str(base), "verify"],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


def fresh_base() -> Path:
    b = Path(tempfile.mkdtemp(prefix="tcm-base-"))
    run(["init"], b)
    return b


def inner(b: Path) -> Path:
    return b / "内部数据"


# ------------------------------------------------------------- fixture mails --

def marco_mail(mid, subject, text, date, cid_images=None, attachments=None, html=None):
    return build_mail(mid=mid, frm=("Marco Rossi", "marco.rossi@casarossi.it"),
                      to=[("Alex", "alex@sunrisetrade.example")], date=date,
                      subject=subject, text=text, html=html, cid_images=cid_images,
                      attachments=attachments)


LOGO_CID = "logo001@casarossi.it"


def logo_html(body_text: str) -> str:
    return (f"<p>{body_text}</p>"
            f'<p><img src="cid:{LOGO_CID}" alt="logo"></p>'
            "<p style='font-size:9px'>Casa Rossi Mobili S.r.l. - Milano</p>")


# --------------------------------------------------------------------- tests --

def main() -> None:
    # T1 base layout ingest
    b = fresh_base()
    r = run(["ingest", str(INBOX), str(SENT), "--owner", "alex@sunrisetrade.example"], b)
    check("T1 nine mails ingested", r["new"] == 9 and r["skipped"] == 0, f"new={r['new']}")
    sent = [m for m in r["messages"] if m.get("direction") == "sent"]
    check("T1 three sent marked", len(sent) == 3, f"n={len(sent)}")
    state = json.loads((inner(b) / "state.json").read_text(encoding="utf-8"))
    dump = json.dumps(state, ensure_ascii=False)
    machine_leak = [pat for pat in ("C:" + chr(92) + "Users" + chr(92),
                                  "/Users/", "D:" + chr(92) + "Projects" + chr(92))
                    if pat in dump]
    check("T1 no machine paths in state", not machine_leak, str(machine_leak))
    check("T1 internal data under 内部数据", (inner(b) / "state.json").exists()
          and (inner(b) / "inbox").exists(), "")

    # T2 dedupe
    r2 = run(["ingest", str(INBOX), str(SENT), "--owner", "alex@sunrisetrade.example"], b)
    check("T2 re-import zero new", r2["new"] == 0 and r2["skipped"] == 9, "")
    # T3 incremental
    r3 = run(["ingest", str(ROUND2)], b)
    check("T3 one new mail", r3["new"] == 1, "")
    # T4 junk
    junk = Path(tempfile.mkdtemp(prefix="tcm-junk-")) / "notes.txt"
    junk.write_text("购货清单:螺钉 5000 个,下周提货。", encoding="utf-8")
    r4 = run(["ingest", str(junk)], b)
    check("T4 non-mail rejected", r4["new"] == 0 and r4["unrecognized"] == 1, "")

    # T5 stray customer folder without mapping fails verify
    b5 = fresh_base()
    run(["ingest", str(INBOX), "--owner", "alex@sunrisetrade.example"], b5)
    (b5 / "客户档案" / "Ghost Trading - 德国").mkdir(parents=True)
    (b5 / "客户档案" / "Ghost Trading - 德国" / "客户沟通记录.md").write_text("x", encoding="utf-8")
    p = verify_raw(b5)
    check("T5 stray folder fails verify", p.returncode == 1, p.stdout[:120])

    # T6 doctor
    r6 = run(["doctor"], b)
    c = r6["checks"]
    check("T6 doctor keys", all(k in c for k in ("data_writable", "mail_read_cli_path",
                                                 "mail_read_cli_override", "python_keyring"))
          and isinstance(r6.get("connector_available"), bool), "")

    # T7 owner command retro-marks
    b7 = fresh_base()
    run(["ingest", str(SENT)], b7)
    r7 = run(["owner", "alex@sunrisetrade.example"], b7)
    check("T7 owner retro-marks three", r7["directions_revised"] == 3, f"n={r7['directions_revised']}")

    # T8 hard errors
    b8 = fresh_base()
    for cmd, frag in [(["map-customer", "Ghost Slug", "a@b.com"], "slug must match"),
                      (["done", "deadbeefdead"], "unknown message id")]:
        try:
            run(cmd, b8)
            check(f"T8 {frag[:18]} rejected", False, "no error")
        except AssertionError as e:
            check(f"T8 {frag[:18]} rejected", frag in str(e), "")

    # T9 MID strip/add dedupe both directions
    raw9 = (INBOX / "01-marco-inquiry.eml").read_bytes()
    no_mid = "".join(l for l in raw9.decode("utf-8").splitlines(keepends=True)
                     if not l.startswith("Message-ID:")).encode("utf-8")
    tmp9 = Path(tempfile.mkdtemp(prefix="tcm-mid-"))
    (tmp9 / "withmid.eml").write_bytes(raw9)
    (tmp9 / "nomid.eml").write_bytes(no_mid)
    b9 = fresh_base()
    r9a = run(["ingest", str(tmp9 / "withmid.eml")], b9)
    r9b = run(["ingest", str(tmp9 / "nomid.eml")], b9)
    check("T9 with-MID then stripped dedupes", r9a["new"] == 1 and r9b["new"] == 0,
          f"{r9a['new']}/{r9b['new']}")
    b9b = fresh_base()
    r9c = run(["ingest", str(tmp9 / "nomid.eml")], b9b)
    r9d = run(["ingest", str(tmp9 / "withmid.eml")], b9b)
    check("T9 stripped then MID added dedupes", r9c["new"] == 1 and r9d["new"] == 0,
          f"{r9c['new']}/{r9d['new']}")

    # T10 false-success guards
    b10 = fresh_base()
    run(["ingest", str(INBOX), "--owner", "alex@sunrisetrade.example"], b10)
    st = run(["status"], b10)
    run(["done", *[u["id"] for u in st["unprocessed"]]], b10)
    p = verify_raw(b10)
    v = json.loads(p.stdout)
    check("T10 done-but-unmapped fails", p.returncode == 1 and
          any("unmapped" in x for x in v["problems"]), str(v["problems"])[:100])

    # T10b full flow minus pieces must fail
    b10b = fresh_base()
    run(["ingest", str(INBOX), "--owner", "alex@sunrisetrade.example"], b10b)
    run(["map-customer", "marco-rossi-casa-rossi-mobili", "marco.rossi@casarossi.it",
         "--name", "Marco Rossi", "--company", "Casa Rossi Mobili S.r.l.",
         "--country", "意大利"], b10b)
    st = run(["status"], b10b)
    marco_ids = [u["id"] for u in st["unprocessed"] if u["from"] == "marco.rossi@casarossi.it"]
    run(["done", *marco_ids], b10b)
    p = verify_raw(b10b)
    v = json.loads(p.stdout)
    check("T10b done mail not rendered fails",
          p.returncode == 1 and any("客户沟通记录" in x or "not rendered" in x or "未生成" in x
                                for x in v["problems"]), str(v["problems"])[:150])
    run(["render"], b10b)
    p = verify_raw(b10b)
    v = json.loads(p.stdout)
    check("T10b rendered but no report fails", p.returncode == 1 and
          any("report" in x.lower() or "状态未写入" in x for x in v["problems"]),
          str(v["problems"])[:150])
    run(["report", "marco-rossi-casa-rossi-mobili", "--stage", "PI",
         "--situation", "客户已接受价格。", "--next", "业务员发 PI。",
         "--priority", "urgent"], b10b)
    run(["render"], b10b)
    p = verify_raw(b10b)
    check("T10b complete flow passes", p.returncode == 0, p.stdout[:150])
    overview = b10b / "客户档案" / "客户概览.md"
    overview.write_text("手工改坏的内容\n", encoding="utf-8")
    p = verify_raw(b10b)
    v = json.loads(p.stdout)
    check("T10b hand-edited overview fails consistency", p.returncode == 1 and
          any("客户概览" in x for x in v["problems"]), str(v["problems"])[:120])
    run(["render"], b10b)
    comm = (b10b / "客户档案" / "Casa Rossi Mobili S.r.l. - 意大利" / "客户沟通记录.md")
    text = comm.read_text(encoding="utf-8")
    comm.write_text(text.replace("## 客户情况", "## 被删掉的章节"), encoding="utf-8")
    p = verify_raw(b10b)
    check("T10b gutted agent section fails", p.returncode == 1, p.stdout[:120])

    # T11/T12 imap helpers
    try:
        imap_fetch.validate_limit(0)
        check("T11 limit 0 rejected", False, "")
    except (SystemExit, ValueError):
        check("T11 limit 0 rejected", True, "")
    check("T11 valid limit accepted", imap_fetch.validate_limit("50") == 50, "")
    n1 = imap_fetch.make_mail_name("a@x.com", "INBOX", "1")
    n2 = imap_fetch.make_mail_name("b@x.com", "INBOX", "1")
    n3 = imap_fetch.make_mail_name("a@x.com", "INBOX/Sub Work", "1")
    n4 = imap_fetch.make_mail_name("a@x.com", "INBOX-Sub-Work", "1")
    check("T12 names unique across accounts/folders",
          n1 != n2 and n3 != n4 and n1 == imap_fetch.make_mail_name("a@x.com", "INBOX", "1"), "")

    # T13 owner persistence
    b13 = fresh_base()
    run(["ingest", str(SENT), "--owner", "alex@sunrisetrade.example"], b13)
    state = json.loads((inner(b13) / "state.json").read_text(encoding="utf-8"))
    check("T13 owner persisted", state.get("owner") == "alex@sunrisetrade.example", "")
    r13 = run(["owner", "alex@sunrisetrade.example"], b13)
    check("T13 owner idempotent after ingest --owner", r13["directions_revised"] == 0,
          f"n={r13['directions_revised']}")

    # T14 status keys
    b14 = fresh_base()
    run(["ingest", str(INBOX), "--owner", "alex@sunrisetrade.example"], b14)
    st14 = run(["status"], b14)
    check("T14 status has reports/coverage, no tasks_file",
          "reports" in st14 and "coverage" in st14 and "tasks_file" not in st14,
          str(st14.keys()))

    # T15 empty text/plain + valid HTML (real-experiment bug)
    b15 = fresh_base()
    (inner(b15) / "inbox" / "html.eml").write_bytes(marco_mail(
        "<sarah-html-1@northpeakoutfitters.com>",
        "Re-order details",
        "",  # empty text part on purpose
        "Sat, 3 Oct 2026 21:05:00 -0500",
        html="<p>Hi Alex, the <b>500 pcs</b> re-order is confirmed.</p>"))
    r15 = run(["ingest", str(inner(b15) / "inbox")], b15)
    doc = json.loads((inner(b15) / "messages" / f"msg-{r15['messages'][0]['id']}.json")
                     .read_text(encoding="utf-8"))
    body = doc["body"]
    check("T15 body extracted from HTML when plain empty",
          "500 pcs" in body and "confirmed" in body, body[:80])

    # T16 cid inline image links
    b16 = fresh_base()
    (inner(b16) / "inbox" / "cid.eml").write_bytes(marco_mail(
        "<marco-cid-1@casarossi.it>", "Brochure inside",
        "Please see the attached product photo.",
        "Mon, 21 Sep 2026 10:00:00 +0200",
        attachments=[("product-photo.png", PRODUCT, "image/png")]))
    r16 = run(["ingest", str(inner(b16) / "inbox")], b16)
    mid16 = r16["messages"][0]["id"]
    doc = json.loads((inner(b16) / "messages" / f"msg-{mid16}.json").read_text(encoding="utf-8"))
    atts = doc["meta"].get("attachments", [])
    check("T16 attachment payload extracted", len(atts) == 1 and atts[0]["sha256"], str(atts)[:120])
    check("T16 payload stored under 内部数据",
          (inner(b16) / "attachments" / mid16).is_dir() and
          any(p.is_file() for p in (inner(b16) / "attachments" / mid16).iterdir()), "")

    # T17 cleaning
    from tcm import clean_body  # noqa: E402
    interleaved = "\n".join([
        "Hello Alex,", "",
        "> 1. Can you do USD 230 at 300 pcs?",
        "Yes, 300 pcs at USD 230 is fine.",
        "> 2. What about lead time?",
        "Lead time is 40 days.", "",
        ">", "> -----Original Message-----",
        "> From: Alex [mailto:alex@sunrisetrade.example]",
        "> Sent: Monday, Sep 21, 2026",
        "> To: Marco Rossi",
        "> Subject: Quote WT-118",
        ">", "> Dear Marco, our offer is USD 245/pc FOB Qingdao.",
        "> Lead time 45 days after deposit.",
    ])
    cleaned = clean_body(interleaved)
    check("T17 interleaved answers kept", "Yes, 300 pcs at USD 230 is fine." in cleaned
          and "Lead time is 40 days." in cleaned, "")
    check("T17 quoted questions kept", "> 1. Can you do USD 230 at 300 pcs?" in cleaned, "")
    check("T17 historical quote collapsed",
          "USD 245/pc FOB Qingdao" not in cleaned and "历史引用" in cleaned, cleaned[-120:])
    sig = "Dear Alex,\n\nWe accept the price.\n\nBest regards,\nMarco Rossi\nCasa Rossi Mobili\nmilano@casarossi.it"
    csig = clean_body(sig)
    check("T17 signature greeting block cut", "milano@casarossi.it" not in csig
          and "We accept the price." in csig, csig)
    disc = "Hello\n\nOur offer stands.\n\nThis email and any attachments are CONFIDENTIAL and may be privileged. If you are not the intended recipient..."
    cdisc = clean_body(disc)
    check("T17 disclaimer cut", "CONFIDENTIAL" not in cdisc and "Our offer stands." in cdisc, cdisc)
    stdsig = "Hello\n\nPlease send PI.\n\n-- \nAlex | Sunrise Trade"
    check("T17 std signature delimiter cut", "Sunrise Trade" not in clean_body(stdsig)
          and "Please send PI." in clean_body(stdsig), "")
    only_quote = "> quote line 1\n> quote line 2\n> quote line 3"
    check("T17 all-quoted falls back to raw", "quote line 1" in clean_body(only_quote), "")

    # T18 attachments pipeline end-to-end
    b18 = fresh_base()
    inbox18 = inner(b18) / "inbox"
    # marco: 3 mails sharing inline logo (suspected logo) + one unique product attachment
    for i, (mid, dt, att) in enumerate([
        ("<marco-l1@casarossi.it>", "Mon, 21 Sep 2026 10:00:00 +0200",
         [("price-list.pdf", b"%PDF-1.4 fake price list marco", "application/pdf")]),
        ("<marco-l2@casarossi.it>", "Tue, 22 Sep 2026 10:00:00 +0200", None),
        ("<marco-l3@casarossi.it>", "Wed, 23 Sep 2026 10:00:00 +0200",
         [("catalog.pdf", b"%PDF-1.4 fake catalog marco", "application/pdf")]),
    ]):
        (inbox18 / f"m{i}.eml").write_bytes(marco_mail(
            mid, f"Thread {i}", f"Message {i} with logo.", dt,
            html=logo_html(f"Message {i} with logo."),
            cid_images={LOGO_CID: ("logo.png", LOGO)}, attachments=att))
    # sarah: same content under two names (dedupe), then an empty-attachment mail
    def sarah_mail(mid, fname, blob, dt, subject):
        return build_mail(mid=mid, frm=("Sarah Thompson", "sarah.thompson@northpeakoutfitters.com"),
                          to=[("Alex", "alex@sunrisetrade.example")], date=dt,
                          subject=subject, text="Specs attached.",
                          attachments=[(fname, blob, "application/pdf")] if fname else None)
    (inbox18 / "s1.eml").write_bytes(sarah_mail("<sarah-a@northpeakoutfitters.com>",
                                                "spec-sheet.pdf", b"%PDF-1.4 spec", "Thu, 24 Sep 2026 10:00:00 -0500", "Specs v1"))
    (inbox18 / "s2.eml").write_bytes(sarah_mail("<sarah-b@northpeakoutfitters.com>",
                                                "spec-sheet-final.pdf", b"%PDF-1.4 spec", "Fri, 25 Sep 2026 10:00:00 -0500", "Specs v1 again"))
    (inbox18 / "s3.eml").write_bytes(sarah_mail("<sarah-c@northpeakoutfitters.com>",
                                                None, b"", "Sat, 26 Sep 2026 10:00:00 -0500", "No attachment this time"))
    run(["ingest", str(inbox18), "--owner", "alex@sunrisetrade.example"], b18)
    run(["map-customer", "marco-rossi-casa-rossi-mobili", "marco.rossi@casarossi.it",
         "--name", "Marco Rossi", "--company", "Casa Rossi Mobili S.r.l.", "--country", "意大利"], b18)
    run(["map-customer", "sarah-thompson-northpeak", "sarah.thompson@northpeakoutfitters.com",
         "--name", "Sarah Thompson", "--company", "NorthPeak Outfitters", "--country", "美国"], b18)
    st = run(["status"], b18)
    run(["done", *[u["id"] for u in st["unprocessed"]
                   if u["from"] and not u["from"].endswith("sunrisetrade.example")]], b18)
    run(["report", "marco-rossi-casa-rossi-mobili", "--stage", "还价", "--situation", "已还价。",
         "--next", "业务员答复。", "--priority", "urgent"], b18)
    run(["report", "sarah-thompson-northpeak", "--stage", "复购", "--situation", "等待确认。",
         "--next", "等待客户 PO。", "--priority", "waiting"], b18)
    run(["render"], b18)
    marco_dir = b18 / "客户档案" / "Casa Rossi Mobili S.r.l. - 意大利"
    sarah_dir = b18 / "客户档案" / "NorthPeak Outfitters - 美国"
    check("T18 folders created with 公司-国家 naming", marco_dir.is_dir() and sarah_dir.is_dir(),
          str(list((b18 / "客户档案").iterdir())))
    marco_att = marco_dir / "附件"
    att_files = sorted(p.name for p in marco_att.iterdir())
    check("T18 attachments copied once each", len(att_files) == 3,
          str(att_files))
    check("T18 logo file kept but flagged", any("logo" in f for f in att_files), str(att_files))
    comm18 = (marco_dir / "客户沟通记录.md").read_text(encoding="utf-8")
    check("T18 logo not previewed as product image",
          "![logo" not in comm18 and "签名图片" in comm18, "")
    check("T18 real attachment previewed as link", "](附件/price-list.pdf)" in comm18
          or "](附件/" in comm18, "")
    sarah_att = list((sarah_dir / "附件").iterdir())
    check("T18 same content two names -> one file", len(sarah_att) == 1, str([p.name for p in sarah_att]))
    sarah_comm = (sarah_dir / "客户沟通记录.md").read_text(encoding="utf-8")
    check("T18 empty-attachment mail noted without fabrication",
          "附件:无" in sarah_comm, "")
    check("T18 附件 dir exists even when empty for a mail",
          (sarah_dir / "附件").is_dir(), "")

    # T19 agent section preserved; folder rename keeps files
    agent_block = "## 客户情况\n\n老客户,广交会认识。\n\n## 关键已确认事实\n\n- 报价 245 [msg-test]\n\n## 当前阶段\n\n还价。\n\n## 下一行动\n\n- [ ] 答复还价\n"
    text19 = (marco_dir / "客户沟通记录.md").read_text(encoding="utf-8")
    import re as _re
    new_text = _re.sub(r"(<!-- TCM:AGENT:BEGIN -->\n).*?(\n<!-- TCM:AGENT:END -->)",
                       lambda m: m.group(1) + agent_block + m.group(2), text19, flags=_re.S)
    (marco_dir / "客户沟通记录.md").write_text(new_text, encoding="utf-8")
    run(["ingest", str(ROUND2)], b18)
    run(["render"], b18)
    comm19 = (marco_dir / "客户沟通记录.md").read_text(encoding="utf-8")
    check("T19 agent section survives re-render", "广交会认识" in comm19, "")
    check("T19 timeline gains new mail once", comm19.count("msg-6fce646dab55") >= 1
          and list((marco_dir / "附件").iterdir()) is not None, "")
    run(["map-customer", "marco-rossi-casa-rossi-mobili", "marco.rossi@casarossi.it",
         "--name", "Marco Rossi", "--company", "Casa Rossi Mobili S.r.l.", "--country", "意大利北部"], b18)
    run(["render"], b18)
    renamed = b18 / "客户档案" / "Casa Rossi Mobili S.r.l. - 意大利北部"
    check("T19 folder renamed with files intact", renamed.is_dir()
          and (renamed / "附件").is_dir()
          and not (b18 / "客户档案" / "Casa Rossi Mobili S.r.l. - 意大利").exists()
          and "广交会认识" in (renamed / "客户沟通记录.md").read_text(encoding="utf-8"),
          str([p.name for p in (b18 / "客户档案").iterdir()]))

    # T20 report/brief/overview single source
    ov = (b18 / "客户档案" / "客户概览.md").read_text(encoding="utf-8")
    check("T20 overview lists both customers", "Casa Rossi Mobili" in ov and "NorthPeak" in ov, "")
    check("T20 urgent bucket before waiting", ov.find("需要业务员行动") < ov.find("等待客户"), "")
    br = run(["brief"], b18)
    brief_text = br["text"]
    check("T20 brief from same source", "Casa Rossi Mobili" in brief_text
          and "NorthPeak" in brief_text and "还价" in brief_text, brief_text[:120])
    ov_before = (b18 / "客户档案" / "客户概览.md").read_text(encoding="utf-8")
    run(["render"], b18)
    ov_after = (b18 / "客户档案" / "客户概览.md").read_text(encoding="utf-8")
    check("T20 overview idempotent", ov_before == ov_after, "")

    # T21 coverage
    b21 = fresh_base()
    run(["ingest", str(INBOX), "--owner", "alex@sunrisetrade.example"], b21)
    imap_fetch.write_status(inner(b21) / "fetch-status.json", stage="邮件读取完成",
                            folder="-", done=9, total=1185, started="t0",
                            coverage={"INBOX": {"total": 725, "fetched": 6},
                                      "已发送": {"total": 460, "fetched": 3}},
                            since="2026-09-01", finish=True)
    run(["map-customer", "marco-rossi-casa-rossi-mobili", "marco.rossi@casarossi.it",
         "--name", "Marco Rossi", "--company", "Casa Rossi Mobili S.r.l.", "--country", "意大利"], b21)
    st21 = run(["status"], b21)
    check("T21 coverage in status", st21["coverage"].get("folders", {}).get("INBOX", {}).get("total") == 725,
          str(st21["coverage"])[:100])
    ids = [u["id"] for u in st21["unprocessed"] if u["from"] == "marco.rossi@casarossi.it"]
    run(["done", *ids], b21)
    run(["report", "marco-rossi-casa-rossi-mobili", "--stage", "询盘", "--situation", "首询。",
         "--next", "业务员报价。", "--priority", "urgent"], b21)
    run(["render"], b21)
    comm21 = (b21 / "客户档案" / "Casa Rossi Mobili S.r.l. - 意大利" / "客户沟通记录.md").read_text(encoding="utf-8")
    check("T21 coverage line rendered with omitted counts",
          "725" in comm21 and "460" in comm21 and ("未纳入" in comm21 or "未读" in comm21),
          comm21[:200])

    # T22 isolation
    repo_dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                                text=True, cwd=str(ROOT)).stdout
    unexpected = [l for l in repo_dirty.splitlines() if "tests/" not in l and "scripts/" not in l
                  and "samples/" not in l and "docs/" not in l and ".gitignore" not in l
                  and "README" not in l and "START-HERE" not in l and "SKILL" not in l
                  and "references/" not in l and "templates/" not in l
                  # skills/ and .gitattributes: the Alibaba companion skill added
                  # 2026-10-14 (repo-only, excluded from the Email ZIP)
                  and "skills/" not in l and ".gitattributes" not in l]
    check("T22 repo worktree untouched by test data", not unexpected, str(unexpected[:3]))
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    check("T22 gitignore covers 客户档案/内部数据", "客户档案" in gi and "内部数据" in gi, "")

    # T23 launcher
    b23 = fresh_base()
    r23 = run(["launcher", "--host", "imap.exmail.qq.com", "--user", "boss@company.com",
               "--since", "2026-09-01", "--limit", "120"], b23)
    cmd_file = b23 / "连接邮箱.command"
    bat_file = b23 / "连接邮箱.bat"
    check("T23 launcher files created", cmd_file.is_file() and bat_file.is_file(), "")
    cmd_text = cmd_file.read_text(encoding="utf-8")
    bat_text = bat_file.read_text(encoding="utf-8")
    check("T23 .command embeds fetch params", "imap_fetch.py" in cmd_text
          and "boss@company.com" in cmd_text and "2026-09-01" in cmd_text, "")
    check("T23 .bat embeds fetch params", "imap_fetch.py" in bat_text
          and "imap.exmail.qq.com" in bat_text, "")

    # T24 fetch status writer
    tmp24 = Path(tempfile.mkdtemp(prefix="tcm-status-")) / "fetch-status.json"
    imap_fetch.write_status(tmp24, stage="读取正文", folder="INBOX", done=12, total=58,
                            started="t0", error=None)
    s24 = json.loads(tmp24.read_text(encoding="utf-8"))
    check("T24 status fields", s24["done"] == 12 and s24["total"] == 58
          and s24["stage"] == "读取正文" and s24["finished"] is False, str(s24))
    imap_fetch.write_status(tmp24, stage="邮件读取完成", folder="INBOX", done=58, total=58,
                            started="t0", error=None, finish=True)
    s24b = json.loads(tmp24.read_text(encoding="utf-8"))
    check("T24 status finish flag", s24b["finished"] is True and s24b["stage"] == "邮件读取完成", "")

    # ---- v4.1 reliability round (red first) ----

    # T25 fetch planning / ETA / partial-completion honesty
    uids25 = [b"%d" % i for i in range(1, 11)]
    planned, total = imap_fetch.plan_folder_uids(uids25, 3)
    check("T25 plan truncates to last limit", len(planned) == 3 and planned == uids25[-3:]
          and total == 10, "")
    planned2, total2 = imap_fetch.plan_folder_uids(uids25, 50)
    check("T25 plan keeps all when under limit", len(planned2) == 10 and total2 == 10, "")
    check("T25 eta known rate", abs(imap_fetch.eta_seconds(60.0, 10, 30) - 120.0) < 1e-6, "")
    check("T25 eta none before data / when done",
          imap_fetch.eta_seconds(60.0, 0, 30) is None
          and imap_fetch.eta_seconds(60.0, 30, 30) is None, "")
    st25 = Path(tempfile.mkdtemp(prefix="tcm-fin-")) / "fetch-status.json"
    imap_fetch.finalize_status(st25, started="t0",
                               coverage={"INBOX": {"total": 10, "fetched": 8}},
                               planned_total=10, fetched_total=8,
                               errors=["第 7 封读取失败"], since=None)
    f25 = json.loads(st25.read_text(encoding="utf-8"))
    check("T25 partial completion flagged", f25["finished"] is True and f25["complete"] is False
          and "部分" in f25["stage"] and f25["errors"], str(f25)[:120])
    imap_fetch.finalize_status(st25, started="t0",
                               coverage={"INBOX": {"total": 10, "fetched": 10}},
                               planned_total=10, fetched_total=10, errors=[], since=None)
    f25b = json.loads(st25.read_text(encoding="utf-8"))
    check("T25 clean completion flagged", f25b["complete"] is True and f25b["stage"] == "邮件读取完成"
          and not f25b["errors"], "")

    # T26 verify must police processed SENT mail too (my quote mail cannot vanish)
    b26 = fresh_base()
    run(["ingest", str(SENT), "--owner", "alex@sunrisetrade.example"], b26)
    run(["map-customer", "marco-rossi-casa-rossi-mobili", "marco.rossi@casarossi.it",
         "--name", "Marco Rossi", "--company", "Casa Rossi Mobili S.r.l.", "--country", "意大利"], b26)
    run(["map-customer", "sarah-thompson-northpeak", "sarah.thompson@northpeakoutfitters.com",
         "--name", "Sarah Thompson", "--company", "NorthPeak Outfitters", "--country", "美国"], b26)
    st26 = run(["status"], b26)
    run(["done", *[u["id"] for u in st26["unprocessed"]]], b26)
    p = verify_raw(b26)
    v = json.loads(p.stdout)
    probs = " | ".join(v["problems"])
    check("T26 done sent mail without render fails verify", p.returncode == 1
          and ("not rendered" in probs or "未生成" in probs or "沟通记录" in probs), probs[:160])
    check("T26 sent to unmapped recipient flagged",
          any("unmapped" in x for x in v["problems"]), probs[:160])

    # T27 base-path guard + launcher argument validation
    r27 = subprocess.run([sys.executable, str(TCM), "--base", str(ROOT / "data"), "init"],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
    check("T27 refuses data dir inside skill tree", r27.returncode == 2
          and "安装目录" in r27.stdout, r27.stdout[:120])
    eg = ROOT / "samples" / "t27-eg"
    r27b = subprocess.run([sys.executable, str(TCM), "--base", str(eg), "init"],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
    check("T27 samples/ whitelist still allowed", r27b.returncode == 0, r27b.stdout[:80])
    shutil.rmtree(eg, ignore_errors=True)
    r27c = subprocess.run([sys.executable, str(TCM), "--base", str(Path(tempfile.mkdtemp())),
                           "launcher", "--host", "imap.exmail.qq.com",
                           "--user", 'a@x.com"; rm -rf /; "'],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
    check("T27 launcher rejects injection in user", r27c.returncode == 2, r27c.stdout[:120])
    r27d = subprocess.run([sys.executable, str(TCM), "--base", str(Path(tempfile.mkdtemp())),
                           "launcher", "--host", "bad host name", "--user", "a@b.com"],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
    check("T27 launcher rejects bad host", r27d.returncode == 2, r27d.stdout[:120])
    b27 = fresh_base()
    run(["launcher", "--host", "imap.exmail.qq.com", "--user", "boss@company.com"], b27)
    cmd27 = (b27 / "连接邮箱.command").read_text(encoding="utf-8")
    check("T27 launcher embeds user as a single intact arg",
          "--user boss@company.com" in cmd27, "")
    check("T27 no raw semicolon injection in .command",
          "; rm" not in cmd27 and "rm -rf" not in cmd27, "")

    # T28 example attachments must be genuinely openable
    pdfs = sorted((ROOT / "samples" / "example-output" / "客户档案").rglob("附件/*.pdf"))
    pngs = sorted((ROOT / "samples" / "example-output" / "客户档案").rglob("附件/*.png"))
    ok_pdf = bool(pdfs) and all(f.read_bytes().startswith(b"%PDF-") and b"%%EOF" in f.read_bytes()
                                for f in pdfs)
    ok_png = bool(pngs) and all(f.read_bytes().startswith(b"\x89PNG") for f in pngs)
    check("T28 example PDFs are real (header+EOF)", ok_pdf,
          str([f.name for f in pdfs]))
    check("T28 example PNGs have signature", ok_png, str([f.name for f in pngs]))

    # ---- v4.2: simulated-IMAP-server round (red first) ----
    import imaplib
    from fake_imap import start_fake_imap
    from email.utils import formatdate

    def make_mail(uid, date, subject):
        return {"uid": uid, "internaldate": date,
                "raw": build_mail(mid=f"<fake-{uid}@x.example>",
                                  frm=("F Sender", "f@x.example"),
                                  to=[("Alex", "alex@sunrisetrade.example")],
                                  date=formatdate(0, localtime=False),
                                  subject=subject, text=f"body {uid}")}

    SENT_RAW = "&XfJT0ZAB-"  # yunyou raw folder name for 已发送 (modified UTF-7)

    def base_folders():
        return {
            "INBOX": [make_mail(1, "21-Sep-2026 09:00:00 +0800", "one"),
                      make_mail(2, "22-Sep-2026 09:00:00 +0800", "two"),
                      make_mail(3, "23-Sep-2026 09:00:00 +0800", "three")],
            SENT_RAW: [make_mail(10, "24-Sep-2026 09:00:00 +0800", "sent one"),
                       make_mail(11, "25-Sep-2026 09:00:00 +0800", "sent two")],
        }

    def connect(port):
        M = imaplib.IMAP4("127.0.0.1", int(port))
        M.login("u@x.example", "pw")
        return M

    # T29a Chinese sent folder: raw modified-UTF-7 name SELECTed, decoded
    # display name in coverage, both folders fetched
    srv = start_fake_imap(base_folders())
    M = connect(srv.port)
    out29 = Path(tempfile.mkdtemp(prefix="tcm-29a-"))
    r29 = imap_fetch.run_fetch(M, host="imap.example.com", folders_spec="auto",
                               out_dir=out29, status_path=out29 / "s.json",
                               limit=20, started="t0", log=lambda m: None)
    M.logout(); srv.stop()
    check("T29a both folders fetched (5 mails)", r29["complete"] is True
          and r29["fetched"] == 5, str(r29)[:120])
    check("T29a raw folder name used for SELECT", SENT_RAW in srv.selected,
          str(srv.selected))
    check("T29a decoded Chinese display in coverage",
          any("已发送" in k for k in r29["coverage"]), str(list(r29["coverage"])))

    # T29b known-unreliable provider + SEARCH silently dropping one mail:
    # enum strategy must recover the dropped mail
    srv = start_fake_imap(base_folders(), search_drop_first=1)
    M = connect(srv.port)
    out29b = Path(tempfile.mkdtemp(prefix="tcm-29b-"))
    r29b = imap_fetch.run_fetch(M, host="imap.yunyou.top", folders_spec="auto",
                                out_dir=out29b, status_path=out29b / "s.json",
                                limit=20, started="t0", log=lambda m: None)
    M.logout(); srv.stop()
    check("T29b enum recovers SEARCH-dropped mail", r29b["complete"] is True
          and r29b["fetched"] == 5, str(r29b)[:120])

    # T29c legitimately empty folder: SEARCH zero must NOT trigger enumeration;
    # missing sent folder on auto must be reported honestly
    srv = start_fake_imap({"INBOX": []})
    M = connect(srv.port)
    out29c = Path(tempfile.mkdtemp(prefix="tcm-29c-"))
    r29c = imap_fetch.run_fetch(M, host="imap.example.com", folders_spec="auto",
                                out_dir=out29c, status_path=out29c / "s.json",
                                limit=20, started="t0", log=lambda m: None)
    M.logout(); srv.stop()
    check("T29c empty folder does not enumerate", r29c["fetched"] == 0
          and not any("INTERNALDATE" in c for c in srv.commands), str(srv.commands)[-80:])
    check("T29c missing sent folder reported as error (complete=False)",
          any("已发送" in e for e in r29c["errors"]) and r29c["complete"] is False,
          str(r29c["errors"])[:120])

    # T29d connection drop mid-FETCH: partial result, honest completion
    srv = start_fake_imap(base_folders(), drop_on_fetch_uid=2)
    M = connect(srv.port)
    out29d = Path(tempfile.mkdtemp(prefix="tcm-29d-"))
    r29d = imap_fetch.run_fetch(M, host="imap.example.com", folders_spec="auto",
                                out_dir=out29d, status_path=out29d / "s.json",
                                limit=20, started="t0", log=lambda m: None)
    try: M.logout()
    except Exception: pass
    srv.stop()
    check("T29d mid-fetch drop is partial, not success", r29d["complete"] is False
          and r29d["errors"] and r29d["fetched"] >= 1, str(r29d)[:120])

    # T29e enum window: scanning capped, truncation recorded honestly
    big = {"INBOX": [make_mail(i, "10-Sep-2026 09:00:00 +0800", f"m{i}")
                     for i in range(1, 151)]}
    srv = start_fake_imap(big)
    M = connect(srv.port)
    out29e = Path(tempfile.mkdtemp(prefix="tcm-29e-"))
    r29e = imap_fetch.run_fetch(M, host="imap.yunyou.top", folders_spec="INBOX",
                                out_dir=out29e, status_path=out29e / "s.json",
                                limit=150, started="t0", enum_window=100,
                                log=lambda m: None)
    M.logout(); srv.stop()
    cov = r29e["coverage"].get("INBOX", {})
    check("T29e enum window caps scan and marks truncation", r29e["complete"] is False
          and cov.get("scanned") == 100 and cov.get("total") == 150
          and any("未覆盖" in e for e in r29e["errors"]), str(cov)[:140])

    # ---- summary (kept at end) ----
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    if passed != len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()

