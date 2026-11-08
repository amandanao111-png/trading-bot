#!/usr/bin/env python3
"""reconcile_fills.py — Alpaca fills vs. the trading bot's journals.

WHY THIS EXISTS (2026-11-08)
----------------------------
The bot's learning loop only learns from rows that are actually in
trade_journal.json. Between Nov 4 and Nov 5 the bot executed three real
exits that never reached the journal, so learned_stats.json froze at 166
trades and symbol_position.json multipliers stopped reflecting reality.

Alpaca is the source of truth for FILLS. The journal is a cache that has to
be reconciled against it.

WHAT IT DOES
  1. Pulls every closed order from Alpaca (paginated with `until` -- the
     `after` cursor does NOT advance on this endpoint and silently re-fetches
     the same page).
  2. Keeps only genuinely filled orders and separates OPENING fills from
     CLOSING fills using `position_intent`. This matters: 912 of 1,287 fills
     are `sell_to_open` one-share dust, and counting them as exits would
     manufacture hundreds of phantom "missing exits".
  3. Clusters closing fills into LOGICAL exits. The bot chunks one exit
     across several Alpaca orders, so a journal row can correspond to many
     fills. Matching fill-to-row would report every chunk as missing.
  4. Reconstructs entry price / realized P&L per logical exit (FIFO).
  5. Matches exits against trade_journal.json AND v7_journal.json, with a
     price-anchored fallback (hand-backfilled rows carry the BACKFILL
     timestamp, not the fill time).
  6. Reports what is missing.

SAFETY CONTRACT
  * READ-ONLY by default. Nothing is written unless --repair is passed.
  * --repair only touches exits newer than --repair-days (default 7), and
    only writes through learning.record_trade() -- the single true write
    path, which also keeps learned_stats.json in sync and reproduces the
    live bot's BUY-closing semantics. A hand-rolled json.dump would desync
    learned_stats.json and leave the backfilled row un-countable.
  * Historical gaps (older than --repair-days) are REPORTED, never written.
    Backfilling months of history would retroactively rewrite every symbol's
    win-rate and size multiplier -- that is a human decision.
  * learned_stats.json and symbol_position.json are NEVER written here.
    symbol_position.json is derived from the journal by evolution.py, so the
    correct follow-up is to re-run evolution, not to hand-patch it.
  * Never prints the API key or secret. Never raises out of main().

USAGE
    python3 reconcile_fills.py                  # read-only report
    python3 reconcile_fills.py --since 2026-11-01
    python3 reconcile_fills.py --repair         # repair exits <= 7 days old
    python3 reconcile_fills.py --repair --repair-days 30
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOURNAL_FILE = os.path.join(BASE_DIR, "trade_journal.json")
V7_JOURNAL = os.path.join(BASE_DIR, "v7_journal.json")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
API_URL = os.environ.get("ALPACA_API_URL", "https://paper-api.alpaca.markets")

# Closing fills within this many seconds of each other (and at a similar
# price) are treated as ONE logical exit split across several orders.
EXIT_CLUSTER_SECONDS = 300

PRICE_TOL_REL = 0.005     # 0.5%
PRICE_TOL_ABS = 0.02
QTY_TOL_REL = 0.01
QTY_TOL_ABS = 1.0

MATCH_TIME_TOL_SEC = 1800      # journal row vs Alpaca fill
V7_TIME_TOL_SEC = 300          # v7 cycle timestamp vs Alpaca fill

OPEN_INTENTS = ("buy_to_open", "sell_to_open")
CLOSE_INTENTS = ("buy_to_close", "sell_to_close")


def _local_tz():
    if ZoneInfo is not None:
        return ZoneInfo("Europe/London")
    return timezone.utc


def _load_dotenv():
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass


def _headers():
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_API_SECRET", "")
    if not key or not secret:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_API_SECRET not set")
    return {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }


def _to_local(ts):
    """Alpaca UTC -> naive Europe/London (the journal's timestamp convention)."""
    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if ZoneInfo is not None:
        dt = dt.astimezone(ZoneInfo("Europe/London"))
    return dt.replace(tzinfo=None)


def _journal_ts(value):
    """Journal naive local -> naive local."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _fetch_orders(max_orders=3000):
    """All closed orders, paginated with `until`.

    `after` does NOT advance the cursor on /v2/orders -- it returns the same
    page. `until` (with direction=desc) does.
    """
    import requests
    headers = _headers()
    out, seen, until = [], set(), None
    for _ in range(40):
        params = {"status": "closed", "limit": 500, "direction": "desc"}
        if until:
            params["until"] = until
        r = requests.get(f"{API_URL}/v2/orders", headers=headers, params=params, timeout=45)
        if r.status_code != 200:
            break
        page = r.json()
        if not isinstance(page, list) or not page:
            break
        fresh = [o for o in page if o.get("id") not in seen]
        for o in fresh:
            seen.add(o.get("id"))
        out.extend(fresh)
        if len(page) < 500 or not fresh or len(out) >= max_orders:
            break
        oldest = min(o.get("submitted_at") for o in page if o.get("submitted_at"))
        until = oldest
    return out[:max_orders]


def _fills(orders):
    """Normalised fills: only genuinely filled orders, intent preserved."""
    fills = []
    for o in orders:
        if o.get("status") != "filled":
            continue
        try:
            qty = float(o.get("filled_qty") or 0)
            price = float(o.get("filled_avg_price") or 0)
        except Exception:
            continue
        if qty <= 0 or price <= 0:
            continue
        stamp = o.get("filled_at") or o.get("submitted_at")
        if not stamp:
            continue
        fills.append({
            "id": o.get("id"),
            "symbol": (o.get("symbol") or "").upper(),
            "side": (o.get("side") or "").lower(),
            "intent": o.get("position_intent") or "",
            "qty": qty,
            "price": price,
            "time": _to_local(stamp),
        })
    fills.sort(key=lambda f: (f["time"], str(f["id"])))
    return fills


def _price_close(a, b):
    return abs(a - b) <= max(PRICE_TOL_ABS, PRICE_TOL_REL * max(abs(a), abs(b)))


def _qty_close(a, b):
    return abs(a - b) <= max(QTY_TOL_ABS, QTY_TOL_REL * max(abs(a), abs(b)))


def _cluster_exits(fills):
    """Group CLOSING fills into logical exits (one bot exit = N orders)."""
    by_symbol = defaultdict(list)
    for f in fills:
        if f["intent"] in CLOSE_INTENTS:
            by_symbol[f["symbol"]].append(f)

    events = []
    for symbol, fl in by_symbol.items():
        cur, ref = [], None
        for f in fl:
            if ref is None:
                ref, cur = f, [f]
                continue
            gap = (f["time"] - ref["time"]).total_seconds()
            if gap <= EXIT_CLUSTER_SECONDS and _price_close(f["price"], ref["price"]):
                cur.append(f)
            else:
                events.append(_event(symbol, cur))
                ref, cur = f, [f]
        if cur:
            events.append(_event(symbol, cur))
    events.sort(key=lambda e: e["time"])
    return events


def _event(symbol, fills):
    qty = sum(f["qty"] for f in fills)
    vwap = sum(f["qty"] * f["price"] for f in fills) / qty if qty else 0.0
    side = "long" if fills[0]["side"] == "sell" else "short"
    return {
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": vwap,
        "time": min(f["time"] for f in fills),
        "orders": len(fills),
    }


def _entry_prices(fills, events):
    """FIFO entry price + realized P&L for each logical exit."""
    by_symbol = defaultdict(lambda: {"long": [], "short": []})
    for f in fills:
        if f["intent"] in OPEN_INTENTS:
            key = "long" if f["intent"] == "buy_to_open" else "short"
            by_symbol[f["symbol"]][key].append(f)

    for e in events:
        lots = by_symbol[e["symbol"]][e["side"]]
        need = e["qty"]
        cost = 0.0
        for lot in lots:
            if need <= 1e-9:
                break
            take = min(need, lot["qty"])
            cost += take * lot["price"]
            need -= take
        got = e["qty"] - need
        e["entry_price"] = (cost / got) if got > 0 else 0.0
        if e["side"] == "long":
            e["pnl"] = (e["price"] - e["entry_price"]) * got
        else:
            e["pnl"] = (e["entry_price"] - e["price"]) * got
        e["matched_qty"] = got
    return events


def _load_journal():
    try:
        with open(JOURNAL_FILE) as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _load_v7_exits():
    """v7_journal.json is JSONL: {time, trades:[], exits:[symbols]}."""
    events = []
    if not os.path.exists(V7_JOURNAL):
        return events
    try:
        with open(V7_JOURNAL) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                when = _journal_ts(row.get("time"))
                if when is None:
                    continue
                for sym in row.get("exits") or []:
                    events.append({"symbol": str(sym).upper(), "time": when})
    except Exception:
        pass
    return events


def _find_missing(events, journal, v7_events):
    """Exit events with no journal row (trade_journal or v7)."""
    rows = []
    for i, r in enumerate(journal):
        if not isinstance(r, dict):
            continue
        if (r.get("type") or "").upper() != "SELL":
            continue
        rows.append({
            "i": i,
            "symbol": (r.get("symbol") or "").upper(),
            "qty": float(r.get("qty") or 0),
            "price": float(r.get("price") or 0),
            "time": _journal_ts(r.get("timestamp")),
        })

    missing = []
    used = set()
    for e in events:
        # primary: symbol + qty + time
        hit = None
        for r in rows:
            if r["i"] in used or r["symbol"] != e["symbol"]:
                continue
            if not _qty_close(r["qty"], e["qty"]):
                continue
            if r["time"] and abs((e["time"] - r["time"]).total_seconds()) <= MATCH_TIME_TOL_SEC:
                hit = r["i"]
                break
        if hit is None:
            # price-anchored fallback: hand-backfilled rows carry the backfill
            # timestamp, so match on symbol+qty+price instead of time
            for r in rows:
                if r["i"] in used or r["symbol"] != e["symbol"]:
                    continue
                if not _qty_close(r["qty"], e["qty"]):
                    continue
                if r["price"] and _price_close(r["price"], e["price"]):
                    hit = r["i"]
                    break
        if hit is not None:
            used.add(hit)
            continue

        # v7_journal: symbol only, no qty
        if any(v["symbol"] == e["symbol"]
               and abs((v["time"] - e["time"]).total_seconds()) <= V7_TIME_TOL_SEC
               for v in v7_events):
            continue

        missing.append(e)
    return missing


def _notify(lines):
    """Send a Telegram note ONLY when there is something to say.

    Uses the same message-send pattern as scripts/automation_health.py. The
    nightly job must stay silent on the many days it finds nothing to do,
    or it becomes noise the user learns to ignore. Nothing is sent unless
    the journal was actually changed.
    """
    if not lines:
        return False
    import subprocess
    try:
        subprocess.run(
            ["openclaw", "message", "send",
             "--channel", "telegram",
             "--target", "8250061141",
             "--message", "\n".join(lines)],
            capture_output=True, text=True, timeout=30)
        return True
    except Exception:
        return False


def _repair(missing, days):
    """Write missing recent exits through learning.record_trade().

    Uses the bot's own write path so that:
      * learned_stats.json stays in sync (record_trade maintains it),
      * the matching open BUY is closed with the P&L exactly as a live exit
        does, which is what evolution.py's derivation actually counts.
    """
    cutoff = datetime.now() - timedelta(days=days)
    todo = [e for e in missing if e["time"] >= cutoff]
    skipped = [e for e in missing if e["time"] < cutoff]
    if not todo:
        return [], skipped, []

    import learning
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup = os.path.join(BACKUP_DIR, f"trade_journal.json.bak.reconcile-{stamp}")
    import shutil
    shutil.copy2(JOURNAL_FILE, backup)

    journal = _load_journal()
    open_buys = defaultdict(int)
    for r in journal:
        if ((r.get("type") or "").upper() == "BUY"
                and (r.get("symbol") or "").upper() in {e["symbol"] for e in todo}
                and not r.get("closed", False)):
            open_buys[(r.get("symbol") or "").upper()] += 1

    written = []
    no_open_buy = []
    for e in todo:
        symbol = e["symbol"]
        # learning.record_trade('SELL') closes the OLDEST OPEN BUY row for the
        # symbol. If there is no open BUY, it would close some OTHER lot and
        # mis-attribute the P&L to the wrong trade. In that case the correct
        # action is to adopt the position from Alpaca (what
        # fix_stale_journal.py does), not to guess. Report it instead.
        if open_buys.get(symbol, 0) <= 0:
            no_open_buy.append(e)
            continue
        signal = "RECONCILED"
        reasons = ["RECONCILED", f"RECONCILE_{datetime.now().strftime('%Y-%m-%d')}"]
        qty = int(round(e["qty"])) if float(e["qty"]).is_integer() else e["qty"]
        learning.record_trade("SELL", symbol, qty, round(e["price"], 6),
                              signal, 0, reasons, round(e["pnl"], 2))
        open_buys[symbol] -= 1
        written.append({
            "symbol": symbol, "qty": qty,
            "price": round(e["price"], 6), "pnl": round(e["pnl"], 2),
            "exit_time": e["time"].isoformat(),
        })
    return written, skipped, no_open_buy


def main():
    repair = "--repair" in sys.argv
    days = 7
    since = None
    for a in sys.argv[1:]:
        if a.startswith("--repair-days"):
            try:
                days = int(a.split("=", 1)[1])
            except Exception:
                pass
        if a.startswith("--since"):
            try:
                since = datetime.strptime(a.split("=", 1)[1], "%Y-%m-%d")
            except Exception:
                pass

    _load_dotenv()
    try:
        orders = _fetch_orders()
    except Exception as e:
        print(f"ERROR: could not fetch orders: {e}")
        return 1

    fills = _fills(orders)
    events = _entry_prices(fills, _cluster_exits(fills))
    journal = _load_journal()
    v7 = _load_v7_exits()
    missing = _find_missing(events, journal, v7)
    if since:
        missing = [e for e in missing if e["time"] >= since]

    print(f"closed orders fetched : {len(orders)}")
    print(f"filled orders         : {len(fills)}")
    print(f"  opening fills       : {sum(1 for f in fills if f['intent'] in OPEN_INTENTS)}")
    print(f"  closing fills       : {sum(1 for f in fills if f['intent'] in CLOSE_INTENTS)}")
    print(f"logical exits         : {len(events)}")
    print(f"journal rows          : {len(journal)}")
    print(f"v7 exit events        : {len(v7)}")
    print(f"MISSING exits         : {len(missing)}")
    if missing:
        print()
        for e in missing:
            print(f"  - {e['time'].isoformat()} {e['symbol']:6s} {e['side']:5s} "
                  f"qty={e['qty']:<9.0f} entry={e['entry_price']:<9.4f} "
                  f"exit={e['price']:<9.4f} pnl={e['pnl']:<9.2f} "
                  f"({e['orders']} order(s))")

    if not repair:
        print("\nread-only — nothing written. Re-run with --repair to append "
              f"exits newer than {days} days.")
        return 0

    if not missing:
        print("\n--repair requested but nothing is missing — no write performed.")
        return 0

    written, skipped, no_open_buy = _repair(missing, days)
    print(f"\nbackup written      : {os.path.join(BACKUP_DIR, 'trade_journal.json.bak.reconcile-*')}")
    print(f"appended {len(written)} row(s):")
    for w in written:
        print(f"  + {w['exit_time']} {w['symbol']:6s} qty={w['qty']:<9} "
              f"px={w['price']:<10} pnl={w['pnl']}")

    # Notify ONLY if the journal actually changed. A nightly job that sends a
    # message every night regardless is noise, and the dedupe logic in
    # automation_health.py exists for exactly this reason.
    if written:
        _notify([
            "🔧 Trading journal reconciled",
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')} — {len(written)} exit(s) "
            f"were missing from the journal and have been repaired.",
            "",
            *[f"• {w['symbol']} qty={w['qty']} px={w['price']} pnl={w['pnl']} "
              f"({w['exit_time']})" for w in written[:10]],
            *(["• ..."] if len(written) > 10 else []),
            "",
            "The learning loop can now see these trades again. Evolution will",
            "pick them up on its next pass.",
        ])
    if no_open_buy:
        print(f"\n{len(no_open_buy)} exit(s) have NO open BUY row to close:")
        for e in no_open_buy:
            print(f"  - {e['time'].isoformat()} {e['symbol']:6s} qty={e['qty']:<9} pnl={e['pnl']:<9.2f}")
        print("  These are NOT repaired: learning.record_trade closes the oldest open")
        print("  BUY for a symbol, and with none open it would attach the P&L to an")
        print("  unrelated lot. Adopting them needs a position-aware reconciliation,")
        print("  not a guessed write.")
    if skipped:
        print(f"\n{len(skipped)} HISTORICAL exit(s) left unrepaired on purpose "
              f"(older than {days} days). They are reported above. Backfilling "
              f"them would retroactively rewrite every symbol's win rate and "
              f"size multiplier — that needs a human decision.")
        for e in skipped[:20]:
            print(f"  - {e['time'].isoformat()} {e['symbol']:6s} qty={e['qty']:<9.0f} pnl={e['pnl']:<9.2f}")
        if len(skipped) > 20:
            print(f"  ... and {len(skipped) - 20} more")
    print("\nNext step: re-run evolution so learned_stats.json / symbol_position.json "
          "pick up the repaired rows.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"reconcile_fills failed: {exc}")
        sys.exit(1)
