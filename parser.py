"""Загрузка и разбор страниц с опубликованными решениями об актировках.

Парсер намеренно строгий: если разметка изменилась или решение неполное,
он поднимает ValueError, а бот не публикует догадку. Молча превращать
неизвестную таблицу в «актировок нет» нельзя.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import requests
import urllib3
from bs4 import BeautifulSoup

from config import DISTRICTS

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADING = "ИНФОРМАЦИЯ ОБ АКТИРОВКАХ"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Заголовки, которые идут сразу после блока актировок на обеих страницах.
STOP_MARKERS = (
    "ИНФОРМАЦИЯ О ПОГОДЕ",
    "ВОЗОБНОВЛЕН СВОБОДНЫЙ ДОСТУП",
    "СОСТОЯНИЕ АВТОДОРОГ",
)

# Допустимые значения в ячейке района: «нет» либо диапазон классов.
STATUS_RE = re.compile(
    r"""
    ^(?:
        нет
      | актировк\w*\s+нет
      | (?:[1-9]|1[01])\s*[-–—]\s*(?:[1-9]|1[01])\s*класс\w*
      | (?:[1-9]|1[01])\s*класс\w*
      | все\s+класс\w*
      | с\s*(?:[1-9]|1[01])\s*по\s*(?:[1-9]|1[01])\s*класс\w*
    )$
    """,
    re.IGNORECASE | re.VERBOSE,
)

DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")


def to_punycode_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.hostname:
        return url
    host = parts.hostname.encode("idna").decode("ascii")
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _decode(raw: bytes) -> str:
    """Декодирует HTML: сначала meta charset, затем UTF-8, затем cp1251."""
    head = raw[:4096].decode("ascii", "ignore")
    meta = re.search(r"""charset\s*=\s*["']?([\w-]+)""", head, re.IGNORECASE)
    encodings = [meta.group(1)] if meta else []
    encodings += ["utf-8", "cp1251"]
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


