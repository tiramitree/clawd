#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gmail daily digest.

Goals (per user request):
- More readable
- Include concrete email content (snippet / key details)
- Remove noisy info like exact timestamps
"""

import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

ACCOUNT = os.environ.get("GOG_ACCOUNT", "").strip()

# --- Heuristics (tune as needed) ---
URGENT_FROM_DOMAINS = {
    "chase.com",
    "jpmorgan.com",
    "card.bilt.com",
    "biltrewards.com",
    "paypal.com",
    "bankofamerica.com",
    "ealerts.bankofamerica.com",
    "usps.com",
    "informeddelivery.usps.com",
    "irs.gov",
    "ssa.gov",
}

URGENT_SUBJECT_PATTERNS = [
    r"\btransaction\b",
    r"\balert\b",
    r"\bsecurity\b",
    r"\bverify\b",
    r"\bconfirm\b",
    r"\bdeactivated\b",
    r"\bpast due\b",
    r"\bpayment\b",
    r"\bcharge\b",
    r"\brefund\b",
    r"\bdispute\b",
    r"\bzelle\b",
]

PROMO_LABELS = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL"}
NOISE_SENDERS = {}


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or f"Command failed: {cmd}")
    return p.stdout


def gog_search(query, maxn=80):
    if not ACCOUNT:
        raise RuntimeError("GOG_ACCOUNT is required")
    out = run(["gog", "gmail", "messages", "search", query, "--max", str(maxn), "--json", "--account", ACCOUNT])
    data = json.loads(out)
    return data.get("messages", [])


def gog_get(message_id):
    out = run(["gog", "gmail", "get", message_id, "--json", "--account", ACCOUNT])
    return json.loads(out)


def from_addr(from_field: str) -> str:
    if not from_field:
        return ""
    m = re.search(r"<([^>]+)>", from_field)
    return (m.group(1) if m else from_field).strip()


def from_domain(from_field: str) -> str:
    addr = from_addr(from_field).lower()
    m = re.search(r"@([a-z0-9.-]+)", addr)
    return m.group(1) if m else ""


def clean_snippet(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def is_urgent(envelope):
    subj = (envelope.get("subject") or "")
    dom = from_domain(envelope.get("from") or "")
    if dom in URGENT_FROM_DOMAINS:
        return True
    for pat in URGENT_SUBJECT_PATTERNS:
        if re.search(pat, subj, re.I):
            return True
    return False


def is_phishy(envelope, snippet: str = ""):
    """Lightweight phishing suspicion.

    We *do not* open links. We just flag patterns for user attention.
    """
    subj = (envelope.get("subject") or "")
    frm = (envelope.get("from") or "")
    dom = from_domain(frm)
    score = 0

    # lookalike-ish domain tokens
    if any(x in dom for x in ["-secure", "verify", "login", "support", "account", "update"]):
        score += 1

    # subject urgency / lockout language
    if re.search(r"urgent|immediately|locked|suspend|disabled|unusual|verify|confirm|login", subj, re.I):
        score += 1

    # snippet similar language
    if re.search(r"verify|confirm|password|code|login|unusual|suspend|locked", snippet or "", re.I):
        score += 1

    # non-standard TLD is a small signal
    if dom and not any(dom.endswith(tld) for tld in [".com", ".org", ".edu", ".gov", ".net"]):
        score += 1

    return score >= 3


def extract_key_details(envelope, body_html: str) -> str:
    """Try to pull out useful fields from known alert emails (best effort)."""
    subj = (envelope.get("subject") or "")

    # Chase transaction alert often contains Merchant + Amount; we already have it in subject sometimes.
    if "You made a $" in subj and "transaction" in subj:
        return "重点：交易提醒（请确认是否本人消费）"

    if "deactivated" in subj.lower() and "bilt" in (envelope.get("from") or "").lower():
        return "重点：卡片将停用/切换（检查自动扣款/新卡激活）"

    if "echeck" in subj.lower() and "paypal" in (envelope.get("from") or "").lower():
        return "重点：付款已清算（通常无需操作，除非在等对方确认）"

    return ""


def classify(envelope):
    labels = set(envelope.get("labels") or [])
    if is_urgent(envelope):
        return "need_now"
    # unread but not promo -> important
    if "UNREAD" in labels and not (labels & PROMO_LABELS):
        return "important"
    return "normal"


def format_item(envelope, detail_json=None):
    frm = envelope.get("from") or ""
    subj = envelope.get("subject") or ""

    snippet = ""
    body_html = ""
    if detail_json:
        snippet = clean_snippet(detail_json.get("message", {}).get("snippet", "") or detail_json.get("snippet", ""))
        body_html = detail_json.get("body", "") or ""

    phish_flag = "【疑似诈骗】" if is_phishy(envelope, snippet) else ""

    key = extract_key_details(envelope, body_html)
    key_line = f"（{key}）" if key else ""

    # No timestamps; keep it compact.
    line1 = f"• {phish_flag}{subj} — {frm}{key_line}".strip()
    if snippet:
        line2 = f"  内容：{snippet}"
        return line1 + "\n" + line2
    return line1


def main():
    # 24h is usually enough for a morning digest
    envelopes = gog_search("in:inbox newer_than:1d", maxn=120)

    buckets = {"need_now": [], "important": [], "normal": []}
    for e in envelopes:
        buckets[classify(e)].append(e)

    # Fetch details for top items where content matters
    def with_details(items, limit):
        out = []
        for e in items[:limit]:
            try:
                out.append((e, gog_get(e["id"])))
            except Exception:
                out.append((e, None))
        return out

    need_now_d = with_details(buckets["need_now"], 10)
    important_d = with_details(buckets["important"], 10)

    # Normal: collapse noisy senders, show a small sample
    normal = buckets["normal"]
    noise_count = 0
    normal_filtered = []
    sender_counts = Counter()
    for e in normal:
        addr = from_addr(e.get("from") or "").lower()
        if addr in NOISE_SENDERS:
            noise_count += 1
            continue
        sender_counts[from_domain(e.get("from") or "") or (e.get("from") or "")] += 1
        normal_filtered.append(e)

    out_lines = []

    out_lines.append("【需要立刻处理】")
    if need_now_d:
        for e, d in need_now_d:
            out_lines.append(format_item(e, d))
    else:
        out_lines.append("• 无")

    out_lines.append("")
    out_lines.append("【重要】")
    if important_d:
        for e, d in important_d:
            out_lines.append(format_item(e, d))
    else:
        out_lines.append("• 无")

    out_lines.append("")
    out_lines.append("【正常】")

    # Summary line for normal (counts)
    if sender_counts:
        top = ", ".join([f"{k}×{v}" for k, v in sender_counts.most_common(5)])
        out_lines.append(f"• 概览：{top}" + (f"；另有 {noise_count} 条 BuildingLink/SoMA" if noise_count else ""))

    # Add a few example items (content-lite)
    sample = normal_filtered[:5]
    if sample:
        for e in sample:
            out_lines.append(f"• {e.get('subject','')} — {e.get('from','')}")
    else:
        if noise_count:
            out_lines.append(f"• 主要是 BuildingLink/SoMA 通知（{noise_count} 条）")
        else:
            out_lines.append("• 无")

    # Phishing safety footer (short)
    out_lines.append("")
    out_lines.append("提示：涉及付款/登录/验证码的邮件，一律先在官方App或手动输入官网核对，不点邮件里的链接。")

    print("\n".join(out_lines).strip())


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"邮件摘要失败：{e}", file=sys.stderr)
        sys.exit(1)
