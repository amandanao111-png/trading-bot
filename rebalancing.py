#!/usr/bin/env python3
"""
rebalancing.py — weekly sector rebalance report.

Restored 2026-10-25. The weekly-rebalancing cron referenced this file but it was
missing from disk (never committed in git), so the cron could only ever fail.

Reads live Alpaca positions, groups gross exposure by sector, compares against
the target allocation, and writes rebalancing_report.json in the exact schema
consumers already expect:

    {"date":..., "portfolio_value":..., "current_allocation":{...},
     "target_allocation":{...}, "suggestions":[{"action","sector","reason","suggestion"}]}

IMPORTANT (bug this fixes): a report written with an EMPTY current_allocation
makes every sector read "underweight by full target" -> 6 phantom BUY
suggestions plus a fake "BUY signals sitting idle" action item in
bot_status_digest.py. So:
  * current_allocation is always populated from live positions
  * if the positions fetch fails, exit non-zero WITHOUT overwriting the report
  * a minimum deviation threshold suppresses noise
  * sectors with no target are reported as uncovered rather than silently
    folded into a targeted sector

Paper account (BASE_URL = paper-api.alpaca.markets), same as auto_trade.py.
"""

import json
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(WORKSPACE, ".env"))

import requests  # noqa: E402

REPORT_PATH = os.path.join(WORKSPACE, "rebalancing_report.json")
BASE_URL = os.environ.get("ALPACA_API_URL", "https://paper-api.alpaca.markets")
API_KEY = os.environ.get("ALPACA_API_KEY", "")
API_SECRET = os.environ.get("ALPACA_API_SECRET", "")
HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
}

# Target allocation — unchanged from the previous report.
TARGET_ALLOCATION = {
    "tech": 30,
    "index": 25,
    "finance": 15,
    "healthcare": 10,
    "consumer": 10,
    "commodity": 10,
}

# Sector map — copied verbatim from auto_trade.py (check_sector_cooldown /
# set_sector_cooldown) so both scripts agree on what sector a symbol is in.
SECTOR_MAP = {
    "AAPL": "tech", "NVDA": "tech", "MSFT": "tech", "GOOGL": "tech", "AMZN": "consumer_tech",
    "META": "tech", "TSLA": "tech", "AMD": "tech", "INTC": "tech",
    "JPM": "finance", "BAC": "finance", "V": "finance", "MA": "finance",
    "JNJ": "healthcare", "UNH": "healthcare", "PFE": "healthcare", "ABBV": "healthcare",
    "WMT": "consumer", "COST": "consumer", "PG": "consumer", "KO": "consumer",
    "QQQ": "index", "SPY": "index", "IWM": "index", "VOO": "index", "DIA": "index",
    "XLK": "sector_tech", "XLE": "sector_energy", "XLF": "sector_finance", "XLV": "sector_health",
    "GLD": "commodity", "SLV": "commodity", "TLT": "bond",
}

# auto_trade.py uses "sector_*" names for the ETF variants; normalise them onto
# the targeted sector names so XLK counts as tech, XLF as finance, XLV as
# healthcare. Sectors with no target stay under their own name and are
# reported as uncovered exposure rather than being silently dropped.
SECTOR_ALIAS = {
    "sector_tech": "tech",
    "sector_finance": "finance",
    "sector_health": "healthcare",
}

MIN_DEVIATION_PCT = 5.0  # suppress noise; report only meaningful gaps

# patch 2026-11-05: gross exposure cap. Above this, the book is levered and
# sector-level BUY advice compounds the leverage instead of fixing it.
MAX_GROSS_EXPOSURE_PCT = 150.0


def fetch_live_state():
    """Return (positions, portfolio_value). Raises on any failure."""
    if not API_KEY or not API_SECRET:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_API_SECRET missing from .env")

    r = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS, timeout=20)
    r.raise_for_status()
    positions = r.json()
    if not isinstance(positions, list):
        raise RuntimeError(f"unexpected /v2/positions payload: {type(positions).__name__}")

    a = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS, timeout=20)
    a.raise_for_status()
    acct = a.json()
    portfolio_value = float(acct.get("portfolio_value") or 0.0)
    return positions, portfolio_value


