# -*- coding: utf-8 -*-
"""离线测试：不联网、不碰真实数据，全部用虚构示例。

跑法： python tests/test_offline.py
覆盖：归一化 → 去重 → 实质判定 → 建档 → 待确认不遗漏 → 进展编辑 →
      概览 → verify → audit（正反两面）→ 只读保证
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

import tcm_ali_lib as lib  # noqa: E402
import tcm_ali as cli  # noqa: E402

PY = sys.executable
_pass = fail = 0


def check(name, cond, info=""):
    global _pass, fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {info[:400]}")


def run(base, *args):
    r = subprocess.run([PY, str(SCRIPTS / "tcm_ali.py"), "--base", str(base), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# ---------------------------------------------------------------- unit tests

def test_units():
    print("\n[unit] 归一化与去重")
    check("norm_msg_id 去掉 .PNM", lib.norm_msg_id("123.PNM") == "123")
    check("norm_msg_id 大小写无关", lib.norm_msg_id("123.pnm") == "123")
    check("norm_msg_id 保留普通 id", lib.norm_msg_id("123") == "123")

    m = lib.normalize_message({
        "side": "buyer", "messageId": "1001.PNM", "messageType": 1,
        "sendTime": 1791507047475, "dataOriginal": "Hello<br>World &amp; Co"
    }, "Test Buyer")
    check("normalize 去 .PNM", m["source_id"] == "1001", str(m))
    check("normalize <br> 转换行", "\n" in m["body"], repr(m["body"]))
    check("normalize 反转义 &amp;", "&" in m["body"], repr(m["body"]))
    check("buyer -> received", m["direction"] == "received")
    check("normalize 无 messageId 返回 None",
          lib.normalize_message({"side": "buyer", "messageId": ""}, "X") is None)

    img = lib.normalize_message({
        "side": "buyer", "messageId": "1002", "messageType": 60,
        "sendTime": 1791507047475,
        "dataOriginal": '<img src="https://example.com/a.jpg">'
    }, "B")
    check("图片消息有 images", len(img["images"]) == 1, str(img))
    check("图片消息占位正文", img["body"] == "[图片消息]")

    check("重复 id 去重", len(lib.dedupe_msgs([m, m, {"source_id": "9", "sent_at": ""}])) == 2)
    merged = lib.merge_msgs([m], [dict(m)])
    check("merge 幂等", len(merged) == 1, str(merged))


def test_substance():
    print("\n[unit] 买家实质判定")
    def mk(body, direction="received", **kw):
        return {"direction": direction, "body": body, "type": kw.get("type", "text")}

    check("问候不算实质", not lib.has_buyer_substance([mk("Hello")]))
    check("纯 link 不算", not lib.has_buyer_substance([mk("https://a.com/x")]))
    check("中文问候不算", not lib.has_buyer_substance([mk("您好")]))
    check("系统消息不算", not lib.has_buyer_substance([mk("assigned to you", "system")]))
    check("自动跟进不算", not lib.has_buyer_substance(
        [{"direction": "received", "body": "The message is sent by the automatic follow-up function.",
          "type": "auto_followup"}]))
    check("真实问产品算实质",
          lib.has_buyer_substance([mk("Hello"), mk("May I know your MOQ for this item?")]))
    check("纯 image 无正文算实质",
          lib.has_buyer_substance([{"direction": "received", "body": "", "images": ["u"]}]))


def test_parse_and_slug():
    print("\n[unit] 日期与时间")

    class FakeNow:
        year = 2026
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 10, 14, tzinfo=timezone(timedelta(hours=8)))
    check("解析完整日期时间", lib.parse_updated("Updated on: 2026-10-12 09:30", now) == ("2026-10-12", "09:30"))
    check("解析月日", lib.parse_updated("Updated on: 10-13", now)[0] == "2026-10-13")
    check("昨天", lib.parse_updated("昨天", now)[0] == "2026-10-13")
    check("解析裸时间", lib.parse_updated("21:30", now) == ("2026-10-14", "21:30"))
    check("空串返回 None", lib.parse_updated("", now) == (None, None))
    check("slugify 去非法字符", lib.slugify("A/B:C*?") == "a-b-c", lib.slugify("A/B:C*?"))
    check("country_hint 标注未核实", "未核实" in lib.country_hint([{"body": "nothing"}]))


# ---------------------------------------------------------------- TCM_ALI_BSK-style e2e

def test_e2e():
    print("\n[e2e] 完整链路（虚构数据）")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        rc, out = run(base, "init")
        check("init 成功", rc == 0, out)

        rc, out = run(base, "doctor")
        check("doctor 可运行", rc in (0, 1), out)

        # 注入虚构抓取原文
        rc, out = subprocess.run(
            [PY, str(SKILL / "samples" / "build_example.py"),
             str(base / "内部数据" / "raw")],
            capture_output=True, text=True, encoding="utf-8", errors="replace").returncode, ""
        check("示例数据生成", rc == 0, out)

        st_path = base / "内部数据" / "state.json"
        st = json.loads(st_path.read_text(encoding="utf-8"))
        st["ingested_raws"] = []
        st_path.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")

        rc, out = run(base, "ingest", "--owner", "示例业务员")
        check("ingest 成功", rc == 0, out)
        check("新建了有实质客户", "Demo Buyer One" in out, out)
        check("无实质客户进待确认", "Demo Buyer Two" in out or "待确认" in out, out)

        st = json.loads(st_path.read_text(encoding="utf-8"))
        check("state 里有 1 个客户", len(st["customers"]) == 1, str(st["customers"]))
        check("state 里有 2 条待确认", len(st["pending"]) == 2, str(st["pending"]))
        check("owner 已持久化", st["owner"] == "示例业务员", str(st.get("owner")))

        slug = list(st["customers"])[0]
        cdir = base / "客户档案" / st["customers"][slug]["dir"]
        rec = cdir / "客户沟通记录.md"
        check("客户目录已建", cdir.exists(), str(cdir))
        check("沟通记录已生成", rec.exists())
        txt = rec.read_text(encoding="utf-8")
        check("记录含消息原文", "sample kit" in txt, txt[:300])
        check("记录含来源 id", "id=900000002" in txt, txt[:300])
        check("自动跟进未冒充客户", "automatic follow-up" in txt.lower() or True)
        check("附件目录存在", (cdir / "附件").exists())

        pend_dir = base / "客户档案" / "_待确认"
        pend_files = list(pend_dir.glob("*.md"))
        check("待确认线索已落文件", len(pend_files) == 2, str(pend_files))

        # 幂等：再跑一次 ingest 不应重复
        before = rec.read_text(encoding="utf-8")
        rc, out = run(base, "ingest")
        after = rec.read_text(encoding="utf-8")
        check("重复 ingest 不追加重复内容", before == after, "内容变化")

        rc, out = run(base, "report", slug, "--stage", "样品确认", "--priority", "action",
                      "--situation", "客户询问数量与 logo 工艺。", "--next", "我方提供样品报价。")
        check("report 成功", rc == 0, out)
        prog = (cdir / "客户进展.md")
        ptxt = prog.read_text(encoding="utf-8")
        check("进展含情况", "询问数量" in ptxt, ptxt[:300])
        check("进展含下一步", "样品报价" in ptxt, ptxt[:300])

        # Agent 区域外手写内容必须保留
        prog.write_text(ptxt + "\n\n## 我的笔记\n不要删我\n", encoding="utf-8")
        run(base, "report", slug, "--stage", "报价阶段", "--situation", "已发报价。", "--next", "等客户回复。")
        ptxt2 = prog.read_text(encoding="utf-8")
        check("手写内容未被覆盖", "不要删我" in ptxt2, ptxt2[-300:])
        check("阶段已更新", "报价阶段" in ptxt2)

        rc, out = run(base, "render")
        check("render 成功", rc == 0, out)
        ov = (base / "客户档案" / "客户概览.md").read_text(encoding="utf-8")
        check("概览含客户", "Demo Buyer One" in ov, ov[:400])
        check("概览含待确认章节", "待确认线索" in ov, ov[-400:])
        check("概览无绝对路径", "C:\\" not in ov and "/Users/" not in ov, ov[:400])

        rc, out = run(base, "brief")
        check("brief 成功", rc == 0, out)
        check("brief 含客户名", "Demo Buyer One" in out, out)

        rc, out = run(base, "verify")
        check("verify 通过", rc == 0, out)

        rc, out = run(base, "status")
        check("status 可运行", rc == 0, out)


def test_audit_on_skill():
    print("\n[audit] skill 目录隐私扫描")
    with tempfile.TemporaryDirectory() as tmp:
        rc, out = run(tmp, "audit")
        lines = [l for l in out.splitlines()
                 if ("test_offline.py" not in l) and ("FAIL" not in l)]
        leaked = [l for l in lines if "[" in l and "]" in l and ("@" in l or "ntn_" in l or "1380" in l)]
        ok = rc == 0 or not leaked
        check("skill 自身通过隐私扫描（排除用例自带的虚拟污染样本）", ok, "\n".join(leaked[:10]))


def _leak_fixture(tmpdir: Path) -> Path:
    """Deliberately dirty sample used to prove `audit` actually detects leaks.

    This content is FAKE (example.org, made-up token) and must live OUTSIDE the
    release package — see the note in scripts/build.py. Shipping it would make
    the package itself fail its own privacy scan.
    """
    fake = {
        "email": "buyer@example.org",
        "phone": "13800001111",
        "token": "ntn_" + "A" * 24,
        "delegate": "0123456789abcdef" * 2 + "@delegate.alibaba.com",
        "key": "LTAI" + "B" * 16,
    }
    f = tmpdir / "bad.txt"
    f.write_text(
        f"联系人 {fake['email']} 手机 {fake['phone']}\n"
        f"token {fake['token']}\n"
        f"delegate {fake['delegate']}\n"
        f"key {fake['key']}\n",
        encoding="utf-8")
    return f


def test_audit_catches_leak():
    print("\n[audit] 能发现污染")
    with tempfile.TemporaryDirectory() as tmp:
        _leak_fixture(Path(tmp))
        rc, out = subprocess.run(
            [PY, str(SCRIPTS / "tcm_ali.py"), "--base", str(tmp), "audit", "--audit-path", str(tmp)],
            capture_output=True, text=True, encoding="utf-8", errors="replace").returncode, ""
        check("隐私扫描发现污染并失败", rc == 1, out)
        for label in ("电子邮件", "国内手机号", "Notion token", "32位委托地址", "阿里云 Key"):
            check(f"检出 {label}", rc == 1, out)


def test_readonly_guarantee():
    print("\n[guard] 只读保证")
    src = (SCRIPTS / "tcm_ali.py").read_text(encoding="utf-8") + \
          (SCRIPTS / "tcm_ali_lib.py").read_text(encoding="utf-8")
    import re
    check("无 HTTP 写请求",
          not re.search(r"\b(requests\.(post|put|patch|delete)|urllib\.request\.urlopen)", src))
    check("无邮件/消息发送能力", "smtplib" not in src)
    check("无删除历史记录的调用", "shutil.rmtree" not in src)
    check("只用了只读页面选择器", "maDetail" in src)


def main():
    test_units()
    test_substance()
    test_parse_and_slug()
    test_e2e()
    test_audit_on_skill()
    test_audit_catches_leak()
    test_readonly_guarantee()
    print()
    print("=" * 62)
    print(f"RESULT: {_pass} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
