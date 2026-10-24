
from datetime import datetime, timezone
from io import BytesIO
import re

from openpyxl import load_workbook

from database import supabase


SUMM_SHEET = "SUMM"


def normalize_header(value):
    """Menyeragamkan teks header Excel."""
    if value is None:
        return ""

    text = str(value).strip().casefold()
    text = re.sub(r"[_./-]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_npk(value):
    """Mengubah NPK menjadi string konsisten."""
    if value is None or isinstance(value, bool):
        return ""

    if isinstance(value, float) and value.is_integer():
        value = int(value)

    text = str(value).strip()

    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".")[0]

    return text


def normalize_salesman_name(value):
    """
    Menormalisasi nama untuk pencocokan master salesman.

    Perbedaan huruf besar/kecil dan spasi berlebih tidak
    menyebabkan salesman dianggap sebagai orang berbeda.
    """
    if value is None:
        return ""

    text = str(value).strip().casefold()
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_sales_office(value):
    """Menormalisasi nama sales office untuk pencocokan."""
    if value is None:
        return ""

    text = str(value).strip().casefold()
    text = re.sub(r"\s+", " ", text)
    return text


def parse_summ_number(value):
    """Mengubah nilai indikator SUMM menjadi angka."""
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace(" ", "")

    if not text:
        return None

    if "," in text and "." not in text:
        text = text.replace(",", ".")

    try:
        return float(text)
    except (ValueError, TypeError):
        return None


def _find_summ_headers(sheet):
    """
    Mencari dua baris header pada sheet SUMM.

    Baris pertama dapat berisi kelompok indikator.
    Baris berikutnya berisi nama kolom sebenarnya.
    """
    max_header_row = min(sheet.max_row or 0, 40)

    for row_number in range(1, max_header_row + 1):
        current = [
            normalize_header(cell.value)
            for cell in sheet[row_number]
        ]

        next_row = (
            [
                normalize_header(cell.value)
                for cell in sheet[row_number + 1]
            ]
            if row_number < sheet.max_row
            else []
        )

        all_headers = set(current + next_row)

        has_npk = "npk" in all_headers
        has_name = any(
            value in {
                "nama",
                "name",
                "salesman",
                "nama salesman",
            }
            for value in all_headers
        )
        has_grade = "grade" in all_headers
        has_score = "score" in all_headers
        has_result = any(
            "sales achiev" in value
            or "sales achievement" in value
            for value in all_headers
        )

        if not (
            has_npk
            and has_name
            and has_grade
            and has_score
            and has_result
        ):
            continue

        columns = {}
        max_columns = max(len(current), len(next_row))

        for index in range(max_columns):
            first_value = (
                current[index]
                if index < len(current)
                else ""
            )
            second_value = (
                next_row[index]
                if index < len(next_row)
                else ""
            )

            # Prioritaskan baris kedua yang berisi nama indikator.
            selected_header = second_value or first_value

            if selected_header:
                columns[index] = selected_header

        return row_number, columns

    raise ValueError(
        "Header SUMM tidak ditemukan. "
        "Pastikan sheet memiliki NPK, NAMA, SALES ACHIEV, "
        "GRADE, dan SCORE."
    )


def _get_summ_column(columns, aliases):
    """Mencari kolom menggunakan daftar alias."""
    for index, header in columns.items():
        for alias in aliases:
            if alias in header:
                return index

    return None


def _get_cell(row, index):
    """Mengambil nilai kolom dengan aman."""
    if index is None or index >= len(row):
        return None

    return row[index]


