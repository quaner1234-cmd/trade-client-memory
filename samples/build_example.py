#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regenerate samples/example-output/客户档案 (the committed fictional demo).

Only writes inside samples/ (the whitelisted zone); 内部数据/ stays out of
git via .gitignore. Run from the repo root:
    python samples/build_example.py
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from demo_corpus import build_extended                      # noqa: E402
from mailbuilder import build_mail, minimal_pdf             # noqa: E402

BASE = ROOT / "samples" / "example-output"
ALEX = ("Alex", "alex@sunrisetrade.example")


def tcm(*args: str) -> dict:
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "tcm.py"),
                        "--base", str(BASE), *args],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else {}


def main() -> None:
    shutil.rmtree(BASE, ignore_errors=True)
    tcm("init")
    inbox = BASE / "内部数据" / "inbox"
    for name in build_extended(inbox, ROOT / "tests" / "fixtures" / "assets"):
        if name == "ext-07-hans-photo.eml":  # two-customer example: no Hans
            (inbox / name).unlink()
    def sarah(mid, fname, dt, subject):
        return build_mail(mid=mid, frm=("Sarah Thompson", "sarah.thompson@northpeakoutfitters.com"),
                          to=[ALEX], date=dt, subject=subject, text="Specs attached.",
                          attachments=[(fname, minimal_pdf("NP-104 spec sheet v1 - fictional"),
                                        "application/pdf")] if fname else None)
    (inbox / "ext-08-sarah-a.eml").write_bytes(sarah(
        "<sarah-x1@northpeakoutfitters.com>", "spec-sheet.pdf",
        "Thu, 24 Sep 2026 10:00:00 -0500", "Specs v1"))
    (inbox / "ext-09-sarah-b.eml").write_bytes(sarah(
        "<sarah-x2@northpeakoutfitters.com>", "spec-sheet-final.pdf",
        "Fri, 25 Sep 2026 10:00:00 -0500", "Specs v1 again"))
    print("ingest:", tcm("ingest", "--owner", "alex@sunrisetrade.example")["new"])
    tcm("map-customer", "marco-rossi-casa-rossi-mobili", "marco.rossi@casarossi.it",
        "--name", "Marco Rossi", "--company", "Casa Rossi Mobili S.r.l.", "--country", "意大利")
    tcm("map-customer", "sarah-thompson-northpeak", "sarah.thompson@northpeakoutfitters.com",
        "--name", "Sarah Thompson", "--company", "NorthPeak Outfitters", "--country", "美国")
    st = tcm("status")
    tcm("done", *[u["id"] for u in st["unprocessed"]])
    tcm("render")

    comm_ids = {d.name: sorted(set(re.findall(r"msg-([0-9a-f]{12})",
                (d / "客户沟通记录.md").read_text(encoding="utf-8"))))
                for d in (BASE / "客户档案").iterdir() if d.is_dir()}
    mi = comm_ids["Casa Rossi Mobili S.r.l. - 意大利"]
    si = comm_ids["NorthPeak Outfitters - 美国"]
    sections = {
        "Casa Rossi Mobili S.r.l. - 意大利": f"""## 客户情况

意大利米兰家具零售连锁,采购经理 Marco Rossi,主动询盘(广交会来源待核实)。

## 关键已确认事实

- 询价核桃木餐桌约 200 件 [msg-{mi[1] if len(mi) > 1 else mi[0]}]
- 我方报价 USD 245/pc FOB Qingdao(9-23),客户随后逐项还价 USD 230 @ 300 件
- 客户每封邮件都带同一签名 Logo(已自动识别存档);真实附件:price-list、catalog

## 当前阶段

还价等待我方答复(客户 9-26 已逐项给出接受条件)

## 下一行动

- [ ] 业务员:答复还价(USD 230 @ 300 件)并安排样品
""",
        "NorthPeak Outfitters - 美国": f"""## 客户情况

美国丹佛户外用品商,联系人 Sarah Thompson;规格表已发来,我方已确认收到。

## 关键已确认事实

- NP-104 规格表已收到(同一文件两个文件名,已按内容去重为一份)[msg-{si[-1] if si else '000000000000'}]
- 我方已回复确认收到(已发=已行动)[msg-{si[0] if si else '000000000000'}]

## 当前阶段

复购准备中:等待我方发出正式报价

## 下一行动

- [ ] 业务员:发出 NP-104 500 件正式报价
""",
    }
    for folder, content in sections.items():
        p = BASE / "客户档案" / folder / "客户沟通记录.md"
        t = p.read_text(encoding="utf-8")
        t = re.sub(r"(<!-- TCM:AGENT:BEGIN -->\n).*?(\n<!-- TCM:AGENT:END -->)",
                   lambda m: m.group(1) + content + m.group(2), t, flags=re.S)
        p.write_text(t, encoding="utf-8")
    tcm("report", "marco-rossi-casa-rossi-mobili", "--stage", "还价",
        "--situation", "客户还价 USD 230 @ 300 件并逐项给出接受条件,等待我方答复。",
        "--next", "业务员需要:答复还价并安排样品;等待客户:确认来访日期。",
        "--priority", "urgent")
    tcm("report", "sarah-thompson-northpeak", "--stage", "复购准备中",
        "--situation", "规格表已收到并确认;我方尚未发出正式报价。",
        "--next", "业务员需要:发出 NP-104 500 件正式报价。",
        "--priority", "action")
    tcm("render")
    v = subprocess.run([sys.executable, str(ROOT / "scripts" / "tcm.py"),
                        "--base", str(BASE), "verify"],
                       capture_output=True, text=True, encoding="utf-8")
    print("verify:", v.stdout.strip())
    print("example rebuilt at", BASE / "客户档案")


if __name__ == "__main__":
    main()
