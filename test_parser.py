import unittest
from parser import parse_page, fingerprint
SOURCE = {"name": "Тест", "url": "https://example.org", "official": True}

def page(status="нет", date="09.11.2026", shift="2-ой"):
    rows = "".join(f"<tr><td>{name}</td><td>{status}</td></tr>" for name in
                   ["Центральный район", "ж/о Оганер", "район Талнах", "район Кайеркан"])
    return (f"<h2>ИНФОРМАЦИЯ ОБ АКТИРОВКАХ</h2><p>По состоянию на {date} "
            f"для обучающихся {shift} смены.</p><table>{rows}</table>"
            "<h2>ИНФОРМАЦИЯ О ПОГОДЕ</h2><p>Прогноз 10.11.2026</p>")

class ParserTests(unittest.TestCase):
    def test_date_shift(self):
        r = parse_page(page(), SOURCE)
        self.assertEqual((r["date"], r["shift"], len(r["districts"])), ("2026-11-09", 2, 4))
    def test_classes(self):
        self.assertEqual(parse_page(page("1–6 классы", shift="1-й"), SOURCE)["shift"], 1)
    def test_ambiguous_date(self):
        with self.assertRaises(ValueError):
            parse_page(page().replace("По состоянию", "10.11.2026 По состоянию"), SOURCE)
    def test_missing_district(self):
        with self.assertRaises(ValueError):
            parse_page(page().replace("район Талнах", "Другой район"), SOURCE)
    def test_unknown_status(self):
        with self.assertRaises(ValueError):
            parse_page(page("непонятно"), SOURCE)
    def test_missing_boundary(self):
        with self.assertRaises(ValueError):
            parse_page(page().replace("ИНФОРМАЦИЯ О ПОГОДЕ", "ПОГОДА"), SOURCE)
    def test_same_hash(self):
        a = parse_page(page("1–6 классы"), SOURCE)
        b = parse_page(page("1-6 классы"), {**SOURCE, "name": "Другой"})
        self.assertEqual(fingerprint(a), fingerprint(b))
    def test_repeated_heading(self):
        with self.assertRaises(ValueError):
            parse_page(page() + "<h2>ИНФОРМАЦИЯ ОБ АКТИРОВКАХ</h2>", SOURCE)

if __name__ == "__main__":
    unittest.main()