def _extract_summ_records(
    file_bytes,
    sales_office,
    report_period,
    source_file,
):
    """Membaca sheet SUMM dan menghasilkan record terstandardisasi."""
    workbook = load_workbook(
        BytesIO(file_bytes),
        read_only=True,
        data_only=True,
    )

    try:
        if SUMM_SHEET not in workbook.sheetnames:
            raise ValueError(
                f"Sheet '{SUMM_SHEET}' tidak ditemukan. "
                f"Sheet tersedia: {', '.join(workbook.sheetnames)}"
            )

        sheet = workbook[SUMM_SHEET]
        header_row, columns = _find_summ_headers(sheet)

        column_map = {
            "npk": _get_summ_column(columns, ["npk"]),
            "nama": _get_summ_column(
                columns,
                ["nama", "salesman"],
            ),
            "result_sales_achiev_gr": _get_summ_column(
                columns,
                ["sales achiev", "sales achievement"],
            ),
            "cross_up_sell": _get_summ_column(
                columns,
                ["cross up sell", "cross selling", "up sell"],
            ),
            "active_outlet": _get_summ_column(
                columns,
                ["active outlet"],
            ),
            "repeat_order": _get_summ_column(
                columns,
                ["repeat order"],
            ),
            "process_dks": _get_summ_column(
                columns,
                ["dks"],
            ),
            "final_grade": _get_summ_column(
                columns,
                ["grade"],
            ),
            "final_score": _get_summ_column(
                columns,
                ["score"],
            ),
        }

        required_columns = [
            "npk",
            "nama",
            "result_sales_achiev_gr",
            "cross_up_sell",
            "active_outlet",
            "repeat_order",
            "process_dks",
            "final_grade",
            "final_score",
        ]

        missing = [
            key
            for key in required_columns
            if column_map[key] is None
        ]

        if missing:
            raise ValueError(
                "Kolom SUMM tidak lengkap: "
                + ", ".join(missing)
            )

        records_by_npk = {}
        duplicate_npks = set()
        identities_seen = set()
        duplicate_identities = set()
        skipped_rows = 0

        # Data dimulai dua baris setelah baris kelompok header.
        for row in sheet.iter_rows(
            min_row=header_row + 2,
            values_only=True,
        ):
            npk = normalize_npk(
                _get_cell(row, column_map["npk"])
            )

            name_value = _get_cell(
                row,
                column_map["nama"],
            )

            nama = (
                str(name_value).strip()
                if name_value is not None
                else ""
            )

            if not npk or not nama:
                skipped_rows += 1
                continue

            if not re.fullmatch(r"\d+", npk):
                skipped_rows += 1
                continue

            result_value = parse_summ_number(
                _get_cell(
                    row,
                    column_map["result_sales_achiev_gr"],
                )
            )

            cross_value = parse_summ_number(
                _get_cell(
                    row,
                    column_map["cross_up_sell"],
                )
            )

            outlet_value = parse_summ_number(
                _get_cell(
                    row,
                    column_map["active_outlet"],
                )
            )

            repeat_value = parse_summ_number(
                _get_cell(
                    row,
                    column_map["repeat_order"],
                )
            )

            dks_value = parse_summ_number(
                _get_cell(
                    row,
                    column_map["process_dks"],
                )
            )

            grade_value = parse_summ_number(
                _get_cell(
                    row,
                    column_map["final_grade"],
                )
            )

            score_value = _get_cell(
                row,
                column_map["final_score"],
            )

            final_score = (
                str(score_value).strip().upper()
                if score_value is not None
                else ""
            )

            metric_values = [
                result_value,
                cross_value,
                outlet_value,
                repeat_value,
                dks_value,
                grade_value,
            ]

            # Abaikan baris yang belum memiliki nilai SUMM.
            if (
                all(value is None for value in metric_values)
                and not final_score
            ):
                skipped_rows += 1
                continue

            if grade_value is None or not final_score:
                raise ValueError(
                    f"Grade atau score SUMM kosong untuk "
                    f"NPK {npk} ({nama})."
                )

            record = {
                "npk": npk,
                "nama": nama,
                "sales_office": sales_office,
                "report_period": str(report_period),
                "final_grade": (
                    int(grade_value)
                    if grade_value.is_integer()
                    else grade_value
                ),
                "final_score": final_score,
                "strategic_alignment": {
                    "cross_up_sell": cross_value or 0,
                    "active_outlet": outlet_value or 0,
                    "repeat_order": repeat_value or 0,
                    "process_dks": dks_value or 0,
                },
                "result_sales_achiev_gr": result_value or 0,
                "source_file": source_file,
                "updated_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            }

            if npk in records_by_npk:
                duplicate_npks.add(npk)
                continue

            # Cegah dua baris dengan nama dan office yang sama
            # memperbarui master yang sama secara tidak sengaja.
            identity = (
                normalize_salesman_name(nama),
                normalize_sales_office(sales_office),
            )

            if identity in identities_seen:
                duplicate_identities.add(nama)
                continue

            identities_seen.add(identity)
            records_by_npk[npk] = record

        if duplicate_npks:
            raise ValueError(
                "Impor SUMM dibatalkan karena NPK duplikat: "
                + ", ".join(sorted(duplicate_npks))
            )

        if duplicate_identities:
            raise ValueError(
                "Impor SUMM dibatalkan karena terdapat nama "
                "salesman berulang pada sales office yang sama: "
                + ", ".join(sorted(duplicate_identities))
            )

        records = list(records_by_npk.values())

        if not records:
            raise ValueError(
                "Tidak ada baris SUMM valid yang ditemukan."
            )

        return records, skipped_rows

    finally:
        workbook.close()


def _get_salesmen_master(client):
    """
    Mengambil master salesman untuk pencocokan.

    Kolom id dipakai sebagai kunci update agar pembaruan
    tidak mengenai beberapa baris dengan NPK yang sama.
    """
    response = (
        client.table("salesmen")
        .select(
            "id,npk,nama,sales_office,grup_product,summ_history"
        )
        .execute()
    )

    return response.data or []


def _find_matching_salesman(master_rows, record):
    """
    Mencari satu master salesman berdasarkan nama dan office.

    NPK dari SUMM bukan kunci utama karena dapat berbeda
    dari NPK master Productivity.
    """
    target_name = normalize_salesman_name(record["nama"])
    target_office = normalize_sales_office(
        record["sales_office"]
    )

    matches = [
        row
        for row in master_rows
        if normalize_salesman_name(row.get("nama"))
        == target_name
        and normalize_sales_office(row.get("sales_office"))
        == target_office
    ]

    if len(matches) > 1:
        raise ValueError(
            "Ditemukan lebih dari satu master salesman untuk "
            f"{record['nama']} di {record['sales_office']}. "
            "Impor dibatalkan agar histori tidak salah masuk."
        )

    if matches:
        return matches[0]

    # Tidak ada nama + office yang cocok.
    # Jangan insert jika NPK SUMM sudah dipakai master lain.
    target_npk = normalize_npk(record["npk"])

    npk_matches = [
        row
        for row in master_rows
        if normalize_npk(row.get("npk")) == target_npk
    ]

    if npk_matches:
        details = ", ".join(
            f"{row.get('nama')} "
            f"(NPK {row.get('npk')}, "
            f"office {row.get('sales_office')})"
            for row in npk_matches
        )

        raise ValueError(
            f"NPK SUMM {target_npk} untuk {record['nama']} "
            "sudah digunakan oleh master salesman lain: "
            f"{details}. Tidak ada data yang diubah."
        )

    return None


def import_salesman_summ_excel(
    file_bytes,
    sales_office,
    report_period,
    source_file,
    dry_run=False,
    db_client=None,
):
    """
    Impor sheet SUMM ke salesmen.summ_history.

    Kunci histori:
        YYYY-MM-DD|SALES OFFICE

    Aturan:
    - Master dicocokkan berdasarkan nama + sales office.
    - NPK master tidak diganti oleh NPK dari SUMM.
    - NPK SUMM disimpan di dalam entri histori.
    - Periode yang sama diperbarui/ditimpa.
    - Histori periode lain dipertahankan.
    - Master baru hanya dibuat jika tidak ditemukan pasangan
      nama + office dan NPK SUMM belum dipakai master lain.
    """
    sales_office = str(sales_office or "").strip()
    report_period = str(report_period or "").strip()

    if not sales_office:
        raise ValueError("sales_office tidak boleh kosong.")

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_period):
        raise ValueError(
            "report_period harus berformat YYYY-MM-DD."
        )

    try:
        datetime.strptime(report_period, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(
            "report_period bukan tanggal yang valid."
        ) from exc

    records, skipped_rows = _extract_summ_records(
        file_bytes=file_bytes,
        sales_office=sales_office,
        report_period=report_period,
        source_file=source_file,
    )

    if dry_run:
        return {
            "message": (
                "Validasi SUMM berhasil; database tidak diubah."
            ),
            "dry_run": True,
            "sheet": SUMM_SHEET,
            "summ_rows_imported": len(records),
            "skipped_rows": skipped_rows,
            "sales_office": sales_office,
            "report_period": report_period,
            "source_file": source_file,
            "preview": records[:5],
        }

    client = db_client if db_client is not None else supabase
    history_key = f"{report_period}|{sales_office}"

    # Ambil master sekali agar pencocokan konsisten sepanjang impor.
    master_rows = _get_salesmen_master(client)

    inserted = 0
    updated = 0

    for record in records:
        existing = _find_matching_salesman(
            master_rows,
            record,
        )

        history_entry = {
            # Ini NPK dari file SUMM, bukan pengganti NPK master.
            "npk": record["npk"],
            "nama": record["nama"],
            "sales_office": sales_office,
            "report_period": report_period,
            "final_grade": record["final_grade"],
            "final_score": record["final_score"],
            "strategic_alignment": (
                record["strategic_alignment"]
            ),
            "result_sales_achiev_gr": (
                record["result_sales_achiev_gr"]
            ),
            "source_file": source_file,
            "updated_at": record["updated_at"],
        }

        if existing:
            history = existing.get("summ_history") or {}

            if not isinstance(history, dict):
                raise ValueError(
                    "summ_history untuk master "
                    f"{existing.get('nama')} "
                    f"(NPK master {existing.get('npk')}) "
                    "bukan objek JSON. Periksa data sebelum impor."
                )

            # Salin histori sebelumnya.
            # Key periode + office yang sama akan diganti.
            new_history = dict(history)
            new_history[history_key] = history_entry

            master_id = existing.get("id")

            if master_id is None:
                raise ValueError(
                    "Master salesman tidak memiliki kolom id. "
                    "Impor dibatalkan agar update tidak mengenai "
                    "lebih dari satu baris."
                )

            (
                client.table("salesmen")
                .update({
                    "summ_history": new_history,
                })
                .eq("id", master_id)
                .execute()
            )

            # Perbarui cache lokal agar record berikutnya
            # menggunakan histori paling baru.
            existing["summ_history"] = new_history
            updated += 1

        else:
            # Tidak ditemukan master dengan nama + office yang cocok
            # dan NPK SUMM belum digunakan master lain.
            insert_payload = {
                "npk": record["npk"],
                "nama": record["nama"],
                "sales_office": sales_office,
                "summ_history": {
                    history_key: history_entry,
                },
            }

            response = (
                client.table("salesmen")
                .insert(insert_payload)
                .execute()
            )

            inserted += 1

            # Simpan row baru ke cache untuk pemeriksaan berikutnya.
            inserted_rows = response.data or []

            if inserted_rows:
                master_rows.extend(inserted_rows)
            else:
                # Jika respons insert tidak mengembalikan row,
                # tambahkan representasi minimal ke cache.
                master_rows.append({
                    "id": None,
                    "npk": record["npk"],
                    "nama": record["nama"],
                    "sales_office": sales_office,
                    "grup_product": None,
                    "summ_history": {
                        history_key: history_entry,
                    },
                })

    return {
        "message": "Impor SUMM berhasil.",
        "dry_run": False,
        "sheet": SUMM_SHEET,
        "summ_rows_imported": len(records),
        "summ_rows_inserted": inserted,
        "summ_rows_updated": updated,
        "skipped_rows": skipped_rows,
        "sales_office": sales_office,
        "report_period": report_period,
        "source_file": source_file,
    }


def get_all_salesmen():
    response = (
        supabase.table("salesmen")
        .select("*")
        .execute()
    )
    return response.data or []


def get_filter_options():
    response = (
        supabase.table("salesmen")
        .select("sales_office, grup_product, nama, npk")
        .execute()
    )

    raw_data = response.data or []

    return {
        "raw": raw_data,
        "offices": sorted({
            item["sales_office"]
            for item in raw_data
            if item.get("sales_office")
        }),
        "groups": sorted({
            item["grup_product"]
            for item in raw_data
            if item.get("grup_product")
        }),
        "salesmen": sorted({
            item["nama"]
            for item in raw_data
            if item.get("nama")
        }),
    }


def get_salesman_performance(
    office: str | None = None,
    group: str | None = None,
    salesman: str | None = None,
):
    query = supabase.table("salesmen").select("*, targets(*)")

    if salesman and salesman.strip():
        query = query.ilike(
            "nama",
            f"%{salesman.strip()}%",
        )

    if office and office.strip():
        query = query.eq(
            "sales_office",
            office.strip(),
        )

    if group and group.strip():
        query = query.eq(
            "grup_product",
            group.strip(),
        )

    response = query.execute()
    return response.data or []


def get_salesman_summ(
    office: str | None = None,
    group: str | None = None,
    salesman: str | None = None,
    report_period: str | None = None,
):
    """Membaca histori SUMM dari salesmen.summ_history."""
    query = supabase.table("salesmen").select(
        "npk, nama, sales_office, grup_product, summ_history"
    )

    if salesman and salesman.strip():
        query = query.ilike(
            "nama",
            f"%{salesman.strip()}%",
        )

    if office and office.strip():
        query = query.eq(
            "sales_office",
            office.strip(),
        )

    if group and group.strip():
        query = query.eq(
            "grup_product",
            group.strip(),
        )

    response = query.execute()
    rows = response.data or []

    results = []

    for row in rows:
        history = row.get("summ_history") or {}

        if not isinstance(history, dict):
            continue

        for entry in history.values():
            if not isinstance(entry, dict):
                continue

            if (
                report_period
                and str(
                    entry.get("report_period", "")
                ).strip()
                != str(report_period).strip()
            ):
                continue

            if office and (
                str(
                    entry.get("sales_office", "")
                ).strip()
                != office.strip()
            ):
                continue

            results.append({
                **entry,
                # Utamakan NPK dan nama dari histori SUMM.
                # NPK master digunakan hanya jika histori kosong.
                "npk": entry.get("npk") or row.get("npk"),
                "nama": entry.get("nama") or row.get("nama"),
                "sales_office": (
                    entry.get("sales_office")
                    or row.get("sales_office")
                ),
                "grup_product": row.get("grup_product"),
            })

    results.sort(
        key=lambda item: (
            str(item.get("nama", "")).casefold(),
            str(item.get("npk", "")),
        )
    )

    return results
