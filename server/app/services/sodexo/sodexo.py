#!/usr/bin/env python3
"""
Fetch today's meals ("Tänään") from Sodexo Frami and send them to a Discord webhook.
Also includes a scheduler that posts every weekday at a set local time in a background thread.
"""

import logging
import os
import sys
import threading
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

URL = "https://www.sodexo.fi/ravintolat/ravintola-frami"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Python/requests",
    "Accept-Language": "fi-FI,fi;q=0.9,en-US;q=0.8,en;q=0.7",
}

# ----- Helpers for scraping -----


def _nfc_lower(s: str) -> str:
    return unicodedata.normalize("NFC", s).strip().lower()


def _find_today_tab_id(soup: BeautifulSoup) -> str | None:
    # The tab bar has class "meal-date-tabs …"
    ul = soup.select_one("ul.meal-date-tabs")
    if not ul:
        return None

    # Look for an <a> whose text is "Tänään"
    for a in ul.select("li > a"):
        label = _nfc_lower(a.get_text(strip=True))
        if label == "tänään" or label.startswith("tänään"):
            href = a.get("href", "").strip()
            if href.startswith("#"):
                return href.lstrip("#")
    return None


@dataclass
class Meal:
    type: str
    name: str


# ----- Discord formatting & sending -----


def _send_today_meals_to_discord(
    meals: list[Meal],
    webhook_url: str,
    restaurant_name: str = "Ravintola Frami",
    username: str | None = "Lounasbotti",
    avatar_url: str | None = None,
) -> None:
    """
    Renames & orders meals, formats a 'Tänään' message, and posts it to a Discord webhook.
    """
    mapping: dict[str, str] = {
        "FROM OUR FAVORITES": "Perussetti",
        "FROM THE SOUP BOWL": "Keittolounas",
        "FROM THE SWEET": "Jälkiruoka",
        "FROM THE FIELD-VEGAN": "Vegaani",
        "From our bakery": "PATONKI",
        "FROM THE GRILL": "Prikka?",
    }
    # Accept both original and renamed labels for ordering
    order_index: dict[str, int] = {}
    for idx, (src, dst) in enumerate(mapping.items()):
        order_index[src] = idx
        order_index[dst] = idx

    # Stable sort by our mapping (unknown types after known)
    meals.sort(key=lambda m: order_index.get(m.type, float("inf")))
    # Rename after sorting
    for m in meals:
        m.type = mapping.get(m.type, m.type)

    # Build Discord message
    now = datetime.now(ZoneInfo("Europe/Helsinki"))
    wd = ["Ma", "Ti", "Ke", "To", "Pe", "La", "Su"][now.weekday()]
    date_str = f"{wd} {now.day:02d}.{now.month:02d}."

    grouped: dict[str, list[str]] = defaultdict(list)
    seen_order: list[str] = []
    for m in meals:
        if m.type not in grouped:
            seen_order.append(m.type)
        if m.name:
            grouped[m.type].append(m.name.strip())

    lines: list[str] = [f"**{restaurant_name} — Tänään {date_str}**"]
    for t in seen_order:
        items = grouped[t]
        if not items:
            continue
        if t == "Jälkiruoka":
            # lines.append(f"\n**{t}**")
            for n in items:
                lines.append(f"• **{t}**: {n}")
            continue

        lines.append(f"\n**{t}**")
        for n in items:
            lines.append(f"• {n}")

    content = (
        "\n".join(lines).strip()
        if lines
        else f"**{restaurant_name} — Tänään {date_str}**\n(Ei ruokia)"
    )

    # Send (chunk if >2000 chars)
    def _chunks(s: str, limit: int = 2000):
        buf = ""
        for line in s.splitlines(True):  # keepends
            if len(buf) + len(line) > limit and buf:
                yield buf
                buf = line
            else:
                buf += line
        if buf:
            yield buf

    for idx, chunk in enumerate(_chunks(content)):
        payload = {"content": chunk}
        if username:
            payload["username"] = username
        if avatar_url:
            payload["avatar_url"] = avatar_url

        r = requests.post(webhook_url, json=payload, timeout=15)
        if r.status_code not in (200, 204):
            raise RuntimeError(f"Discord webhook error (part {idx + 1}): {r.status_code} {r.text}")


# ----- One-shot fetch & post -----


