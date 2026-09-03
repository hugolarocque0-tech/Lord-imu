import os
import time
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from zoneinfo import ZoneInfo

# ============================================================
# CONFIGURATION
# ============================================================

CHECK_INTERVAL = 30
CONFIRMATIONS_REQUIRED = 2
COOLDOWN_SECONDS = 120
BLOCKED_TICKERS = {
    "TPC",
    "BCPC",
}
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PAGES = {
    "PRE-MARKET": "https://stockmarketwatch.com/movers/premarket",
    "MARKET HOURS": "https://stockmarketwatch.com/movers/today",
    "AFTER HOURS": "https://stockmarketwatch.com/movers/afterhours",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
    ),
    "Cache-Control": "no-cache",
}

TIMEZONE = ZoneInfo("America/Toronto")

last_confirmed_ticker = None
last_confirmed_session = None

candidate_ticker = None
candidate_count = 0

last_alert_time = {}
last_alerted_ticker = None


# ============================================================
# SESSION ACTIVE
# ============================================================

def get_active_session():
    now = datetime.now(TIMEZONE)

    hour = now.hour
    minute = now.minute
    total_minutes = hour * 60 + minute

    # 5:00 -> 9:29
    if 300 <= total_minutes <= 569:
        return "PRE-MARKET"

    # 9:30 -> 15:59
    if 570 <= total_minutes <= 959:
        return "MARKET HOURS"

    # 16:00 -> 20:00
    if 960 <= total_minutes <= 1200:
        return "AFTER HOURS"

    return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("ERREUR: variables Telegram manquantes.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID.strip(),
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, data=payload, timeout=15)

        if not response.ok:
            print("Erreur Telegram:", response.status_code, response.text)
            return False

        return True

    except Exception as e:
        print("Erreur Telegram:", e)
        return False


# ============================================================
# EXTRACTION DU #1 GAINER
# ============================================================

def clean_text(text):
    return " ".join(text.split())


def get_number_one(session_name, url):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20,
            params={"_": int(time.time())},
        )

        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        tables = soup.find_all("table")

        for table in tables:
            surrounding_text = ""

            previous = table.find_previous(
                ["h1", "h2", "h3", "h4", "div"]
            )

            if previous:
                surrounding_text = clean_text(
                    previous.get_text(" ", strip=True)
                )

            headers = [
                clean_text(th.get_text(" ", strip=True))
                for th in table.find_all("th")
            ]

            headers_lower = [h.lower() for h in headers]

            has_symbol = any(
                h in ["symb", "symbol", "ticker"]
                for h in headers_lower
            )

            has_change = any(
                "%chg" in h or "chg" in h or "change" in h
                for h in headers_lower
            )

            if not has_symbol or not has_change:
                continue

            parent_text = clean_text(
                table.parent.get_text(" ", strip=True)
            ).lower()

            context = (
                surrounding_text + " " + parent_text[:500]
            ).lower()

            if "gainer" not in context:
                continue

            rows = table.find_all("tr")

            if len(rows) < 2:
                continue

            cells = rows[1].find_all(["td", "th"])

            values = [
                clean_text(cell.get_text(" ", strip=True))
                for cell in cells
            ]

            if len(values) < 3:
                continue

            symbol_index = None
            change_index = None
            last_index = None
            company_index = None
            volume_index = None

            for i, header in enumerate(headers_lower):

                if header in ["symb", "symbol", "ticker"]:
                    symbol_index = i

                elif "%chg" in header or "change" in header:
                    change_index = i

                elif header in ["last", "price"]:
                    last_index = i

                elif "company" in header:
                    company_index = i

                elif "volume" in header:
                    volume_index = i

            if symbol_index is None:
                continue

            ticker = values[symbol_index].upper().strip()

            if not re.fullmatch(r"[A-Z0-9.\-]{1,10}", ticker):
                continue

            change = (
                values[change_index]
                if change_index is not None and change_index < len(values)
                else "N/D"
            )

            price = (
                values[last_index]
                if last_index is not None and last_index < len(values)
                else "N/D"
            )

            company = (
                values[company_index]
                if company_index is not None and company_index < len(values)
                else ""
            )

            volume = (
                values[volume_index]
                if volume_index is not None and volume_index < len(values)
                else "N/D"
            )

            return {
                "ticker": ticker,
                "change": change,
                "price": price,
                "company": company,
                "volume": volume,
                "session": session_name,
                "url": url,
            }

        print(f"[{session_name}] Table Gainers non trouvée.")
        return None

    except Exception as e:
        print(f"[{session_name}] Erreur:", e)
        return None


