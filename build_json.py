"""excel/ papkasidagi barcha .xlsx fayllarni o'qib, units.json yaratadi.

Ishlatish:  python build_json.py

Fayl formatlari avtomatik aniqlanadi (ustun sarlavhalari bo'yicha):
  - Kitob | Unit | So'z | Transkripsiya | So'z turkumi | O'zbekcha ma'nosi | Misol jumla
  - Unit | So'z | Transkripsiya | So'z turkumi | O'zbekcha ma'nosi | Misol jumla
  - № | Unit | Inglizcha so'z | O'zbekcha tarjimasi
Kitob raqami: "Kitob" ustunidan, bo'lmasa varaq nomidan (masalan "Kitob 3"),
bo'lmasa fayl nomidan (masalan ..._2.xlsx) olinadi.
"""
import json
import re
import sys
from pathlib import Path

import openpyxl

BASE = Path(__file__).parent
FORCE = "--force" in sys.argv
ARGS = [a for a in sys.argv[1:] if a != "--force"]
EXCEL_DIR = Path(ARGS[0]) if len(ARGS) > 0 else BASE / "excel"
OUT = Path(ARGS[1]) if len(ARGS) > 1 else BASE / "units.json"


def norm(v):
    return str(v).strip().lower() if v is not None else ""


def map_header(row):
    """Sarlavha qatoridan ustun raqamlarini topadi."""
    cols = {}
    for i, cell in enumerate(row):
        h = norm(cell)
        if not h:
            continue
        if h == "unit":
            cols["unit"] = i
        elif "kitob" in h:
            cols["book"] = i
        elif "transkripsiya" in h:
            cols["tr"] = i
        elif "turkum" in h:
            cols["pos"] = i
        elif "misol" in h:
            cols["ex"] = i
        elif "zbekcha" in h or "nosi" in h or "tarjima" in h:
            cols["uz"] = i
        elif "word" in h or "inglizcha" in h:
            cols["en"] = i
    return cols


def book_from(value):
    m = re.search(r"(\d+)", str(value or ""))
    return m.group(1) if m else None


def read_sheet(ws, filename):
    header_idx, cols = None, None
    for idx, row in enumerate(ws.iter_rows(min_row=1, max_row=15, values_only=True)):
        c = map_header(row)
        if "unit" in c and "en" in c and "uz" in c:
            header_idx, cols = idx + 1, c
            break
    if not cols:
        return []
    m = re.search(r"(?:kitob\s*)?(\d+)\s*$", ws.title, re.I)
    default_book = (m.group(1) if m else None) or book_from(
        (re.search(r"_(\d+)\.xlsx$", filename) or [None, None])[1]
    )
    out = []
    for row in ws.iter_rows(min_row=header_idx + 1, values_only=True):
        def get(key):
            i = cols.get(key)
            return str(row[i]).strip() if i is not None and i < len(row) and row[i] is not None else ""

        um = re.match(r"Unit\s*(\d+)", get("unit"), re.I)
        book = book_from(get("book")) if "book" in cols else default_book
        en, uz = get("en"), get("uz")
        if not (um and book and en and uz):
            continue
        item = {"en": en, "uz": uz}
        for key in ("tr", "pos", "ex"):
            if get(key):
                item[key] = get(key)
        out.append((book, um.group(1), item))
    return out


# data[book][unit][en.lower()] = item   (takrorlar birlashtiriladi)
data = {}
junk = {}  # sifatsiz (takrorlangan) kitoblar, faqat --force bilan ishlatiladi
for f in sorted(EXCEL_DIR.glob("*.xlsx")):
    if f.name.startswith("~$"):
        continue
    wb = openpyxl.load_workbook(f, data_only=True)
    # shu fayl ichida: file_books[book][unit][en.lower()] = item
    file_books = {}
    for ws in wb.worksheets:
        for book, unit, item in read_sheet(ws, f.name):
            file_books.setdefault(book, {}).setdefault(unit, {})[item["en"].lower()] = item
    # Sifat tekshiruvi: har bir fayl-kitob alohida tekshiriladi.
    # Bir xil so'zlar qayta-qayta takrorlangan bo'lsa, o'tkazib yuboriladi.
    for book in sorted(file_books, key=int):
        words = [it for u in file_books[book].values() for it in u.values()]
        uniq = {w["en"].lower() for w in words}
        if len(uniq) < 0.5 * len(words):
            print(
                f"⚠️  {f.name} -> {book}-kitob O'TKAZIB YUBORILDI: {len(words)} ta qatorda "
                f"atigi {len(uniq)} ta noyob so'z bor (ma'lumot takrorlangan/noto'g'ri)."
            )
            junk.setdefault(book, file_books[book])
            continue
        for unit, d in file_books[book].items():
            data.setdefault(book, {}).setdefault(unit, {}).update(d)
        print(f"O'qildi: {f.name} -> {book}-kitob ({len(words)} so'z)")

if FORCE:
    # haqiqiy ma'lumoti yo'q kitoblarga sifatsiz ma'lumotni vaqtincha qo'shamiz
    for book, units in junk.items():
        if book not in data:
            data[book] = units
            print(f"⚠️  {book}-kitob: VAQTINCHA takrorlangan ma'lumot qo'shildi (--force)")

final = {}
for book in sorted(data, key=int):
    units = data[book]
    final[book] = {u: list(units[u].values()) for u in sorted(units, key=int)}
    total = sum(len(v) for v in final[book].values())
    print(f"✅ {book}-kitob: {len(units)} unit, {total} so'z")

if not final:
    raise SystemExit("Yaroqli ma'lumot topilmadi. units.json o'zgartirilmadi.")

OUT.write_text(json.dumps(final, ensure_ascii=False, indent=1), encoding="utf-8")
print("Saqlandi:", OUT)