def _fetch_today_meals() -> list[Meal]:
    resp = requests.get(URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    tab_id = _find_today_tab_id(soup)
    if not tab_id:
        raise RuntimeError('Could not find the "Tänään" tab.')

    container = soup.find("div", id=tab_id)
    if not container:
        raise RuntimeError(f'Could not find tab panel with id="{tab_id}".')

    rows = container.select("div.mealrow")
    if not rows:
        return []

    meals: list[Meal] = []
    for row in rows:
        mtype = row.select_one(".meal-type")
        mname = row.select_one(".meal-name")
        t = mtype.get_text(strip=True) if mtype else ""
        n = mname.get_text(strip=True) if mname else ""
        if t or n:
            meals.append(Meal(type=t, name=n))
    return meals


def _post_today_menu(webhook_url: str, restaurant_name: str = "Sodexo Frami") -> bool:
    try:
        meals = _fetch_today_meals()
        _send_today_meals_to_discord(
            meals, webhook_url=webhook_url, restaurant_name=restaurant_name
        )
        return True
    except Exception as e:
        logger.exception(f"[ERROR] post_today_menu failed: {e}")
        return False


# ----- Scheduler thread -----


def _is_weekday(dt: datetime) -> bool:
    return dt.weekday() < 5  # Mon=0 .. Fri=4


def _next_run_time(
    now: datetime, hour: int, minute: int, tz: ZoneInfo, skip_weekends: bool
) -> datetime:
    """Compute the next local run time in tz (aware)."""
    today_target = datetime(now.year, now.month, now.day, hour, minute, tzinfo=tz)
    candidate = today_target if now < today_target else (today_target + timedelta(days=1))

    # If skipping weekends, advance to next Mon-Fri
    if skip_weekends:
        while not _is_weekday(candidate):
            candidate += timedelta(days=1)
    return candidate


def _sleep_until(target: datetime, tz: ZoneInfo, stop_event: threading.Event) -> bool:
    """Sleep in small chunks until target local time; return False if cancelled."""
    while not stop_event.is_set():
        now = datetime.now(tz)
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            return True
        # Sleep in ≤60s chunks so we can react to stop signals & clock changes
        chunk = 60 if remaining > 60 else max(remaining, 0.1)
        if stop_event.wait(timeout=chunk):
            return False
    return False


def _scheduler_loop(
    webhook_url: str,
    restaurant_name: str,
    hour: int,
    minute: int,
    tz_name: str,
    skip_weekends: bool,
    stop_event: threading.Event,
) -> None:
    tz = ZoneInfo(tz_name)
    while not stop_event.is_set():
        now = datetime.now(tz)
        target = _next_run_time(now, hour, minute, tz, skip_weekends)
        # Sleep until target (unless stopped)
        if not _sleep_until(target, tz, stop_event):
            break

        # If weekend slipped in due to clock changes, guard again
        if skip_weekends and not _is_weekday(datetime.now(tz)):
            continue

        ok = _post_today_menu(webhook_url=webhook_url, restaurant_name=restaurant_name)
        if not ok:
            # Optional: retry once after a brief delay
            if stop_event.wait(timeout=30):
                break
            _post_today_menu(webhook_url=webhook_url, restaurant_name=restaurant_name)

        # Compute next target (typically tomorrow; Monday if Fri and skipping weekends)
        # Loop repeats and calculates again.


def start_sodexo_webhook_thread(
    webhook_url: str,
    *,
    restaurant_name: str = "Sodexo Frami",
    hour: int = 10,
    minute: int = 30,
    tz_name: str = "Europe/Helsinki",
    skip_weekends: bool = True,
) -> tuple[threading.Event, threading.Thread]:
    """
    Start a daemon thread that posts the menu every weekday at <hour>:<minute> local time.
    Returns (stop_event, thread). Call stop_event.set() to stop, then thread.join().
    """
    stop_event = threading.Event()
    th = threading.Thread(
        target=_scheduler_loop,
        args=(webhook_url, restaurant_name, hour, minute, tz_name, skip_weekends, stop_event),
        name="lounasbotti-scheduler",
        daemon=True,
    )
    th.start()
    return stop_event, th


# ----- CLI entry point -----


def main() -> int:
    # Prefer WEBHOOK_URL env so you don't commit secrets
    _post_today_menu(
        webhook_url="https://canary.discord.com/api/webhooks/1434958256668938431/8ZMosz0dbxKcNJGw7NNbEaejzTnayiuCoeJH_ZUwuvSAaz8yXBYCbXY85u9zhhR5r_Ql",
        restaurant_name="Testi Ravintola",
    )
    return 0
    webhook_url = os.getenv("SODEXO_WEBHOOK_URL")
    if not webhook_url:
        logger.error("Set SODEXO_WEBHOOK_URL env var.")
        return 2

    # If you run the script directly, start the scheduler and keep the main thread alive
    stop_event, th = start_sodexo_webhook_thread(
        webhook_url,
        restaurant_name="Sodexo Frami",
        hour=10,
        minute=30,
        tz_name="Europe/Helsinki",
        skip_weekends=True,
    )

    logger.info("Scheduler started (weekdays 10:30 Europe/Helsinki). Press Ctrl+C to stop.")
    try:
        while th.is_alive():
            th.join(timeout=1.0)
    except KeyboardInterrupt:
        logger.info("\nStopping scheduler...")
        stop_event.set()
        th.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