def sector_of(symbol):
    raw = SECTOR_MAP.get(symbol, "other")
    return SECTOR_ALIAS.get(raw, raw)


def build_report(positions, portfolio_value):
    """Compute current allocation + suggestions. Pure function of live state.

    patch 2026-11-05: two defects fixed here.

    1. SIGN DISCARDED. The old code took abs(market_value), so a short position
       counted as positive exposure. The live book is a SLV short worth
       -$271,792 against $147,579 equity; treated as a long it reads as
       "184% commodity overweight" instead of "-184% short commodity".

    2. NO GROSS-EXPOSURE CHECK. Suggestions were computed per sector in
       isolation. With gross exposure already at 185.7% of equity, the report
       still emitted five BUY suggestions (tech/index/finance/healthcare/
       consumer, ~$130K of new long exposure). Acting on them would push gross
       exposure to roughly 274% of equity. The report now measures gross
       exposure first and, when it is above MAX_GROSS_EXPOSURE_PCT, replaces
       the BUY suggestions with a single REDUCE_LEVERAGE action.

    Long and short exposure are now reported separately, and net exposure is
    used for the under/overweight comparison, which is what a sector target
    actually means.
    """
    long_exp = {}
    short_exp = {}
    uncovered = {}
    for p in positions:
        symbol = p.get("symbol")
        if not symbol:
            continue
        try:
            mv = float(p.get("market_value") or 0.0)
        except (TypeError, ValueError):
            continue
        if mv == 0:
            continue
        sec = sector_of(symbol)
        if sec not in TARGET_ALLOCATION:
            uncovered[sec] = uncovered.get(sec, 0.0) + abs(mv)
            continue
        # Alpaca reports a short position with a negative market_value.
        if mv < 0:
            short_exp[sec] = short_exp.get(sec, 0.0) + abs(mv)
        else:
            long_exp[sec] = long_exp.get(sec, 0.0) + mv

    if portfolio_value <= 0:
        raise RuntimeError("portfolio_value is 0 — refusing to compute allocation")

    current = {}
    for sec in TARGET_ALLOCATION:
        pct = round((long_exp.get(sec, 0.0) - short_exp.get(sec, 0.0))
                    / portfolio_value * 100, 2)
        current[sec] = pct

    gross_exposure = sum(long_exp.values()) + sum(short_exp.values())
    gross_pct = round(gross_exposure / portfolio_value * 100, 2)
    net_exposure = sum(long_exp.values()) - sum(short_exp.values())
    net_pct = round(net_exposure / portfolio_value * 100, 2)

    suggestions = []
    over_leveraged = gross_pct > MAX_GROSS_EXPOSURE_PCT

    for sec, target in TARGET_ALLOCATION.items():
        cur = current.get(sec, 0.0)
        gap = target - cur
        if gap >= MIN_DEVIATION_PCT:
            if cur < 0:
                # A net-short sector is not "underweight"; it is short.  A BUY
                # label here is ambiguous and can be interpreted as adding to
                # the short.  Ask for the short to be reduced instead.
                suggestions.append({
                    "action": "REDUCE_SHORT",
                    "sector": sec,
                    "reason": f"Net short by {abs(cur):.1f}% of equity",
                    "suggestion": f"Cover/reduce the {sec} short",
                })
            else:
                suggestions.append({
                    "action": "BUY",
                    "sector": sec,
                    "reason": f"Underweight by {gap:.1f}%",
                    "suggestion": f"Increase {sec} exposure",
                })
        elif -gap >= MIN_DEVIATION_PCT:
            suggestions.append({
                "action": "TRIM",
                "sector": sec,
                "reason": f"Overweight by {-gap:.1f}%",
                "suggestion": f"Reduce {sec} exposure",
            })

    # patch 2026-11-05: buying more while the book is already over-leveraged
    # is the wrong advice. Surface the leverage problem instead of burying it
    # under sector-level BUYs that would compound it.
    if over_leveraged:
        biggest_short = max(short_exp, key=short_exp.get) if short_exp else None
        suggestions = [s for s in suggestions if s["action"] != "BUY"]
        leverage_note = {
            "action": "REDUCE_LEVERAGE",
            "sector": biggest_short or "portfolio",
            "reason": (f"Gross exposure {gross_pct:.1f}% of equity exceeds "
                       f"{MAX_GROSS_EXPOSURE_PCT:.0f}% limit"),
            "suggestion": ("Reduce gross exposure before adding any sector "
                           "exposure — buying now would compound the leverage"),
        }
        suggestions.insert(0, leverage_note)

    for sec, mv in uncovered.items():
        suggestions.append({
            "action": "REVIEW",
            "sector": sec,
            "reason": f"{round(mv / portfolio_value * 100, 1)}% of book has no target allocation",
            "suggestion": f"Decide a target for {sec} or trim it",
        })

    return current, suggestions, {
        "gross_exposure_pct": gross_pct,
        "net_exposure_pct": net_pct,
        "long_exposure": {k: round(v, 2) for k, v in long_exp.items()},
        "short_exposure": {k: round(v, 2) for k, v in short_exp.items()},
        "over_leveraged": over_leveraged,
    }


