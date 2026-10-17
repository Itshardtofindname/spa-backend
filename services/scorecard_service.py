
import re
from datetime import date, datetime
from io import BytesIO

from openpyxl import load_workbook

from database import supabase


TARGET_SHEET = "Produktifitas"


def normalize_header(value):
    if value is None:
        return ""

    text = str(value).strip().lower()
    text = text.replace(".", "")
    text = re.sub(r"\s+", " ", text)

    return text


def normalize_npk(value):
    if value is None:
        return ""

    # Excel terkadang menyimpan NPK numerik sebagai float.
    if isinstance(value, float) and value.is_integer():
        value = int(value)

    return str(value).strip()


def parse_number(value):
    if value is None or str(value).strip() == "":
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace(" ", "")

    if "," in text and "." not in text:
        text = text.replace(",", ".")

    try:
        return float(text)
    except (ValueError, TypeError):
        return None


def get_month_label(value):
    if isinstance(value, (datetime, date)):
        return value.strftime("%b-%y")

    if value is None:
        return None

    text = str(value).strip()

    match = re.match(
        r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"[- ]?(\d{2,4})$",
        text,
        re.IGNORECASE,
    )

    if not match:
        return None

    month = match.group(1).title()
    year = match.group(2)

    if len(year) == 4:
        year = year[-2:]

    return f"{month}-{year}"


def find_header_row(sheet):
    for row_number in range(1, min(sheet.max_row, 30) + 1):
        values = [
            normalize_header(cell.value)
            for cell in sheet[row_number]
        ]

        has_npk = "npk" in values
        has_salesman = any(
            value in ("salesman", "nama salesman")
            for value in values
        )
        has_ph = "ph" in values

        if has_npk and has_salesman and has_ph:
            return row_number

    return None


def import_scorecard_excel(
    file_bytes,
    sales_office,
    report_period,
    source_file,
):
    workbook = load_workbook(
        BytesIO(file_bytes),
        read_only=True,
        data_only=True,
    )

    try:
        if TARGET_SHEET not in workbook.sheetnames:
            raise ValueError(
                f"Sheet '{TARGET_SHEET}' tidak ditemukan. "
                f"Sheet tersedia: {', '.join(workbook.sheetnames)}"
            )

        sheet = workbook[TARGET_SHEET]
        header_row = find_header_row(sheet)

        if header_row is None:
            raise ValueError(
                "Header tidak ditemukan. Pastikan ada kolom "
                "NPK, SALESMAN, dan PH."
            )

        header_cells = [
            cell.value for cell in sheet[header_row]
        ]

        headers = [
            normalize_header(value)
            for value in header_cells
        ]

        # Simpan indeks header pertama untuk setiap nama kolom.
        columns = {}

        for index, header in enumerate(headers):
            if header and header not in columns:
                columns[header] = index

        month_columns = {}

        for index, value in enumerate(header_cells):
            label = get_month_label(value)

            if label and label not in month_columns:
                month_columns[label] = index

        def get_value(row, *names):
            for name in names:
                index = columns.get(name)

                if index is not None and index < len(row):
                    return row[index]

            return None

        records_by_npk = {}
        duplicate_npks = set()

        # Hanya membaca tabel setelah header pertama.
        # Berhenti jika menemukan header tabel berikutnya.
        for row in sheet.iter_rows(
            min_row=header_row + 1,
            values_only=True,
        ):
            normalized_row = [
                normalize_header(value) for value in row
            ]

            if (
                "npk" in normalized_row
                and any(
                    value in ("salesman", "nama salesman")
                    for value in normalized_row
                )
                and "ph" in normalized_row
            ):
                logger_message = (
                    "Header tabel berikutnya ditemukan; "
                    "pembacaan tabel pertama dihentikan."
                )
                print(logger_message)
                break

            npk_value = get_value(row, "npk")
            salesman_value = get_value(
                row, "salesman", "nama salesman"
            )
            ph_value = get_value(row, "ph")

            if (
                npk_value is None
                or salesman_value is None
                or ph_value is None
            ):
                continue

            npk = normalize_npk(npk_value)
            salesman = str(salesman_value).strip()
            ph = str(ph_value).strip()

            if not npk or not salesman or not ph:
                continue

            if normalize_header(npk) in {
                "npk", "total", "subtotal", "grand total"
            }:
                continue

            monthly_values = {}

            for month, index in month_columns.items():
                if index < len(row):
                    value = parse_number(row[index])

                    if value is not None:
                        monthly_values[month] = value

            record = {
                "sales_office": sales_office,
                "report_period": report_period,
                "npk": npk,
                "salesman": salesman,
                "ph": ph,
                "monthly_values": monthly_values,
                "q1": parse_number(get_value(row, "q1")),
                "perform": parse_number(
                    get_value(row, "perform")
                ),
                "oa": parse_number(get_value(row, "oa")),
                "r_order": parse_number(
                    get_value(row, "rorder", "r order")
                ),
                "cross_sell": parse_number(
                    get_value(row, "cross sell")
                ),
                "dks": parse_number(get_value(row, "dks")),
                "source_file": source_file,
                "updated_at": datetime.now().astimezone().isoformat(),
            }

            if npk in records_by_npk:
                duplicate_npks.add(npk)
                continue

            records_by_npk[npk] = record

        if duplicate_npks:
            raise ValueError(
                "NPK duplikat ditemukan pada tabel pertama: "
                + ", ".join(sorted(duplicate_npks))
                + ". Impor dibatalkan agar data tidak tertimpa "
                "secara tidak sengaja."
            )

        records = list(records_by_npk.values())

        if not records:
            raise ValueError(
                "Tidak ada baris Scorecard yang valid pada "
                f"sheet '{TARGET_SHEET}'."
            )

        response = (
            supabase.table("scorecard_productivity")
            .upsert(
                records,
                on_conflict="sales_office,report_period,npk",
            )
            .execute()
        )

        return {
            "message": "Impor Scorecard berhasil",
            "sheet": TARGET_SHEET,
            "sheet_rows_imported": len(records),
            "sales_office": sales_office,
            "report_period": report_period,
            "source_file": source_file,
        }

    finally:
        workbook.close()


def get_scorecard_productivity(sales_office, report_period):
    response = (
        supabase.table("scorecard_productivity")
        .select("*")
        .eq("sales_office", sales_office)
        .eq("report_period", report_period)
        .order("ph")
        .order("npk")
        .execute()
    )

    return response.data or []