def _clean_cell(value: str) -> str:
    value = value.replace("\xa0", " ").replace("|", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip(" :;.,—-–")


def _is_heading(line: str) -> bool:
    return line.strip().rstrip(":").strip().upper() == HEADING


def _validate_status(value: str, district: str) -> str:
    value = _clean_cell(value)
    if not value:
        raise ValueError(f"Пустое значение для района «{district}»")
    if len(value) > 100:
        raise ValueError(f"Слишком длинное значение для района «{district}»")
    if not STATUS_RE.match(value):
        raise ValueError(f"Неизвестный статус для района «{district}»: {value}")
    return value


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


def parse_page(html: str | bytes, source: dict) -> dict:
    if isinstance(html, bytes):
        html = _decode(html)

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    box = None
    for candidate in soup.select("div.white-box.padding-box"):
        if HEADING in candidate.get_text(" ", strip=True).upper():
            box = candidate
            break
    if box is None:
        box = soup.body or soup
    lines = extract_clean_lines(box)

    headings = [i for i, line in enumerate(lines) if _is_heading(line)]
    if not headings:
        raise ValueError("Не найден заголовок блока «ИНФОРМАЦИЯ ОБ АКТИРОВКАХ»")
    if len(headings) > 1:
        raise ValueError("На странице несколько блоков «ИНФОРМАЦИЯ ОБ АКТИРОВКАХ»")
    start_idx = headings[0]

    section_lines: list[str] = []
    stopped = False
    for line in lines[start_idx + 1 :]:
        if any(marker in line.upper() for marker in STOP_MARKERS):
            stopped = True
            break
        section_lines.append(line)
    if not stopped:
        raise ValueError("Не найден маркер конца блока актировок (погода/автодороги)")
    if not section_lines:
        raise ValueError("Блок с актировками пуст")

    section_text = " ".join(section_lines)

    dates = list(dict.fromkeys(DATE_RE.findall(section_text)))
    if len(dates) > 1:
        raise ValueError("В блоке актировок несколько разных дат: " + ", ".join(dates))
    if dates:
        date_str = dates[0]
    else:
        before_text = " ".join(lines[max(0, start_idx - 3) : start_idx])
        before_dates = list(dict.fromkeys(DATE_RE.findall(before_text)))
        if len(before_dates) > 1:
            raise ValueError("Перед блоком актировок несколько разных дат")
        date_str = before_dates[-1] if before_dates else None
    if not date_str:
        raise ValueError("В решении не найдена дата")

    decision_date = datetime.strptime(date_str, "%d.%m.%Y").date()

    shift_match = re.search(
        r"\b([12])\s*(?:[-–—]\s*)?(?:ой|й|ая|я|ую|ю)?\s*смен", section_text, re.IGNORECASE
    )
    if shift_match:
        shift = int(shift_match.group(1))
    else:
        word_shift = re.search(r"\b(перв|втор)\w*\s+смен", section_text, re.IGNORECASE)
        if not word_shift:
            raise ValueError("Не удалось определить смену")
        shift = 1 if word_shift.group(1).lower().startswith("перв") else 2

    districts: dict[str, str] = {}
    i = 0
    while i < len(section_lines):
        line = section_lines[i]

        if "|" in line:
            left, right = line.split("|", 1)
            for name, pattern in DISTRICTS:
                if re.search(pattern, left, re.IGNORECASE):
                    districts[name] = _validate_status(right, name)
                    break
            i += 1
            continue

        matched_name = None
        matched_obj = None
        for name, pattern in DISTRICTS:
            match = re.match(pattern, line, re.IGNORECASE)
            if match:
                matched_name = name
                matched_obj = match
                break

        if matched_name:
            tail = line[matched_obj.end() :].strip(" :—–-|")
            if tail:
                districts[matched_name] = _validate_status(tail, matched_name)
            elif i + 1 < len(section_lines):
                districts[matched_name] = _validate_status(section_lines[i + 1], matched_name)
                i += 1

        i += 1

    if len(districts) != len(DISTRICTS):
        missing = [name for name, _ in DISTRICTS if name not in districts]
        raise ValueError("Не удалось получить данные всех районов (нет: " + ", ".join(missing) + ")")

    return {
        "date": decision_date.isoformat(),
        "shift": shift,
        "districts": districts,
        "source": source,
    }


def _normalize_status(value: str) -> str:
    value = value.lower().replace("\xa0", " ")
    for dash in ("–", "—", "−"):
        value = value.replace(dash, "-")
    value = re.sub(r"\s*-\s*", "-", value)
    return re.sub(r"\s+", " ", value).strip(" :;.,")


def fingerprint(report: dict) -> str:
    """Отпечаток решения. Источник в отпечаток не входит."""
    payload = {
        "date": report["date"],
        "shift": report["shift"],
        "districts": {name: _normalize_status(value) for name, value in report["districts"].items()},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def format_report(report: dict, updated: bool = False) -> str:
    date = datetime.strptime(report["date"], "%Y-%m-%d").strftime("%d.%m.%Y")
    title = "Уточнение: актировки в Норильске" if updated else "Актировки в Норильске"
    lines = [
        ("🔄 " if updated else "❄️ ") + title,
        f"📅 {date} · {report['shift']}-я смена",
        "",
    ]
    for name, _ in DISTRICTS:
        lines.append(f"{name}: {report['districts'][name]}")
    source = report["source"]
    lines += [
        "",
        f"Источник: {source['name']}",
        source.get("display_url") or source["url"],
    ]
    return "\n".join(lines)


def load_source(session: requests.Session, source: dict) -> dict:
    url = to_punycode_url(source["url"])
    try:
        response = session.get(url, headers=BROWSER_HEADERS, timeout=(10, 30))
    except requests.exceptions.SSLError:
        logging.warning(
            "%s: не удалось проверить TLS-сертификат, повтор без проверки. "
            "По возможности установите корневой сертификат сайта.",
            source["name"],
        )
        response = session.get(
            url, headers=BROWSER_HEADERS, timeout=(10, 30), verify=False
        )
    response.raise_for_status()
    return parse_page(response.content, source)
