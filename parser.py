import re
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import requests
import urllib3
from bs4 import BeautifulSoup

from config import DISTRICTS

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

STOP_MARKERS = (
    "ИНФОРМАЦИЯ О ПОГОДЕ",
    "ВОЗОБНОВЛЕН СВОБОДНЫЙ ДОСТУП",
    "СОСТОЯНИЕ АВТОДОРОГ",
)


def to_punycode_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.hostname:
        return url
    host = parts.hostname.encode("idna").decode("ascii")
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def extract_clean_lines(box) -> list[str]:
    for tag in box(["script", "style", "noscript"]):
        tag.decompose()

    for br in box.find_all("br"):
        br.replace_with("\n")

    for td in box.find_all(["td", "th"]):
        td.append(" | ")

    for block in box.find_all(["p", "div", "tr", "li", "h1", "h2", "h3", "h4", "table"]):
        if block.name in ("p", "div") and block.find_parent(["td", "th"]):
            continue
        block.insert_before("\n")
        block.append("\n")

    raw_text = box.get_text().replace("\xa0", " ")
    lines = []
    for line in raw_text.splitlines():
        clean = re.sub(r"\s+", " ", line).strip().strip("|").strip()
        if clean:
            lines.append(clean)
    return lines


def parse_page(html: str, source: dict) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    box = soup.select_one("div.white-box.padding-box") or soup.body or soup
    lines = extract_clean_lines(box)

    start_idx = next(
        (i for i, line in enumerate(lines) if "ИНФОРМАЦИЯ ОБ АКТИРОВКАХ" in line.upper()),
        None,
    )
    if start_idx is None:
        raise ValueError("Не найден заголовок блока «ИНФОРМАЦИЯ ОБ АКТИРОВКАХ»")

    section_lines = []
    for line in lines[start_idx + 1 :]:
        if any(marker in line.upper() for marker in STOP_MARKERS):
            break
        section_lines.append(line)

    if not section_lines:
        raise ValueError("Блок с актировками пуст")

    section_text = " ".join(section_lines)

    date_match = re.search(r"\b(\d{2}\.\d{2}\.\d{4})\b", section_text)
    if not date_match:
        before_text = " ".join(lines[max(0, start_idx - 3) : start_idx])
        before_dates = re.findall(r"\b(\d{2}\.\d{2}\.\d{4})\b", before_text)
        date_str = before_dates[-1] if before_dates else None
    else:
        date_str = date_match.group(1)

    if not date_str:
        raise ValueError("В решении не найдена дата")

    decision_date = datetime.strptime(date_str, "%d.%m.%Y").date()

    shift_match = re.search(
        r"\b([12])\s*(?:[-–—]\s*)?(?:ой|й|ая|я|ую|ю)?\s*смен",
        section_text,
        re.I,
    )
    if not shift_match:
        raise ValueError("Не удалось определить смену")

    districts = {}
    i = 0
    while i < len(section_lines):
        line = section_lines[i]

        if "|" in line:
            left, right = [part.strip() for part in line.split("|", 1)]
            for name, pattern in DISTRICTS:
                if re.search(pattern, left, re.I):
                    if not right or len(right) > 100:
                        raise ValueError(f"Необычное значение района: {name}")
                    districts[name] = right
                    break
            i += 1
            continue

        matched_name = None
        matched_obj = None
        for name, pattern in DISTRICTS:
            m = re.match(pattern, line, re.I)
            if m:
                matched_name = name
                matched_obj = m
                break

        if matched_name:
            tail = line[matched_obj.end() :].strip(" :—–-|")
            if tail and len(tail) <= 100:
                districts[matched_name] = tail
            elif i + 1 < len(section_lines):
                val = section_lines[i + 1].strip()
                if val and len(val) <= 100:
                    districts[matched_name] = val
                i += 1

        i += 1

    if len(districts) != len(DISTRICTS):
        missing = [name for name, _ in DISTRICTS if name not in districts]
        raise ValueError(f"Не удалось получить данные всех районов (нет: {', '.join(missing)})")

    return {
        "date": decision_date.isoformat(),
        "shift": int(shift_match.group(1)),
        "districts": districts,
        "source": source,
    }


def load_source(session: requests.Session, source: dict) -> dict:
    url = to_punycode_url(source["url"])
    try:
        resp = session.get(url, headers=BROWSER_HEADERS, timeout=(10, 20))
    except requests.exceptions.SSLError:
        resp = session.get(url, headers=BROWSER_HEADERS, timeout=(10, 20), verify=False)

    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    return parse_page(resp.text, source)