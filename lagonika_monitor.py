#!/usr/bin/env python3
"""Watch lagonika.gr for new deals (prosfores) and email each new one."""

from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import smtplib
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PAGE_URL = "https://www.lagonika.gr/?dtype=all"
FEED_URL = "https://www.lagonika.gr/feed/"
DEAL_URL = "https://www.lagonika.gr/prosfores/{slug}/"
DEFAULT_NOTIFY_EMAIL = "kintokanndev@gmail.com"
MAX_SEEN = 500

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)


def app_dir() -> Path:
    return Path(__file__).resolve().parent


def state_file() -> Path:
    return app_dir() / "seen.json"


@dataclass
class Deal:
    id: str
    title: str
    url: str
    price: str
    store: str
    image: str
    store_link: str
    discount_code: str
    category: str
    snippet: str
    date: str
    expired: bool = False

    def summary(self) -> str:
        lines = [self.title]
        if self.price:
            lines.append(f"Τιμή: {self.price}")
        if self.store:
            lines.append(f"Κατάστημα: {self.store}")
        if self.category:
            lines.append(f"Κατηγορία: {self.category}")
        if self.discount_code:
            lines.append(f"Κωδικός: {self.discount_code}")
        if self.snippet:
            lines.append("")
            lines.append(self.snippet)
        lines.append("")
        lines.append(f"Lagonika: {self.url}")
        if self.store_link:
            lines.append(f"Κατάστημα: {self.store_link}")
        return "\n".join(lines)


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def fetch_page(url: str, timeout: int = 20, attempts: int = 3) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "el-GR,el;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml",
        },
    )
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="ignore")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            if attempt == attempts:
                raise
            print(f"Fetch {url} failed ({exc}), retry {attempt}/{attempts - 1}", file=sys.stderr)
            time.sleep(5 * attempt)
    raise AssertionError("unreachable")


def html_to_text(raw: str, limit: int = 400) -> str:
    text = re.sub(r"<br\s*/?>|</p>|</li>", "\n", raw or "", flags=re.I)
    text = html_lib.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def format_price(price: object, pricetype: object) -> str:
    try:
        value = float(price)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    amount = f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{amount}%" if pricetype == "percent" else f"{amount}€"


def parse_page_deals(page_html: str) -> list[Deal]:
    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page_html, re.S
    )
    if not match:
        raise ValueError("__NEXT_DATA__ not found")
    posts = json.loads(match.group(1))["props"]["pageProps"]["posts"]
    deals: list[Deal] = []
    for post in posts:
        images = post.get("image") or []
        store = post.get("store") or {}
        category = post.get("category") or {}
        deals.append(
            Deal(
                id=str(post["id"]),
                title=(post.get("title") or "").strip(),
                url=DEAL_URL.format(slug=post.get("slug", "")),
                price=format_price(post.get("price"), post.get("pricetype")),
                store=store.get("name", "") if isinstance(store, dict) else "",
                image=images[0].get("url", "") if images else "",
                store_link=post.get("affiliateLink") or "",
                discount_code=(post.get("discountcode") or "").strip(),
                category=category.get("name", "") if isinstance(category, dict) else "",
                snippet=html_to_text(post.get("content") or ""),
                date=post.get("listDate") or post.get("createDate") or "",
                expired=bool(post.get("expired")),
            )
        )
    return deals


def parse_feed_deals(feed_xml: str) -> list[Deal]:
    root = ET.fromstring(feed_xml)
    deals: list[Deal] = []
    for item in root.iter("item"):
        guid = item.findtext("guid") or ""
        thumb = item.find("thumbnail")
        if thumb is None:
            thumb = item.find("{http://search.yahoo.com/mrss/}thumbnail")
        pub = item.findtext("pubDate") or ""
        try:
            date = parsedate_to_datetime(pub).isoformat()
        except (TypeError, ValueError):
            date = ""
        deals.append(
            Deal(
                id=guid.rsplit("-", 1)[-1],
                title=(item.findtext("title") or "").strip(),
                url=item.findtext("link") or "",
                price="",
                store="",
                image=(thumb.get("url") or thumb.text or "") if thumb is not None else "",
                store_link="",
                discount_code="",
                category="",
                snippet=html_to_text(item.findtext("description") or ""),
                date=date,
            )
        )
    return deals


def fetch_deals() -> list[Deal]:
    try:
        deals = parse_page_deals(fetch_page(PAGE_URL))
        if deals:
            return deals
        raise ValueError("no posts in page data")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
        print(f"Page parse failed ({exc}); falling back to RSS feed", file=sys.stderr)
    return parse_feed_deals(fetch_page(FEED_URL))


def load_state() -> dict:
    path = state_file()
    if not path.exists():
        return {"initialized": False, "ids": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"initialized": False, "ids": []}


def save_state(state: dict) -> None:
    state["ids"] = state["ids"][-MAX_SEEN:]
    state_file().write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def keyword_match(deal: Deal, keywords: list[str]) -> bool:
    if not keywords:
        return True
    title = deal.title.lower()
    return any(k in title for k in keywords)


