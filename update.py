#!/usr/bin/env python3
"""
Automatyczny odczyt oficjalnych danych PKW dla referendum nr 4503.
Skrypt jest celowo defensywny:
- nie zgaduje liczb,
- brak danych pozostawia jako null,
- przy błędzie nie usuwa ostatnich poprawnych wyników,
- korzysta wyłącznie z oficjalnego serwisu wybory.gov.pl.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

BASE = "https://wybory.gov.pl/referendum_lokalne_2024_2029/pl/4503"
URLS = {
    "home": f"{BASE}/home",
    "wyniki": f"{BASE}/wyniki",
    "frekwencja": f"{BASE}/frekwencja",
}
DATA_FILE = Path(__file__).with_name("data.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; ReferendumTrabkiPublicInfo/2.0; "
        "+https://referendum-trabki-wielkie.github.io/referendum-trabki-wielkie/)"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.7",
}

EMPTY = {
    "referendum_id": 4503,
    "source": "Państwowa Komisja Wyborcza",
    "source_home": URLS["home"],
    "source_results": URLS["wyniki"],
    "source_turnout": URLS["frekwencja"],
    "source_ok": True,
    "results_available": False,
    "turnout": None,
    "eligible": None,
    "participants": None,
    "valid_votes": None,
    "yes_votes": None,
    "no_votes": None,
    "election_participants": None,
    "threshold_3_5": None,
    "commissions_total": None,
    "commissions_reported": None,
    "threshold_reached": None,
    "yes_majority": None,
    "updated_at": None,
    "last_error": None,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_previous() -> dict:
    if DATA_FILE.exists():
        try:
            return {**EMPTY, **json.loads(DATA_FILE.read_text(encoding="utf-8"))}
        except Exception:
            pass
    return dict(EMPTY)


def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    # PKW powinno zwracać UTF-8; requests czasem zgaduje inaczej.
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def plain_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    text = text.replace("\u00a0", " ").replace("\u202f", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text


def to_int(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    digits = re.sub(r"\D", "", s)
    return int(digits) if digits else None


def find_int(text: str, pattern: str, flags=re.I | re.S) -> Optional[int]:
    m = re.search(pattern, text, flags)
    return to_int(m.group(1)) if m else None


def find_all_ints(text: str, pattern: str, flags=re.I | re.S) -> list[int]:
    vals = []
    for m in re.finditer(pattern, text, flags):
        v = to_int(m.group(1))
        if v is not None:
            vals.append(v)
    return vals


def find_percent(text: str, pattern: str) -> Optional[str]:
    m = re.search(pattern, text, re.I | re.S)
    if not m:
        return None
    return m.group(1).replace(".", ",") + "%"


def parse_commissions(html: str) -> tuple[Optional[int], Optional[int]]:
    """
    Total: unikalne linki prowadzące do komisji obwodowych.
    Reported: wiersze komisji, w których występuje wartość procentowa frekwencji.
    """
    soup = BeautifulSoup(html, "html.parser")
    links = {}
    reported = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if "/organy_wyborcze/komisja_obwodowa/" not in href:
            continue
        key = href.split("?")[0]
        links[key] = True
        tr = a.find_parent("tr")
        if tr:
            row = tr.get_text(" ", strip=True).replace("\u00a0", " ")
            if re.search(r"\b\d{1,3}[,.]\d{1,2}\s*%", row):
                reported.add(key)

    total = len(links) or None
    rep = len(reported) if total is not None else None
    return total, rep


def parse_results(html: str) -> dict:
    text = plain_text(html)
    out = {}

    # Pierwsza wartość dotyczy wyboru odwoływanego organu, druga referendum.
    election_vals = find_all_ints(
        text,
        r"W wyborze.{0,450}?liczba osób, które wzięły udział w głosowaniu"
        r".{0,120}?wyniosła\s*([0-9][0-9 ]*)",
    )
    if election_vals:
        out["election_participants"] = election_vals[0]

    out["threshold_3_5"] = find_int(
        text,
        r"3/5 liczby biorących udział.{0,180}?wynosi\s*([0-9][0-9 ]*)",
    )

    out["eligible"] = find_int(
        text,
        r"Liczba osób uprawnionych do głosowania wyniosła\s*([0-9][0-9 ]*)",
    )

    participant_vals = find_all_ints(
        text,
        r"Liczba osób, które wzięły udział w głosowaniu"
        r".{0,100}?wyniosła\s*([0-9][0-9 ]*)",
    )
    if participant_vals:
        # Na stronie wyników pierwsza wartość może pochodzić z wyboru organu,
        # dlatego bezpiecznie bierzemy ostatnią.
        out["participants"] = participant_vals[-1]

    out["turnout"] = find_percent(
        text, r"Frekwencja wyniosła\s*([0-9]{1,3}[,.][0-9]{1,2})\s*%"
    )
    out["valid_votes"] = find_int(
        text, r"Liczba głosów ważnych wyniosła\s*([0-9][0-9 ]*)"
    )
    out["yes_votes"] = find_int(
        text,
        r"Liczba głosów za odwołaniem organu.{0,160}?wyniosła\s*([0-9][0-9 ]*)",
    )
    out["no_votes"] = find_int(
        text,
        r"Liczba głosów przeciw odwołaniu organu.{0,160}?wyniosła\s*([0-9][0-9 ]*)",
    )
    return {k: v for k, v in out.items() if v is not None}


def parse_turnout(html: str) -> dict:
    text = plain_text(html)
    out = {}
    out["turnout"] = find_percent(
        text, r"(?:^|\n)Frekwencja(?: wyniosła)?\s*([0-9]{1,3}[,.][0-9]{1,2})\s*%"
    )
    out["eligible"] = find_int(
        text, r"Liczba osób uprawnionych do głosowania\s*([0-9][0-9 ]*)"
    )
    # Na stronie frekwencji łączna liczba kart ważnych jest najbardziej
    # użyteczna jako liczba biorących udział.
    cards = find_all_ints(
        text, r"Liczba kart ważnych\s*([0-9][0-9 ]*)"
    )
    if cards:
        out["participants"] = max(cards)

    total, reported = parse_commissions(html)
    if total is not None:
        out["commissions_total"] = total
        out["commissions_reported"] = reported
    return {k: v for k, v in out.items() if v is not None}


def semantic_payload(d: dict) -> dict:
    """Pola, których zmiana ma znaczenie dla mieszkańca."""
    keys = [
        "source_ok", "results_available", "turnout", "eligible", "participants",
        "valid_votes", "yes_votes", "no_votes", "election_participants",
        "threshold_3_5", "commissions_total", "commissions_reported",
        "threshold_reached", "yes_majority", "last_error",
    ]
    return {k: d.get(k) for k in keys}


def main():
    previous = load_previous()
    current = {**previous}
    current["source_ok"] = True
    current["last_error"] = None

    errors = []
    successes = 0

    # Wyniki
    try:
        html = fetch(URLS["wyniki"])
        current.update(parse_results(html))
        successes += 1
    except Exception as e:
        errors.append(f"wyniki: {type(e).__name__}: {e}")

    # Frekwencja + komisje
    try:
        html = fetch(URLS["frekwencja"])
        # Dane z wyniki mają pierwszeństwo, dlatego uzupełniamy głównie braki,
        # z wyjątkiem komisji, które aktualizujemy zawsze.
        turnout_data = parse_turnout(html)
        for k, v in turnout_data.items():
            if k.startswith("commissions_") or current.get(k) is None:
                current[k] = v
        successes += 1
    except Exception as e:
        errors.append(f"frekwencja: {type(e).__name__}: {e}")

    if successes == 0:
        # Nie niszczymy ostatnich poprawnych liczb.
        current["source_ok"] = False
        current["last_error"] = " | ".join(errors)[:1000]
    elif errors:
        # Częściowy błąd nie kasuje danych, ale zapisujemy go diagnostycznie.
        current["last_error"] = " | ".join(errors)[:1000]

    current["results_available"] = (
        current.get("yes_votes") is not None
        or current.get("no_votes") is not None
        or current.get("valid_votes") is not None
    )

    p = current.get("participants")
    t = current.get("threshold_3_5")
    current["threshold_reached"] = (p >= t) if isinstance(p, int) and isinstance(t, int) else None

    y = current.get("yes_votes")
    vv = current.get("valid_votes")
    current["yes_majority"] = (y * 2 > vv) if isinstance(y, int) and isinstance(vv, int) and vv > 0 else None

    # updated_at oznacza moment zmiany informacji widocznej dla mieszkańca,
    # nie każdą techniczną próbę sprawdzenia.
    if semantic_payload(current) != semantic_payload(previous) or not previous.get("updated_at"):
        current["updated_at"] = now_iso()

    DATA_FILE.write_text(
        json.dumps(current, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps({
        "source_ok": current["source_ok"],
        "results_available": current["results_available"],
        "turnout": current.get("turnout"),
        "participants": current.get("participants"),
        "yes_votes": current.get("yes_votes"),
        "no_votes": current.get("no_votes"),
        "commissions": [current.get("commissions_reported"), current.get("commissions_total")],
        "updated_at": current.get("updated_at"),
        "errors": errors,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
