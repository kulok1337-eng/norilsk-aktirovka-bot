"""Проверки на фрагментах реальных страниц сайтов-источников."""
import unittest
from pathlib import Path

from config import SOURCES
from parser import _decode, fingerprint, format_report, parse_page

ROOT = Path(__file__).resolve().parent
OFFICIAL = ROOT / "fixtures" / "official_2026-11-25.html"
SGNORILSK = ROOT / "fixtures" / "sgnorilsk_2026-11-01.html"


class RealPageTests(unittest.TestCase):
    def test_official_page(self):
        report = parse_page(OFFICIAL.read_bytes(), SOURCES[0])
        self.assertEqual(report["date"], "2026-11-25")
        self.assertEqual(report["shift"], 2)
        self.assertEqual(
            report["districts"],
            {
                "Центральный район": "нет",
                "ж/о Оганер": "нет",
                "район Талнах": "нет",
                "район Кайеркан": "нет",
            },
        )

    def test_sgnorilsk_page(self):
        report = parse_page(SGNORILSK.read_bytes(), SOURCES[1])
        self.assertEqual(report["date"], "2026-11-01")
        self.assertEqual(report["shift"], 2)
        self.assertEqual(len(report["districts"]), 4)

    def test_cp1251_bytes(self):
        html = OFFICIAL.read_text("utf-8")
        raw = html.encode("cp1251")
        self.assertEqual(parse_page(raw, SOURCES[0])["date"], "2026-11-25")
        self.assertIn("25.11.2026", _decode(raw))

    def test_format_report(self):
        text = format_report(parse_page(OFFICIAL.read_bytes(), SOURCES[0]))
        for part in (
            "Актировки в Норильске",
            "25.11.2026",
            "2-я смена",
            "Центральный район: нет",
            "ж/о Оганер: нет",
            "район Талнах: нет",
            "район Кайеркан: нет",
            "Официальный сайт Норильска",
            "норильск.рф",
        ):
            self.assertIn(part, text)
        self.assertLess(len(text), 4096)

    def test_updated_prefix(self):
        report = parse_page(OFFICIAL.read_bytes(), SOURCES[0])
        self.assertTrue(format_report(report, updated=True).startswith("🔄 Уточнение"))

    def test_fingerprint_ignores_source(self):
        report = parse_page(OFFICIAL.read_bytes(), SOURCES[0])
        other = {**report, "source": SOURCES[1]}
        self.assertEqual(fingerprint(report), fingerprint(other))
        changed = {**report, "shift": 1}
        self.assertNotEqual(fingerprint(report), fingerprint(changed))


if __name__ == "__main__":
    unittest.main()
