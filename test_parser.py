import sys
sys.path.insert(0, ".")
from parser import parse_page

OFFICIAL = """<html><body>
<p>09.11.2026</p>
<h2>ИНФОРМАЦИЯ ОБ АКТИРОВКАХ</h2>
<p>В соответствии с предоставленной метеорологической информацией ... для обучающихся 2-ой смены ... актировки нет.</p>
<table>
<tr><td>Центральный район</td><td>нет</td></tr>
<tr><td>ж/о Оганер</td><td>1-6 классы</td></tr>
<tr><td>район Талнах</td><td>нет</td></tr>
<tr><td>район Кайеркан</td><td>1-4 классы</td></tr>
</table>
<h2>ИНФОРМАЦИЯ О ПОГОДЕ</h2>
</body></html>"""

SG = """<html><body>
<h3>Норильск ...</h3>
<h2>Актировки и Сообщения ГО и ЧС</h2>
<p>01.11.2026</p>
<strong>ИНФОРМАЦИЯ ОБ АКТИРОВКАХ</strong>
<p>... для обучающихся 1-ой смены ... актировки нет.</p>
<table>
<tr><td>Центральный район</td><td>нет</td></tr>
<tr><td>ж/о Оганер</td><td>1-11 классы</td></tr>
<tr><td>район Талнах</td><td>нет</td></tr>
<tr><td>район Кайеркан</td><td>нет</td></tr>
</table>
<h3>ИНФОРМАЦИЯ О ПОГОДЕ</h3>
</body></html>"""

if __name__ == "__main__":
    ok = 0
    for label, html in [("official", OFFICIAL), ("sg", SG)]:
        try:
            rep = parse_page(html, {"name": label, "url": "test"})
            assert rep["date"] in ("2026-11-09", "2026-11-01"), rep["date"]
            assert rep["shift"] in (1, 2), rep["shift"]
            assert len(rep["districts"]) == 4
            ok += 1
            print(label, "OK", rep["date"], rep["shift"])
        except Exception as e:
            print(label, "FAIL", type(e).__name__, e)
    sys.exit(0 if ok == 2 else 1)
