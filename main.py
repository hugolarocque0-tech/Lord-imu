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

CHECK_INTERVAL = 30  # vérification toutes les 30 secondes

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

# Garde en mémoire le dernier #1 de chaque session
last_number_one = {
    "PRE-MARKET": None,
    "MARKET HOURS": None,
    "AFTER HOURS": None,
}

# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("ERREUR: variables Telegram manquantes.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, data=payload, timeout=15)

        if not response.ok:
            print("Erreur Telegram:", response.status_code, response.text)

    except Exception as e:
        print("Erreur Telegram:", e)

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
            params={"_": int(time.time())},  # évite certaines mises en cache
        )

        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Cherche toutes les tables de la page
        tables = soup.find_all("table")

        for table in tables:

            # Texte près de la table pour identifier Gainers
            surrounding_text = ""

            previous = table.find_previous(
                ["h1", "h2", "h3", "h4", "div"]
            )

            if previous:
                surrounding_text = clean_text(previous.get_text(" ", strip=True))

            headers = [
                clean_text(th.get_text(" ", strip=True))
                for th in table.find_all("th")
            ]

            headers_lower = [h.lower() for h in headers]

            # Table attendue :
            # %Chg | Last | Symb | Company | Volume

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

            # Priorité aux tables associées aux gainers
            parent_text = clean_text(
                table.parent.get_text(" ", strip=True)
            ).lower()

            context = (surrounding_text + " " + parent_text[:500]).lower()

            if "gainer" not in context:
                continue

            rows = table.find_all("tr")

            if len(rows) < 2:
                continue

            # Première ligne de données = #1
            cells = rows[1].find_all(["td", "th"])

            values = [
                clean_text(cell.get_text(" ", strip=True))
                for cell in cells
            ]

            if len(values) < 3:
                continue

            # Repère les colonnes dynamiquement
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

            # Validation simple d'un ticker US
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
# NOTIFICATION
# ============================================================

def number_one_changed(stock):
    session = stock["session"]
    ticker = stock["ticker"]

    previous = last_number_one[session]

    # Premier passage :
    # on enregistre simplement le #1 actuel sans envoyer d'alerte.
    if previous is None:
        last_number_one[session] = ticker
        print(
            f"[{session}] Premier #1 enregistré: "
            f"{ticker} {stock['change']}"
        )
        return

    # Toujours le même #1 → aucune notification
    if ticker == previous:
        return

    # Nouveau #1
    last_number_one[session] = ticker

    now = datetime.now(TIMEZONE)

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
        f"Ancien #1 : ${previous}\n"
        f"🟢 Nouveau #1 : <b>${ticker}</b>\n\n"
        f"{stock['url']}"
    )

    send_telegram(message)

    print(
        f"ALERTE: [{session}] "
        f"{previous} → {ticker} ({stock['change']})"
    )

# ============================================================
# PROGRAMME PRINCIPAL
# ============================================================

def main():

    print("==========================================")
    print(" StockMarketWatch #1 Gainer Monitor")
    print(" Vérification toutes les 30 secondes")
    print("==========================================")

    send_telegram(
        "✅ <b>StockMarketWatch Monitor démarré</b>\n\n"
        "Surveillance active :\n"
        "🌅 Pre-Market\n"
        "🔔 Market Hours\n"
        "🌙 After Hours\n\n"
        "Vérification toutes les 30 secondes."
    )

    while True:

        for session_name, url in PAGES.items():

            stock = get_number_one(session_name, url)

            if stock:
                print(
                    f"[{datetime.now(TIMEZONE).strftime('%H:%M:%S')}] "
                    f"{session_name}: "
                    f"#{stock['ticker']} {stock['change']}"
                )

                number_one_changed(stock)

            time.sleep(2)

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