# ============================================================
# LOGIQUE D'ALERTE
# ============================================================

def process_candidate(stock):
    global candidate_ticker
    global candidate_count
    global last_confirmed_ticker
    global last_confirmed_session
    global last_alerted_ticker

    ticker = stock["ticker"]
    session = stock["session"]
    if ticker in BLOCKED_TICKERS:
        print(f"[{session}] {ticker} est bloqué -> aucune alerte")
        return
    # Reset si on change de session
    if last_confirmed_session != session:
        print(f"Changement de session -> {session}")

        last_confirmed_session = session
        last_confirmed_ticker = None
        candidate_ticker = None
        candidate_count = 0
        last_alerted_ticker = None

    # Nouveau candidat
    if ticker != candidate_ticker:
        candidate_ticker = ticker
        candidate_count = 1

        print(
            f"[{session}] Candidat #1: "
            f"{ticker} (1/{CONFIRMATIONS_REQUIRED})"
        )

        return

    # Même candidat une autre fois
    candidate_count += 1

    print(
        f"[{session}] Candidat #1: "
        f"{ticker} ({candidate_count}/{CONFIRMATIONS_REQUIRED})"
    )

    if candidate_count < CONFIRMATIONS_REQUIRED:
        return

    # Premier #1 confirmé de la session
    if last_confirmed_ticker is None:
        last_confirmed_ticker = ticker

        print(
            f"[{session}] Premier #1 confirmé enregistré: "
            f"{ticker}"
        )

        return

    # Toujours le même #1
    if ticker == last_confirmed_ticker:
        return

    old_ticker = last_confirmed_ticker
    last_confirmed_ticker = ticker

    now = datetime.now(TIMEZONE)

    # Cooldown anti-spam
    if ticker in last_alert_time:
        elapsed = (
            now - last_alert_time[ticker]
        ).total_seconds()

        if elapsed < COOLDOWN_SECONDS:
            print(
                f"[{session}] {ticker} reprend #1 mais cooldown actif "
                f"({int(elapsed)} sec)"
            )
            return

    # Évite répétition inutile
    if ticker == last_alerted_ticker:
        return

    message = (
        f"🚨 <b>NOUVEAU #1 GAINER</b>\n\n"
        f"🔥 <b>${ticker}</b>\n"
        f"📈 Variation : <b>{stock['change']}</b>\n"
        f"💵 Prix : <b>${stock['price']}</b>\n"
        f"📊 Volume : <b>{stock['volume']}</b>\n"
    )

    if stock["company"]:
        message += f"🏢 {stock['company']}\n"

    message += (
        f"\n⏰ <b>{session}</b>\n"
        f"🕐 {now.strftime('%H:%M:%S')} ET\n\n"
        f"Ancien #1 : ${old_ticker}\n"
        f"🟢 Nouveau #1 : <b>${ticker}</b>\n\n"
        f"{stock['url']}"
    )

    if send_telegram(message):
        last_alert_time[ticker] = now
        last_alerted_ticker = ticker

        print(
            f"ALERTE ENVOYÉE: "
            f"[{session}] {old_ticker} -> {ticker}"
        )


# ============================================================
# PROGRAMME PRINCIPAL
# ============================================================

def main():
    print("==========================================")
    print(" StockMarketWatch #1 Gainer Monitor V2")
    print(" Pre-Market commence à 5:00 ET")
    print(" Confirmation x2")
    print(" Cooldown 2 minutes")
    print("==========================================")

    send_telegram(
        "✅ <b>StockWatch V2 démarré</b>\n\n"
        "🌅 05:00–09:29 : Pre-Market\n"
        "🔔 09:30–15:59 : Market Hours\n"
        "🌙 16:00–20:00 : After Hours\n\n"
        "Confirmation du #1 sur 2 vérifications.\n"
        "Anti-spam activé."
    )

    while True:
        session = get_active_session()

        if session is None:
            now = datetime.now(TIMEZONE)

            print(
                f"[{now.strftime('%H:%M:%S')}] "
                "Hors horaire -> aucune surveillance"
            )

            time.sleep(CHECK_INTERVAL)
            continue

        url = PAGES[session]
        stock = get_number_one(session, url)

        if stock:
            print(
                f"[{datetime.now(TIMEZONE).strftime('%H:%M:%S')}] "
                f"{session}: "
                f"#{stock['ticker']} {stock['change']}"
            )

            process_candidate(stock)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