def main():
    print(f"📊 Rebalance check — {datetime.now():%Y-%m-%d %H:%M}")
    try:
        positions, portfolio_value = fetch_live_state()
    except Exception as e:
        # Leave the existing report untouched: a half-written report is what
        # produced the phantom-BUY bug in the first place.
        print(f"❌ Could not read live positions: {e}")
        print("   Refusing to write a report from incomplete state — "
              "existing rebalancing_report.json left unchanged.")
        return 1

    try:
        current, suggestions, exposure_info = build_report(positions, portfolio_value)
    except Exception as e:
        print(f"❌ Could not compute allocation: {e}")
        return 1

    report = {
        "date": datetime.now().isoformat(),
        "portfolio_value": round(portfolio_value, 2),
        "current_allocation": current,
        "target_allocation": TARGET_ALLOCATION,
        "suggestions": suggestions,
        # patch 2026-11-05: gross/net exposure so a consumer can see WHY the
        # report says what it says, instead of inferring it from a sector %.
        "gross_exposure_pct": exposure_info["gross_exposure_pct"],
        "net_exposure_pct": exposure_info["net_exposure_pct"],
        "long_exposure": exposure_info["long_exposure"],
        "short_exposure": exposure_info["short_exposure"],
        "over_leveraged": exposure_info["over_leveraged"],
    }
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    print(f"✅ Wrote {os.path.basename(REPORT_PATH)} "
          f"({len(positions)} positions, {len(suggestions)} suggestion(s))")

    print(f"   Portfolio: ${portfolio_value:,.2f}")
    print(f"   Gross exposure: {exposure_info['gross_exposure_pct']:.1f}% of equity "
          f"(net {exposure_info['net_exposure_pct']:.1f}%)")
    if exposure_info["short_exposure"]:
        shorts = ", ".join(f"{k} ${v:,.0f}"
                           for k, v in exposure_info["short_exposure"].items())
        print(f"   Shorts: {shorts}")
    for sec in TARGET_ALLOCATION:
        print(f"   {sec:<11} current {current.get(sec, 0.0):>6.2f}%  "
              f"target {TARGET_ALLOCATION[sec]:>3}%")
    if suggestions:
        print("   Suggestions:")
        for s in suggestions:
            print(f"     [{s['action']}] {s['sector']}: {s['reason']}")
    else:
        print("   Suggestions: none — every targeted sector within "
              f"{MIN_DEVIATION_PCT:.0f}% of target")
    return 0


if __name__ == "__main__":
    sys.exit(main())
