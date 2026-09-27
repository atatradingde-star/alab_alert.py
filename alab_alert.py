import os
import json
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import requests


# ============================================================
# KONFIGURATION
# ============================================================

TICKER = "ALAB"
EMAIL = "ata.trading.de@gmail.com"

BB_PERIOD = 30
BB_STD = 2.0

# Datei speichert, welcher BUY bereits gemeldet wurde.
STATE_FILE = Path("alab_state.json")

# Wie viele Tage Kursdaten geladen werden.
HISTORY_DAYS = 180


# ============================================================
# KURSDATEN LADEN
# ============================================================

def get_alab_data():
    """
    Holt tägliche ALAB-Kurse.

    Wir verwenden 180 Tage Historie, damit immer genügend
    Daten für den 30-Tage-Bollinger-Indikator vorhanden sind.
    """

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{TICKER}?range={HISTORY_DAYS}d&interval=1d"
    )

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    result = data["chart"]["result"][0]

    timestamps = result["timestamp"]
    quotes = result["indicators"]["quote"][0]

    closes = quotes["close"]

    rows = []

    for timestamp, close in zip(timestamps, closes):

        if close is None:
            continue

        date = datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc
        ).date()

        rows.append({
            "Date": date,
            "Close": float(close)
        })

    df = pd.DataFrame(rows)

    if df.empty:
        raise RuntimeError("Keine ALAB-Kursdaten erhalten.")

    df = df.drop_duplicates(
        subset=["Date"]
    )

    df = df.sort_values("Date")

    df = df.reset_index(drop=True)

    return df


# ============================================================
# BOLLINGER 30 / 2.0
# ============================================================

def calculate_strategy(df):
    """
    Bollinger-Strategie:

    Period:
        30

    Standardabweichung:
        2.0

    BUY:
        Close < Lower Band
        und vorher OUT

    SELL:
        Close > SMA 30
        und vorher IN

    HOLD:
        alles andere
    """

    df = df.copy()

    # --------------------------------------------------------
    # SMA 30
    # --------------------------------------------------------

    df["SMA_30"] = (
        df["Close"]
        .rolling(BB_PERIOD)
        .mean()
    )

    # --------------------------------------------------------
    # Standardabweichung
    #
    # pandas rolling().std() verwendet standardmäßig
    # ddof=1.
    #
    # Das entspricht der STDEV()-Berechnung in Google Sheets.
    # --------------------------------------------------------

    df["Std_30"] = (
        df["Close"]
        .rolling(BB_PERIOD)
        .std()
    )

    # --------------------------------------------------------
    # Bollinger Bands
    # --------------------------------------------------------

    df["Upper_Band"] = (
        df["SMA_30"]
        + BB_STD * df["Std_30"]
    )

    df["Lower_Band"] = (
        df["SMA_30"]
        - BB_STD * df["Std_30"]
    )

    # --------------------------------------------------------
    # Position / Signal
    # --------------------------------------------------------

    position = "OUT"

    signals = []
    positions = []

    for _, row in df.iterrows():

        signal = "WAIT"

        # Noch keine 30 Kurse
        if pd.isna(row["SMA_30"]):

            signal = "WAIT"

        else:

            close = row["Close"]
            lower = row["Lower_Band"]
            sma = row["SMA_30"]

            # ------------------------------------------------
            # BUY
            # ------------------------------------------------

            if position == "OUT" and close < lower:

                signal = "BUY"
                position = "IN"

            # ------------------------------------------------
            # SELL
            # ------------------------------------------------

            elif position == "IN" and close > sma:

                signal = "SELL"
                position = "OUT"

            # ------------------------------------------------
            # HOLD
            # ------------------------------------------------

            else:

                signal = "HOLD"

        signals.append(signal)
        positions.append(position)

    df["Signal"] = signals
    df["Position"] = positions

    return df


# ============================================================
# STATE LADEN
# ============================================================

def load_state():

    if not STATE_FILE.exists():
        return {}

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return {}


# ============================================================
# STATE SPEICHERN
# ============================================================

def save_state(state):

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
            f,
            indent=2
        )


# ============================================================
# E-MAIL SENDEN
# ============================================================

