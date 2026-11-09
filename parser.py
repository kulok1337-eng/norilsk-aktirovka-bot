"""Разбор страницы «Погода и сообщения ГОиЧС»: дата, смена, районы."""

import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from config import DISTRICTS


def normalize(text):
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_page(html, source):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = normalize(soup.get_text(" ", strip=True))

    start = re.search(r"информация\s+об\s+актировках", text, re.I)
    if not start:
        raise ValueError("Не найден заголовок блока об актировках")

    remainder = text[start.end():]
    end = re.search(r"информация\s+о\s+погоде", remainder, re.I)
    if not end:
        raise ValueError("Не найдена граница блока об актировках")

    block = remainder[:end.start()]

    dates = re.findall(r"\b\d{2}\.\d{2}\.\d{4}\b", block)
    if not dates:
        # Дата может идти раньше заголовка; берём ближайшую до него.
        before = text[:start.start()]
        dates = re.findall(r"\b\d{2}\.\d{2}\.\d{4}\b", before)[-1:]
    if not dates:
        raise ValueError("В решении не найдена дата")

    parsed_dates = {
        datetime.strptime(value, "%d.%m.%Y").date() for value in dates
    }

    if len(parsed_dates) != 1:
        raise ValueError("В блоке разные даты: нужна ручная проверка")

    decision_date = parsed_dates.pop()

    shift_match = re.search(
        r"\b([12])\s*(?:[-–—]\s*)?(?:ой|й|ая|я|ую|ю)?\s*смен",
        block,
        re.I,
    )
    if not shift_match:
        raise ValueError("Не удалось определить смену")

    candidate_tables = []
    for table in soup.find_all("table"):
        table_text = normalize(table.get_text(" ", strip=True))
        if (
            table_text
            and table_text in block
            and all(
                re.search(pattern, table_text, re.I)
                for _, pattern in DISTRICTS
            )
        ):
            candidate_tables.append(table)

    if len(candidate_tables) != 1:
        raise ValueError("Не найдена однозначная таблица четырёх районов")

    districts = {}

    for row in candidate_tables[0].find_all("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) < 2:
            continue

        label = normalize(cells[0].get_text(" ", strip=True))
        value = normalize(
            " ".join(cell.get_text(" ", strip=True) for cell in cells[1:])
        )

        for name, pattern in DISTRICTS:
            if re.search(pattern, label, re.I):
                if name in districts:
                    raise ValueError(f"Повтор района: {name}")
                if not value or len(value) > 100:
                    raise ValueError(f"Необычное значение района: {name}")
                districts[name] = value

    if len(districts) != 4:
        raise ValueError("Не удалось получить данные всех четырёх районов")

    return {
        "date": decision_date.isoformat(),
        "shift": int(shift_match.group(1)),
        "districts": districts,
        "source": source,
    }


def load_source(session, source):
    response = session.get(source["url"], timeout=30)
    response.raise_for_status()

    # Используем разбор BeautifulSoup по умолчанию, чтобы учесть кодировку.
    return parse_page(str(BeautifulSoup(response.content, "html.parser")), source)
