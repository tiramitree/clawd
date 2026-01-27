#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lightweight urgent email watcher.

Notes:
- Gmail search query does NOT reliably support minutes in newer_than (e.g. 30m),
  so we use newer_than:1h and de-dup via a local state file.
- Output rules:
  - If no alerts: print exactly NO_ALERTS
  - If alerts: print a compact readable alert block (no timestamps)
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ACCOUNT = os.environ.get("GOG_ACCOUNT", "89479100+tiramitree@users.noreply.github.com")
STATE = Path(os.environ.get("GMAIL_WATCH_STATE", "/home/tiramitree/.clawdbot/gmail-watch-state.json"))

URGENT_FROM_DOMAINS = {
    "chase.com",
    "jpmorgan.com",
    "card.bilt.com",
    "biltrewards.com",
    "paypal.com",
    "bankofamerica.com",
    "ealerts.bankofamerica.com",
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


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or f"Command failed: {cmd}")
    return p.stdout


def gog_search(query, maxn=50):
    out = run(["gog", "gmail", "messages", "search", query, "--max", str(maxn), "--json", "--account", ACCOUNT])
    data = json.loads(out)
    msgs = data.get("messages")
    return msgs or []


def gog_get(message_id):
    out = run(["gog", "gmail", "get", message_id, "--json", "--account", ACCOUNT])
    return json.loads(out)


def from_domain(from_field: str) -> str:
    if not from_field:
        return ""
    m = re.search(r"@([A-Za-z0-9.-]+)", from_field.lower())
    return (m.group(1) if m else "")


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
    subj = (envelope.get("subject") or "")
    dom = from_domain(envelope.get("from") or "")
    score = 0
    if any(x in dom for x in ["-secure", "verify", "login", "support", "account", "update"]):
        score += 1
    if re.search(r"urgent|immediately|locked|suspend|disabled|unusual|verify|confirm|login", subj, re.I):
        score += 1
    if re.search(r"verify|confirm|password|code|login|unusual|suspend|locked", snippet or "", re.I):
        score += 1
    if dom and not any(dom.endswith(tld) for tld in [".com", ".org", ".edu", ".gov", ".net"]):
        score += 1
    return score >= 3


def load_state():
    if STATE.exists():
        try:
            data = json.loads(STATE.read_text())
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def save_state(st):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=2, sort_keys=True))


def main():
    st = load_state()
    seen_ids = st.get("seenIds") or []
    seen = set(seen_ids)

    # NOTE: minutes are unreliable; use 1h + dedupe.
    envelopes = gog_search("in:inbox newer_than:1h", maxn=80)

    alerts = []
    for e in envelopes:
        mid = e.get("id")
        if not mid or mid in seen:
            continue

        # record as seen early to avoid spam loops
        seen.add(mid)

        if not is_urgent(e):
            continue

        detail = None
        snippet = ""
        try:
            detail = gog_get(mid)
            snippet = clean_snippet(detail.get("message", {}).get("snippet", "") or detail.get("snippet", ""))
        except Exception:
            detail = None

        ph = "【疑似诈骗】" if is_phishy(e, snippet) else ""
        subj = e.get("subject", "")
        frm = e.get("from", "")

        line = f"• {ph}{subj} — {frm}".strip()
        if snippet:
            line += f"\n  内容：{snippet}"
        alerts.append(line)

    st["seenIds"] = list(seen)[-2000:]
    save_state(st)

    if alerts:
        print("【邮件提醒：可能需要立刻处理】")
        print("\n".join(alerts[:6]))
        print("提示：先用官方App/手动输入官网核对，别点邮件里的链接。")
    else:
        print("NO_ALERTS")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"邮件监控失败：{e}", file=sys.stderr)
        sys.exit(1)
