# -*- coding: utf-8 -*-
"""虚构示例原始数据（no real customer data）。

演示阿里卖家后台抓取下来的 raw 格式。以下**全部为编造内容**：
客户名、会话号、消息 ID、聊天正文均与任何真实客户、真实会话无关。

覆盖三种典型情形，供测试与演示使用：
  1. 有实质询盘（产品 + 数量 + 我方回复）→ 应自动建档
  2. 只有问候（无实质）→ 应落「待确认」
  3. 只有系统分配（无实质）→ 应落「待确认」
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 14, 10, 0, tzinfo=CST)

# 全部编造的会话号（9 位，与阿里真实号段无关）
IID_1, IID_2, IID_3 = "900000001", "900000002", "900000003"

LIST = [
    {
        "inquiry_id": IID_1,
        "name": "Demo Buyer One",
        "last_msg": "What is your price for the sample kit?",
        "updated": "Updated on: 2026-10-12 09:30",
        "href": f"/message/maDetail.htm?imInquiryId={IID_1}",
    },
    {
        "inquiry_id": IID_2,
        "name": "Demo Buyer Two",
        "last_msg": "Hello",
        "updated": "Updated on: 2026-10-11 21:05",
        "href": f"/message/maDetail.htm?imInquiryId={IID_2}",
    },
    {
        "inquiry_id": IID_3,
        "name": "Demo Buyer Three",
        "last_msg": "",
        "updated": "Updated on: 2026-10-10 14:20",
        "href": f"/message/maDetail.htm?imInquiryId={IID_3}",
    },
]


def ms(dt):
    return int(dt.timestamp() * 1000)


def text_item(mid, dt, side, body, mtype=1):
    return {
        "side": side,
        "messageId": str(mid),
        "messageType": mtype,
        "sendTime": ms(dt),
        "dataOriginal": body,
    }


# 情形 1：有实质询盘。产品与话术均为编造的通用内容，不取自任何真实会话。
CONV_1_ITEMS = [
    text_item(900000001, NOW - timedelta(days=2, hours=3), "system",
              "The buyer has been assigned to you."),
    text_item(900000002, NOW - timedelta(days=2, hours=2), "buyer",
              "Hi, I would like to know more about your sample kit for testing."),
    text_item(900000003, NOW - timedelta(days=2, hours=1), "seller",
              "Thanks for reaching out. Could you tell us the quantity you need?"),
    text_item(900000004, NOW - timedelta(days=1, hours=5), "buyer",
              "About 200 units for a first trial. Can you print our brand name on it?"),
    {
        "side": "seller",
        "messageId": "900000005",
        "messageType": 9545,
        "sendTime": ms(NOW - timedelta(days=1, hours=4)),
        "dataOriginal": "The message is sent by the automatic follow-up function.",
    },
]

# 情形 2：只有问候（无实质）→ 应落到待确认
CONV_2_ITEMS = [
    text_item(910000001, NOW - timedelta(days=3), "system",
              "The inquiry has been transferred to you."),
    text_item(910000002, NOW - timedelta(days=3, minutes=50), "buyer", "Hello"),
    text_item(910000003, NOW - timedelta(days=3, minutes=40), "seller",
              "Hi, how can I help you?"),
]

# 情形 3：只有系统分配（无实质）→ 应落到待确认
CONV_3_ITEMS = [
    text_item(920000001, NOW - timedelta(days=4), "system",
              "The buyer has been assigned to you."),
]


def main(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "list-20261014-100000.json").write_text(
        json.dumps(LIST, ensure_ascii=False, indent=1), encoding="utf-8")
    convs = [
        {"inquiry_id": IID_1, "name": "Demo Buyer One",
         "updated": "Updated on: 2026-10-12 09:30", "items": CONV_1_ITEMS},
        {"inquiry_id": IID_2, "name": "Demo Buyer Two",
         "updated": "Updated on: 2026-10-11 21:05", "items": CONV_2_ITEMS},
        {"inquiry_id": IID_3, "name": "Demo Buyer Three",
         "updated": "Updated on: 2026-10-10 14:20", "items": CONV_3_ITEMS},
    ]
    for c in convs:
        (out / f"conv-{c['inquiry_id']}-20261014-100000.json").write_text(
            json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已生成示例数据到 {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "内部数据/raw")
