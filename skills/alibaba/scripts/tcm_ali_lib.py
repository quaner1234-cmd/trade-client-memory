#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic helpers for the Alibaba inquiry skill.

Ported from the production nightly sync (verified live since 2026-10-13) —
pure functions only: normalisation, dedupe, and the buyer-substance gate.
No network, no IO, no business data.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))

ALI_TYPE = {
    1: "text",
    50: "business_card",
    60: "image",
    9545: "auto_followup",
    9999: "system",
}

# Transport suffix observed live: the same logical message arrives as
# "1234567890123" on one run and "1234567890123.PNM" on another (ID anonymised).
RE_PNM = re.compile(r"\.pnm$", re.I)

RE_BR = re.compile(r"<br\s*/?>", re.I)
RE_TAG = re.compile(r"<[^>]+>")
RE_IMG_SRC = re.compile(r'<img[^>]*src="([^"]+)"', re.I)
RE_URL_ONLY = re.compile(r"^\s*(https?://\S+\s*)+$", re.I)
RE_COUNTRY_CARD = re.compile(r"[?&]country=([A-Za-z]{2})\b")

_GREETINGS = (
    r"hi", r"hello", r"hey", r"good (morning|afternoon|evening)",
    r"привет", r"здравствуйте", r"добрый (день|вечер)",
    r"came across your", r"你好", r"您好", r"在吗",
    r"hallo", r"bonjour", r"hola", r"ciao", r"olá", r"salam",
)
RE_GREETING = re.compile(
    r"^\s*(?:" + "|".join(_GREETINGS) + r")[\s!.,。！？?]*$", re.I)


def norm_msg_id(mid) -> str:
    """Stable key for one logical message ('X' and 'X.PNM' collapse to 'X')."""
    return RE_PNM.sub("", str(mid or "")).strip()


def iso_from_epoch_ms(ms) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=CST).isoformat()
    except Exception:
        return None


def clean_body(raw: str) -> str:
    """Platform message bodies are HTML fragments: <br> then strip tags."""
    t = RE_BR.sub("\n", raw or "")
    t = RE_TAG.sub("", t)
    return html.unescape(t).strip()


def extract_images(raw: str) -> list[str]:
    out = []
    for m in RE_IMG_SRC.finditer(raw or ""):
        u = html.unescape(m.group(1))
        if u not in out:
            out.append(u)
    return out


def normalize_message(item: dict, buyer_label: str, owner: str = "我方") -> dict | None:
    """One raw .message-item-wrapper dict -> canonical dict. None if unusable."""
    mid = item.get("messageId")
    if not mid:
        return None
    side = (item.get("side") or "").lower()
    direction = {"buyer": "received", "seller": "sent"}.get(side, "system")
    mtype = item.get("messageType")
    try:
        mtype_i = int(mtype) if mtype is not None else None
    except (TypeError, ValueError):
        mtype_i = None
    raw = item.get("dataOriginal") or ""
    imgs = extract_images(raw)
    if mtype_i == 60:
        body = "[图片消息]"
    elif mtype_i == 50:
        body = "[名片/询盘卡片]"
    else:
        body = clean_body(raw)
        if not body and imgs:
            body = "[图片消息]"
    if direction == "received":
        sender = buyer_label
    elif direction == "sent":
        sender = owner
    else:
        sender = "系统"
    mtype_name = "unknown"
    if mtype_i is not None:
        mtype_name = ALI_TYPE.get(mtype_i, str(mtype_i))
    return {
        "source_id": norm_msg_id(mid),
        "sent_at": iso_from_epoch_ms(item.get("sendTime")),
        "direction": direction,
        "sender": sender,
        "body": body,
        "images": imgs,
        "type": mtype_name,
    }


def is_auto_followup(m: dict) -> bool:
    return m.get("type") == "auto_followup" or "automatic follow-up" in (m.get("body") or "").lower()


def has_buyer_substance(msgs) -> bool:
    """True when the visible window shows genuine buyer interest.

    Conservative by design: greetings, bare links/cards and fragments do NOT
    count, so an ordinary conversation is never auto-promoted to a valid
    inquiry. A buyer image does count.
    """
    for m in msgs:
        if m.get("direction") != "received" or is_auto_followup(m):
            continue
        body = (m.get("body") or "").strip()
        if not body:
            if m.get("images"):
                return True
            continue
        if RE_URL_ONLY.match(body) or RE_GREETING.match(body):
            continue
        if len(body) < 6 or sum(ch.isalpha() for ch in body) < 3:
            continue
        return True
    return False


def country_hint(msgs) -> str:
    """Country from a platform trading card, marked unverified (never a fact)."""
    for m in msgs:
        c = RE_COUNTRY_CARD.search(m.get("body") or "")
        if c:
            return f"{c.group(1).upper()}（平台字段，未核实）"
    return "未核实"


def dir_counts(msgs) -> tuple[int, int, int]:
    return (
        sum(1 for m in msgs if m.get("direction") == "received"),
        sum(1 for m in msgs if m.get("direction") == "sent"),
        sum(1 for m in msgs if m.get("direction") == "system"),
    )


def dedupe_msgs(msgs) -> list[dict]:
    """Drop repeats by normalised id, keep stable order ascending by time."""
    seen, out = set(), []
    for m in msgs:
        k = norm_msg_id(m.get("source_id"))
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(m)
    out.sort(key=lambda m: (m.get("sent_at") or "", m.get("source_id") or ""))
    return out


def merge_msgs(old, new) -> list[dict]:
    """Union of already-archived and freshly extracted, idempotent."""
    return dedupe_msgs(list(old or []) + list(new or []))


def parse_updated(s: str, now=None) -> tuple[str | None, str | None]:
    """Parse the platform's 'Updated on: ...' label -> (date, time)."""
    now = now or datetime.now(CST)
    s = (s or "").strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{2}):(\d{2}))?", s)
    if m:
        d = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        return d, (f"{m.group(4)}:{m.group(5)}" if m.group(4) else None)
    m = re.search(r"(\d{2})-(\d{2})(?:\s+(\d{2}):(\d{2}))?", s)
    if m:
        d = f"{now.year}-{m.group(1)}-{m.group(2)}"
        return d, (f"{m.group(3)}:{m.group(4)}" if m.group(3) else None)
    if "昨天" in s or "yesterday" in s.lower():
        return (now - timedelta(days=1)).strftime("%Y-%m-%d"), None
    m = re.search(r"(\d{2}):(\d{2})", s)
    if m:
        return now.strftime("%Y-%m-%d"), f"{m.group(1)}:{m.group(2)}"
    return None, None


def slugify(name: str) -> str:
    """Filesystem-safe, stable internal id derived from the display name."""
    s = re.sub(r"[\\/:*?\"<>|]+", " ", (name or "").strip())
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-.")
    return (s or "unnamed").lower()


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
