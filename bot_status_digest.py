#!/usr/bin/env python3
"""
bot_status_digest.py — Daily 1-page digest of the trading bot.

Reads from LIVE sources (no stale V6 snapshot):
- bot_out.log → last error / alive signal
- rebalancing_report.json → latest portfolio value + sector suggestions
- symbol_position.json → active positions
- disabled_features.json → what's been turned off (the bot's evolution trail)

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


def main():
    processes = find_bot_processes()
    pid, days_alive = processes[0] if processes else (None, None)
    reb = read_json(WORKSPACE / "trading_bot" / "rebalancing_report.json", {})
    pos = read_json(WORKSPACE / "trading_bot" / "symbol_position.json", {})
    disabled = read_json(WORKSPACE / "trading_bot" / "disabled_features.json", {})

    portfolio = reb.get("portfolio_value", 0)
    alloc = reb.get("current_allocation", {})
    target = reb.get("target_allocation", {})
    suggestions = reb.get("suggestions", [])

    # Staleness guard (2026-10-24): a stale rebalancing_report.json with an
    # empty current_allocation makes every sector read "underweight by full
    # target" -> 6 phantom BUYs and a fake "BUY signals sitting idle" item.
    # Only trust the report if it is recent AND carries a real allocation.
    report_age_seconds_value = report_age_seconds(reb)
    report_age_days = (report_age_seconds_value / 86400
                       if report_age_seconds_value is not None else None)
    report_stale = (report_age_seconds_value is None
                    or report_age_seconds_value < 0
                    or report_age_seconds_value > 7 * 86400
                    or not alloc)
    if report_stale:
        suggestions = []
    positions = list(pos.keys()) if isinstance(pos, dict) else []

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
        out.append(
            f"## Rebalancing Suggestions — STALE "
            f"({age_text}, "
            f"allocation={json.dumps(alloc) if alloc else '{}'}) — suppressed"
        )
    else:
        out.append("## Rebalancing Suggestions")
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
    if positions:
        for sym in positions:
            p = pos[sym]
            out.append(
                f"- {sym}: multiplier={p.get('multiplier')}, "
                f"WR={p.get('win_rate')}% ({p.get('trades')} trades), "
                f"avg_pnl={p.get('avg_pnl')}"
            )
    else:
        out.append("- (none open)")
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
        items.append(f"💰 {len(buy)} BUY signal(s) sitting idle: {', '.join(buy)}")
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
