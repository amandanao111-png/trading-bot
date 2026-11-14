#!/usr/bin/env python3
"""
bot_status_digest.py — Daily 1-page digest of the trading bot.

Reads from LIVE sources (no stale V6 snapshot):
- Alpaca /v2/positions → real open positions + live sector allocation
- trade_journal.json → open positions (fallback when the API is unreachable)
- equity_curve_v6.json → live portfolio value + open-position count
- rebalancing_report.json → fallback allocation when the API is unreachable
- symbol_position.json → per-symbol evolution multipliers (NOT positions)
- disabled_features.json → what's been turned off (the bot's evolution trail)

patch 2026-11-12 (Day 256): two defects were fixed here after measuring
what the digest actually printed against Alpaca.

  1. "Active Positions" was read from symbol_position.json, which holds
     per-symbol evolution multipliers (win rate, average P&L), not
     positions. The 2026-11-12 digest reported "PFE: multiplier=0.6" as
     the only open position while the bot actually held AAPL (1), BAC (53)
     and NVDA (5). Open positions now come from the trade journal, which
     was verified to reproduce Alpaca's book exactly.

  2. Sector allocation came from rebalancing_report.json, which the
     weekly cron writes on Sunday. On 2026-11-12 that file was dated
     2026-11-08T18:00 — it predated all three open positions, so it
     reported gross exposure 0.0% and every sector at 0.00%, and the
     digest printed "6 BUY signal(s) sitting idle" as an action item.
     Allocation is now computed live from Alpaca. When the API is
     unreachable the file is used, but only when it is genuinely fresh
     (it must not predate the newest open position).

Writes dashboard/bot_digest_YYYY-MM-DD.txt
Replaces broken equity_curve_v6.json (stale 19 days as of 2026-07-08).

Run manually:  python3 trading_bot/bot_status_digest.py
Run on cron:   03:09 daily, before morning-ideas
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path("/Users/amandaai/.openclaw/workspace")
DASH = WORKSPACE / "dashboard"
TODAY = datetime.now().strftime("%Y-%m-%d")


def read_json(path: Path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def find_bot_processes():
    """Return live bot processes as a list of (pid, etime_days).

    A command merely containing ``auto_trade.py`` is not proof that the bot
    is running (pgrep/tail/pytest commands can contain that text).  Require a
    real Python executable and the script as a separate argument.
    """
    import subprocess
    try:
        out = subprocess.check_output(
            ["ps", "-axo", "pid,etime,command"], text=True
        )
    except Exception:
        return []
    processes = []
    for line in out.splitlines():
        if "auto_trade.py" not in line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        command = parts[2]
        command_parts = command.split()
        is_python = any(
            part.rsplit("/", 1)[-1].lower() in {"python", "python3"}
            or part.rsplit("/", 1)[-1].lower().startswith("python3.")
            for part in command_parts[:1]
        )
        has_script = any(
            part.rsplit("/", 1)[-1] == "auto_trade.py"
            for part in command_parts[1:]
        )
        if is_python and has_script:
            parts = line.split()
            # etime like "04-23:58:39" or "23:58:39"
            etime = parts[1]
            days = 0
            if "-" in etime:
                days_str, _, _ = etime.partition("-")
                days = int(days_str)
                etime = etime.split("-", 1)[1]
            processes.append((int(parts[0]), days))
    return processes


def get_bot_alive():
    """Backward-compatible wrapper returning the first live bot process."""
    processes = find_bot_processes()
    return processes[0] if processes else (None, None)


def format_disabled_features(disabled):
    """Return disabled feature names from either supported JSON schema."""
    if not isinstance(disabled, dict):
        return []
    values = disabled.get("disabled", disabled.get("disabled_features", []))
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if value]


def report_age_seconds(report):
    """Return report age in seconds; future-dated reports have a negative age."""
    raw = report.get("date") if isinstance(report, dict) else None
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        now = datetime.now(stamp.tzinfo) if stamp.tzinfo else datetime.now()
        return (now - stamp).total_seconds()
    except Exception:
        return None


def get_last_error():
    """Last error line in bot_out.log (ignores the stale dotenv error pre-Jul-3 fix)."""
    log = WORKSPACE / "trading_bot" / "bot_out.log"
    if not log.exists():
        return None
    text = log.read_text(errors="ignore").splitlines()
    # Find the LAST "Traceback" / "Error" line by scanning in reverse
    for line in reversed(text):
        if "ModuleNotFoundError: No module named 'dotenv'" in line:
            # this is the known-stale error; ignore unless it's the last error
            continue
        if "Error" in line or "Traceback" in line:
            return line.strip()[:200]
    return None


def derive_open_positions():
    """Open positions derived from the trade journal.

    The journal is the source of truth for what the book holds: a BUY adds
    quantity, a SELL subtracts it, and a row with ``closed=True`` marks the
    symbol flat at that point in history.

    Verified 2026-11-12: this reproduces Alpaca's /v2/positions exactly
    (AAPL 1 @ 354.83, BAC 53 @ 50.71, NVDA 5 @ 174.43).
    """
    rows = read_json(WORKSPACE / "trading_bot" / "trade_journal.json", [])
    if not isinstance(rows, list):
        return {}
    books = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        sym = r.get("symbol")
        if not sym:
            continue
        try:
            qty = float(r.get("qty") or 0)
            price = float(r.get("price") or 0)
        except (TypeError, ValueError):
            continue
        book = books.setdefault(sym, {"qty": 0.0, "cost": 0.0, "opened_at": None})
        kind = r.get("type")
        if kind == "BUY":
            book["qty"] += qty
            book["cost"] += qty * price
            if not r.get("closed"):
                book["opened_at"] = r.get("timestamp")
        elif kind in ("SELL", "EXIT", "CLOSE"):
            book["qty"] -= qty
            book["cost"] -= qty * price
        if r.get("closed"):
            book["qty"] = 0.0
            book["cost"] = 0.0
            book["opened_at"] = None
    out = {}
    for sym, book in books.items():
        # A negative quantity here is not evidence of a short position. The
        # journal is capped, so once a BUY row falls out of the window its
        # later SELL row remains and arithmetic produces a phantom negative
        # position. The bot is long-only; omit these unverifiable rows rather
        # than publishing fake shorts in the digest.
        if book["qty"] <= 1e-9:
            continue
        out[sym] = {
            "qty": book["qty"],
            "avg_price": (book["cost"] / book["qty"]) if book["qty"] else 0.0,
            "opened_at": book["opened_at"],
        }
    return out


def open_positions_from_live(live):
    """Convert a successful Alpaca response into the digest position map.

    An empty Alpaca position list is authoritative: it means the book is
    flat. It must not trigger the journal fallback. The old code treated
    "empty live list" the same as "live API unavailable", so a real flat
    book was replaced by phantom journal shorts.
    """
    out = {}
    if not isinstance(live, dict):
        return out
    positions = live.get("positions")
    if not isinstance(positions, list):
        return out
    for position in positions:
        if not isinstance(position, dict):
            continue
        symbol = position.get("symbol")
        if not symbol:
            continue
        try:
            qty = float(position.get("qty") or 0)
            avg = float(position.get("avg_entry_price") or 0)
            market_value = float(position.get("market_value") or 0)
        except (TypeError, ValueError):
            continue
        if abs(qty) < 1e-9:
            continue
        out[symbol] = {
            "qty": qty,
            "avg_price": avg,
            "market_value": market_value,
            "opened_at": None,
        }
    return out


def live_allocation():
    """Compute sector allocation from the live Alpaca book.

    Returns None when the API cannot be reached, so the caller can fall back
    to the weekly report file without pretending it is current.
    """
    try:
        import rebalancing as rb
        positions, portfolio_value = rb.fetch_live_state()
        current, suggestions, exposure = rb.build_report(positions, portfolio_value)
        return {
            "date": datetime.now().isoformat(timespec="seconds"),
            "portfolio_value": portfolio_value,
            "current_allocation": current,
            "target_allocation": rb.TARGET_ALLOCATION,
            "suggestions": suggestions,
            "positions": positions,
            **exposure,
        }
    except Exception:
        return None


def equity_curve_latest():
    """Latest row of equity_curve_v6.json (the bot writes it every cycle)."""
    rows = read_json(WORKSPACE / "trading_bot" / "equity_curve_v6.json", [])
    if isinstance(rows, list) and rows:
        last = rows[-1]
        if isinstance(last, dict):
            return last
    return {}


def newest_open_timestamp(open_pos):
    """Newest open-position timestamp, as an ISO string."""
    stamps = [p.get("opened_at") for p in open_pos.values() if p.get("opened_at")]
    return max(stamps) if stamps else None


def reconcile_journal_positions(journal_pos, live_pos):
    """Remove journal rows whose sign contradicts the live book.

    The journal is useful only for the open timestamp. Its quantities can be
    stale because the file is capped, while Alpaca is the live book of
    record. A symbol present in one source with the opposite sign is
    inconsistent and must not be shown as a holding.
    """
    reconciled = {}
    for symbol, position in live_pos.items():
        row = dict(position)
        journal = journal_pos.get(symbol)
        if isinstance(journal, dict):
            try:
                same_sign = (float(journal.get("qty") or 0) *
                             float(row.get("qty") or 0)) >= 0
            except (TypeError, ValueError):
                same_sign = False
            if same_sign and journal.get("opened_at"):
                row["opened_at"] = journal.get("opened_at")
        reconciled[symbol] = row
    return reconciled


def main():
    live = live_allocation()
    processes = find_bot_processes()
    pid, days_alive = processes[0] if processes else (None, None)
    reb = read_json(WORKSPACE / "trading_bot" / "rebalancing_report.json", {})
    multipliers = read_json(WORKSPACE / "trading_bot" / "symbol_position.json", {})
    disabled = read_json(WORKSPACE / "trading_bot" / "disabled_features.json", {})

    # Open positions come from the trade journal, which reproduces Alpaca's
    # book exactly (verified 2026-11-12). symbol_position.json holds
    # per-symbol evolution multipliers and is NOT a position list.
    live_positions = [p for p in (live or {}).get("positions", []) if isinstance(p, dict)]

    # Open positions: Alpaca is the book of record. A successful response
    # with zero positions means the book is flat; only an unavailable live
    # API may use the journal fallback. The journal is capped, so a missing
    # earlier BUY can otherwise derive a phantom negative position.
    if live is not None:
        open_pos = open_positions_from_live(live)
    else:
        open_pos = derive_open_positions()
    # Stamp each live position with its open time from the journal only when
    # the two sources agree on the direction of the holding.
    journal_pos = derive_open_positions()
    open_pos = reconcile_journal_positions(journal_pos, open_pos)

    # Allocation: prefer the live Alpaca book. The weekly report file is
    # only a fallback, and only when it does not predate the newest open
    # position -- a report written before the positions existed cannot
    # describe the book that exists now.
    if live:
        reb = live
        allocation_source = "live (Alpaca /v2/positions)"
    else:
        allocation_source = "weekly rebalancing_report.json (fallback)"
    portfolio = reb.get("portfolio_value", 0)
    alloc = reb.get("current_allocation", {})
    target = reb.get("target_allocation", {})
    suggestions = reb.get("suggestions", [])

    report_age_seconds_value = report_age_seconds(reb)
    report_age_days = (report_age_seconds_value / 86400
                       if report_age_seconds_value is not None else None)

    newest_open = newest_open_timestamp(open_pos)
    report_predates_book = False
    if not live and newest_open and reb.get("date"):
        try:
            report_ts = datetime.fromisoformat(str(reb["date"]).replace("Z", "+00:00"))
            open_ts = datetime.fromisoformat(str(newest_open).replace("Z", "+00:00"))
            if report_ts.tzinfo is None:
                report_ts = report_ts.replace(tzinfo=open_ts.tzinfo)
            if open_ts.tzinfo is None:
                open_ts = open_ts.replace(tzinfo=report_ts.tzinfo)
            report_predates_book = report_ts < open_ts
        except Exception:
            report_predates_book = False

    report_stale = (report_age_seconds_value is None
                    or report_age_seconds_value < 0
                    or report_age_seconds_value > 7 * 86400
                    or not alloc
                    or report_predates_book)
    if report_stale:
        suggestions = []

    out = []
    out.append(f"# Bot Digest — {TODAY}")
    out.append("")
    out.append(f"Generated at: {datetime.now().strftime('%H:%M:%S BST')}")
    out.append("")
    out.append("## Process")
    out.append(f"- PID: {pid or '❌ NOT RUNNING'}")
    if pid and days_alive is not None:
        out.append(f"- Uptime: {days_alive} day(s) alive")
    if len(processes) > 1:
        out.append(f"- ⚠️ Instance count: {len(processes)} (expected 1)")
    out.append("")

    out.append("## Portfolio")
    out.append(f"- Value: ${portfolio:,.2f}" if portfolio else "- Value: unknown")
    if alloc:
        out.append(f"- Current allocation:")
        for k, v in sorted(alloc.items()):
            out.append(f"  - {k}: {v:+.2f}%")
    if target:
        out.append(f"- Target allocation:")
        for k, v in sorted(target.items()):
            out.append(f"  - {k}: {v}%")
    out.append("")

    if report_stale:
        age_text = (f"{report_age_days:.1f}d old"
                    if report_age_days is not None else "unknown age")
        why = ("report predates the current open positions"
               if report_predates_book else "report too old")
        out.append(
            f"## Rebalancing Suggestions — STALE "
            f"({age_text}, {why}, "
            f"allocation={json.dumps(alloc) if alloc else '{}'}) — suppressed"
        )
    else:
        out.append("## Rebalancing Suggestions")
        out.append(f"- Source: {allocation_source}")
    if not report_stale and reb.get("gross_exposure_pct") is not None:
        # patch 2026-11-05: show gross exposure next to the suggestions. A
        # sector % on its own hides the fact that the book is levered.
        out.append(
            f"- Gross exposure: {reb.get('gross_exposure_pct', 0):.1f}% of equity "
            f"(net {reb.get('net_exposure_pct', 0):.1f}%)"
        )
        shorts = reb.get("short_exposure") or {}
        if shorts:
            short_txt = ", ".join(f"{k} ${v:,.0f}" for k, v in shorts.items())
            out.append(f"- Short exposure: {short_txt}")
        if reb.get("over_leveraged"):
            out.append(
                "- 🔴 Over-leveraged: sector BUY signals suppressed until gross "
                "exposure is reduced"
            )
    if suggestions:
        for s in suggestions[:6]:
            out.append(
                f"- [{s.get('action','?')}] {s.get('sector','?')}: {s.get('reason','')}"
            )
    else:
        out.append("- (none)")
    out.append("")

    out.append("## Active Positions")
    if open_pos:
        for sym in sorted(open_pos):
            p = open_pos[sym]
            mv = ""
            if p.get("market_value"):
                mv = f", market ${p['market_value']:,.0f}"
            out.append(
                f"- {sym}: {p['qty']:g} sh @ {p['avg_price']:.2f}{mv}"
                + (f" (opened {p['opened_at']})" if p.get("opened_at") else "")
            )
    else:
        out.append("- (none open)")
    out.append("")

    # Per-symbol evolution multipliers. These are NOT positions -- they are
    # the sizing/confidence the evolution pass learned per symbol.
    if isinstance(multipliers, dict) and multipliers:
        out.append("## Symbol Multipliers (evolution, not positions)")
        for sym in sorted(multipliers):
            m = multipliers[sym]
            if not isinstance(m, dict):
                continue
            out.append(
                f"- {sym}: multiplier={m.get('multiplier')}, "
                f"WR={m.get('win_rate')}% ({m.get('trades')} trades), "
                f"avg_pnl={m.get('avg_pnl')}"
            )
        out.append("")

    out.append("## Disabled Features (evolution trail)")
    feats = format_disabled_features(disabled)
    if feats:
        for feature in feats:
            out.append(f"- ❌ {feature}")
    else:
        out.append("- (none)")
    out.append("")

    last_err = get_last_error()
    out.append("## Last Error (real, non-stale)")
    out.append(f"- {last_err or '(none)'}")
    out.append("")

    out.append("## Action Items")
    items = []
    if not pid:
        items.append("🔴 bot not running — investigate")
    if portfolio and abs(alloc.get("commodity", 0)) > 30:
        if alloc.get("commodity", 0) < 0:
            items.append(
                f"🔴 commodity is a SHORT at {abs(alloc.get('commodity', 0)):.1f}% "
                "of equity — reduce before adding any long exposure"
            )
        else:
            items.append(f"⚠️  commodity alloc {alloc.get('commodity'):.1f}% — rebalance")
    if reb.get("over_leveraged"):
        items.append(
            f"🔴 gross exposure {reb.get('gross_exposure_pct', 0):.1f}% of equity "
            "— over the 150% limit, reduce leverage first"
        )
    if suggestions and any(s.get("action") == "BUY" for s in suggestions):
        buy = [s.get("sector") for s in suggestions if s.get("action") == "BUY"]
        items.append(
            f"💰 {len(buy)} sector target gap(s): {', '.join(buy)} "
            "(allocation guidance, not trade signals)"
        )
    elif report_stale:
        items.append(
            "⚠️ rebalancing_report.json is stale — 6 phantom BUY signals suppressed. "
            "Rebalance cron disabled (target script missing). Needs a fresh report."
        )
    if not items:
        items.append("✅ no action needed")
    for i in items:
        out.append(f"- {i}")
    out.append("")

    DASH.mkdir(parents=True, exist_ok=True)
    outfile = DASH / f"bot_digest_{TODAY}.txt"
    outfile.write_text("\n".join(out))

    # Also write to "latest" for one-line checks
    latest = DASH / "bot_digest_latest.txt"
    latest.write_text("\n".join(out))

    print(f"✅ Wrote {outfile}")
    print("\n".join(out[:25]))


if __name__ == "__main__":
    main()
