import datetime as dt

import openpyxl

from friendsbot import journal


def test_journal_parses_china_time_to_utc(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["PAIR", "POSITION", "TIME/CHINA", "TP/SL", "RUNNER", "PIPS"])
    ws.append(["xaususd", "2EL", dt.time(12, 55), "TP", "yes", 6279])
    ws.append(["xaususd", "f2ES", dt.time(13, 45), "SL", "no", -200])
    ws.append(["xaususd", "2ES", "- ", "TP", "-", 5000])
    path = tmp_path / "j.xlsx"
    wb.save(path)
    rows = journal.load(path)
    assert [r["time_utc"] for r in rows] == ["04:55", "05:45", None]
    assert rows[1]["position"] == "F2ES" and rows[1]["direction"] == "short" and rows[1]["prefix_f"]
    s = journal.summary(rows)
    assert (s["trades"], s["tp"], s["sl"]) == (3, 2, 1)
    assert s["runner"] == {"yes": 1, "no": 1, "unknown": 1}