def send_email(row):

    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.environ["SMTP_PORT"])

    smtp_user = os.environ["SMTP_USER"]
    smtp_password = os.environ["SMTP_PASSWORD"]

    date = row["Date"]

    close = float(row["Close"])
    sma = float(row["SMA_30"])
    std = float(row["Std_30"])
    upper = float(row["Upper_Band"])
    lower = float(row["Lower_Band"])

    subject = (
        "ALAB BUY SIGNAL | Bollinger 30/2.0"
    )

    body = f"""
ALAB TRADING SIGNAL

SIGNAL: BUY

Datum:
{date}

ALAB Close:
${close:.2f}

SMA 30:
${sma:.2f}

StdAbw 30:
{std:.2f}

Upper Band:
${upper:.2f}

Lower Band:
${lower:.2f}


REGEL:

Close < Lower Bollinger Band


Position:

IN


Strategie:

Bollinger Band 30 / 2.0


Automatischer Trading-Alarm.

Keine Anlageberatung.
"""

    message = EmailMessage()

    message["From"] = smtp_user
    message["To"] = EMAIL
    message["Subject"] = subject

    message.set_content(body)

    context = ssl.create_default_context()

    with smtplib.SMTP(
        smtp_host,
        smtp_port
    ) as server:

        server.starttls(
            context=context
        )

        server.login(
            smtp_user,
            smtp_password
        )

        server.send_message(message)


# ============================================================
# HAUPTPROGRAMM
# ============================================================

def main():

    print("=" * 60)
    print("ALAB Bollinger 30/2.0 Alert")
    print("=" * 60)

    # --------------------------------------------------------
    # Daten laden
    # --------------------------------------------------------

    print("\nLade ALAB-Kursdaten...")

    df = get_alab_data()

    print(
        f"{len(df)} Tageskurse geladen."
    )

    # --------------------------------------------------------
    # Strategie berechnen
    # --------------------------------------------------------

    df = calculate_strategy(df)

    # --------------------------------------------------------
    # Letzten Kurs bestimmen
    # --------------------------------------------------------

    latest = df.iloc[-1]

    print("\nLETZTER KURS")
    print("-" * 60)

    print(
        f"Datum:       {latest['Date']}"
    )

    print(
        f"Close:       ${latest['Close']:.2f}"
    )

    if pd.notna(latest["SMA_30"]):

        print(
            f"SMA 30:      ${latest['SMA_30']:.2f}"
        )

        print(
            f"StdAbw 30:   {latest['Std_30']:.2f}"
        )

        print(
            f"Upper Band:  ${latest['Upper_Band']:.2f}"
        )

        print(
            f"Lower Band:  ${latest['Lower_Band']:.2f}"
        )

    print(
        f"Signal:      {latest['Signal']}"
    )

    print(
        f"Position:    {latest['Position']}"
    )

    # --------------------------------------------------------
    # State laden
    # --------------------------------------------------------

    state = load_state()

    # --------------------------------------------------------
    # Nur BUY interessiert uns
    # --------------------------------------------------------

    if latest["Signal"] != "BUY":

        print(
            "\nKein neuer BUY."
        )

        print(
            f"Aktueller Zustand: {latest['Signal']}"
        )

        return

    # --------------------------------------------------------
    # BUY-Key
    # --------------------------------------------------------

    buy_key = (
        f"{latest['Date']}_"
        f"{latest['Close']:.8f}"
    )

    # --------------------------------------------------------
    # Prüfen, ob BUY bereits verschickt wurde
    # --------------------------------------------------------

    if state.get("last_buy") == buy_key:

        print(
            "\nBUY wurde bereits gemeldet."
        )

        return

    # --------------------------------------------------------
    # E-Mail senden
    # --------------------------------------------------------

    print(
        "\nNEUER BUY!"
    )

    print(
        "Sende E-Mail..."
    )

    send_email(latest)

    # --------------------------------------------------------
    # State speichern
    # --------------------------------------------------------

    state["last_buy"] = buy_key
    state["last_buy_date"] = str(
        latest["Date"]
    )

    save_state(state)

    print(
        "\nE-Mail erfolgreich verschickt."
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
