from pathlib import Path

from openpyxl import Workbook

from app.services.file_inspector import preview_tabular_file


def test_preview_csv(tmp_path: Path):
    path = tmp_path / "counts.csv"
    path.write_text("taxon,S1,S2\nA,1,2\nB,3,4\nC,5,6\n")

    preview = preview_tabular_file(str(path), max_rows=10)

    assert preview["format"] == "csv"
    assert preview["editable"] is True
    assert preview["columns"] == ["taxon", "S1", "S2"]
    assert preview["dimensions"]["rows"] == 3
    assert preview["preview_rows"] == [["A", "1", "2"], ["B", "3", "4"], ["C", "5", "6"]]


def test_preview_excel(tmp_path: Path):
    path = tmp_path / "meta.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "meta"
    ws.append(["sample_id", "group"])
    ws.append(["S1", "A"])
    ws.append(["S2", "B"])
    wb.save(path)

    preview = preview_tabular_file(str(path), max_rows=10)

    assert preview["format"] == "excel"
    assert preview["editable"] is False
    assert preview["columns"] == ["sample_id", "group"]
    assert preview["preview_rows"][0] == ["S1", "A"]


def test_preview_spss(tmp_path: Path):
    from app.services.file_inspector import _inspect_sav

    path = tmp_path / "study.sav"
    path.write_bytes(b"dummy spss binary content")

    res = _inspect_sav(path)
    assert res["format"] == "spss"
    assert res["name"] == "study.sav"