def build_html(deal: Deal) -> str:
    esc = html_lib.escape
    rows = []
    if deal.price:
        rows.append(f"<b style='font-size:20px;color:#d32f2f'>{esc(deal.price)}</b>")
    meta = " · ".join(esc(x) for x in (deal.store, deal.category) if x)
    if meta:
        rows.append(f"<span style='color:#666'>{meta}</span>")
    if deal.discount_code:
        rows.append(
            "Κωδικός: <code style='background:#fff3cd;padding:2px 6px;"
            f"border-radius:4px;font-size:15px'>{esc(deal.discount_code)}</code>"
        )
    image = (
        f"<img src='{esc(deal.image)}' alt='' style='max-width:100%;max-height:320px;"
        "border-radius:8px;margin-bottom:12px'>"
        if deal.image
        else ""
    )
    button = (
        "display:inline-block;padding:10px 16px;margin:4px 8px 4px 0;border-radius:6px;"
        "text-decoration:none;color:#fff;font-weight:bold;background:{bg}"
    )
    buttons = f"<a href='{esc(deal.url)}' style='{button.format(bg='#1976d2')}'>Δες στο Lagonika</a>"
    if deal.store_link:
        buttons += (
            f"<a href='{esc(deal.store_link)}' style='{button.format(bg='#388e3c')}'>"
            "Πήγαινε στο κατάστημα</a>"
        )
    snippet = esc(deal.snippet).replace("\n", "<br>")
    return (
        "<div style='font-family:Arial,sans-serif;max-width:600px'>"
        f"{image}<h2 style='margin:0 0 8px'>{esc(deal.title)}</h2>"
        f"<p style='margin:0 0 12px;line-height:1.8'>{'<br>'.join(rows)}</p>"
        f"<p style='color:#333;line-height:1.5'>{snippet}</p>"
        f"<p>{buttons}</p></div>"
    )


def send_email(
    subject: str,
    text_body: str,
    html_body: str,
    *,
    to_email: str,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
) -> None:
    message = MIMEMultipart("alternative")
    message["From"] = smtp_user
    message["To"] = to_email
    message["Subject"] = subject
    message.attach(MIMEText(text_body, "plain", "utf-8"))
    message.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, [to_email], message.as_string())


def email_deal(deal: Deal, to_email: str, *, test: bool = False) -> None:
    prefix = "[TEST] " if test else ""
    price = f" — {deal.price}" if deal.price else ""
    send_email(
        f"{prefix}🔥 Lagonika: {deal.title}{price}",
        deal.summary(),
        build_html(deal),
        to_email=to_email,
        smtp_host=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        smtp_user=os.environ["SMTP_USER"],
        smtp_password=os.environ["SMTP_PASSWORD"].replace(" ", ""),
    )


def run_check(*, to_email: str, keywords: list[str], dry_run: bool) -> int:
    try:
        deals = fetch_deals()
    except (HTTPError, URLError, TimeoutError, OSError, ET.ParseError) as exc:
        # Lagonika is occasionally unreachable; skip quietly, the next run catches up.
        print(f"Could not reach lagonika.gr ({exc}); will try again next run.")
        return 0
    print(f"Fetched {len(deals)} deals")
    state = load_state()
    seen = set(state.get("ids", []))

    if not state.get("initialized"):
        print("First run: remembering current deals, no emails sent.")
        if dry_run:
            for deal in deals:
                print(f"  {deal.id} | {deal.price or '-'} | {deal.store or '-'} | {deal.title}")
            return 0
        state = {"initialized": True, "ids": [d.id for d in reversed(deals)]}
        save_state(state)
        return 0

    new_deals = [d for d in deals if d.id not in seen]
    new_deals.sort(key=lambda d: d.date)
    print(f"{len(new_deals)} new deal(s)")

    failures = 0
    for deal in new_deals:
        if deal.expired or not keyword_match(deal, keywords):
            reason = "expired" if deal.expired else "no keyword match"
            print(f"  skip ({reason}): {deal.title}")
        elif dry_run:
            print(f"  would email: {deal.id} | {deal.price or '-'} | {deal.title}")
            continue
        else:
            try:
                email_deal(deal, to_email)
                print(f"  emailed: {deal.title}")
            except (smtplib.SMTPException, OSError) as exc:
                failures += 1
                print(f"  email failed, will retry next run: {exc}", file=sys.stderr)
                continue
        if not dry_run:
            state["ids"].append(deal.id)

    if not dry_run and new_deals:
        save_state(state)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Lagonika.gr new deal email notifier")
    parser.add_argument("--env", type=Path, default=app_dir() / ".env", help="Path to .env file")
    parser.add_argument("--dry-run", action="store_true", help="Show new deals without emailing or saving")
    parser.add_argument("--test-email", action="store_true", help="Email the newest deal as a test")
    parser.add_argument("--reset", action="store_true", help="Forget seen deals")
    parser.add_argument("--email", default=None, help="Email address to notify")
    args = parser.parse_args()

    load_dotenv(args.env)
    to_email = args.email or os.environ.get("NOTIFY_EMAIL") or DEFAULT_NOTIFY_EMAIL
    keywords = [k.strip().lower() for k in os.environ.get("KEYWORDS", "").split(",") if k.strip()]

    if args.reset:
        if state_file().exists():
            state_file().unlink()
        print("Seen deals cleared.")
        return 0

    needs_smtp = not args.dry_run
    if needs_smtp and (not os.environ.get("SMTP_USER") or not os.environ.get("SMTP_PASSWORD")):
        print("Missing SMTP_USER or SMTP_PASSWORD (set them in .env or GitHub Secrets).")
        return 2

    if args.test_email:
        deal = fetch_deals()[0]
        email_deal(deal, to_email, test=True)
        print(f"Test email sent to {to_email}: {deal.title}")
        return 0

    return run_check(to_email=to_email, keywords=keywords, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
