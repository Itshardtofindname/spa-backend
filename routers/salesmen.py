
from datetime import date

from database import supabase
from fastapi import APIRouter, HTTPException, Query

from services.salesmen_service import (
    get_all_salesmen,
    get_filter_options,
    get_salesman_performance,
    get_salesman_summ,
)
from services.scorecard_service import get_scorecard_productivity

router = APIRouter(
    prefix="/api/salesmen",
    tags=["Salesmen Performance"],
)


@router.get("/")
def read_salesmen():
    return get_all_salesmen()


@router.get("/options")
def read_filter_options():
    return get_filter_options()


@router.get("/performance")
def read_salesman_performance(
    office: str = Query(..., min_length=1),
    group: str = Query(..., min_length=1),
    salesman: str = Query(..., min_length=1),
    report_period: date = Query(default=date(2026, 9, 1)),
):
    # Ambil data utama salesman dari tabel salesmen dan targets.
    results = get_salesman_performance(
        office=office,
        group=group,
        salesman=salesman,
    )

    if not results:
        raise HTTPException(
            status_code=404,
            detail="Data performa salesman tidak ditemukan",
        )

    if len(results) > 1:
        raise HTTPException(
            status_code=409,
            detail=(
                "Kombinasi Sales Office, Group Product, dan Salesman "
                "menghasilkan lebih dari satu data."
            ),
        )

    sales_data = results[0]
    sales_npk = str(sales_data.get("npk", "")).strip()

    # Ambil histori SUMM untuk salesman dan periode yang dipilih.
    summ_rows = get_salesman_summ(
        office=office,
        group=group,
        salesman=sales_data.get("nama"),
        report_period=report_period.isoformat(),
    )

    # Cocokkan berdasarkan NPK, bukan nama saja.
    summ_data = next(
        (
            row
            for row in summ_rows
            if str(row.get("npk", "")).strip() == sales_npk
        ),
        None,
    )

    # Nilai default berasal dari Scorecard lama.
    # Nilai ini tetap dipakai jika SUMM periode tersebut tidak ditemukan.
    targets = sales_data.get("targets") or []
    target_info = targets[0] if targets else {}

    score = float(target_info.get("skor_kpi", 10.0))
    pillar_1 = float(target_info.get("pilar_result", 3.0))
    pillar_2 = float(target_info.get("pilar_strategic", 5.0))
    pillar_3 = float(target_info.get("pilar_process", 2.0))

    status_grade = (
        "HIGH" if score >= 11
        else "MEDIUM" if score >= 9
        else "LOW"
    )

    # None berarti SUMM tidak ditemukan, sehingga scorecard lama dipakai.
    summ_scorecard = None

    if summ_data is not None:
        strategic = summ_data.get("strategic_alignment") or {}

        # Nilai akhir dan status mengikuti data SUMM.
        score = float(summ_data.get("final_grade", 0))
        status_grade = str(
            summ_data.get("final_score") or "LOW"
        ).upper()

        # Breakdown tiga pilar SUMM.
        pillar_1 = float(
            summ_data.get("result_sales_achiev_gr", 0)
        )
        pillar_2 = (
            float(strategic.get("cross_up_sell", 0))
            + float(strategic.get("active_outlet", 0))
            + float(strategic.get("repeat_order", 0))
        )
        pillar_3 = float(strategic.get("process_dks", 0))

        # Lima indikator SUMM.
        summ_scorecard = [
            {
                "value": str(
                    float(summ_data.get("result_sales_achiev_gr", 0))
                ),
                "aspect": "RESULT",
                "indicator": "Sales Achievement & Growth",
            },
            {
                "value": str(float(strategic.get("cross_up_sell", 0))),
                "aspect": "STRATEGIC ALIGNMENT",
                "indicator": "Cross Selling - Up Selling",
            },
            {
                "value": str(float(strategic.get("active_outlet", 0))),
                "aspect": "STRATEGIC ALIGNMENT",
                "indicator": "Active Outlet (OA)",
            },
            {
                "value": str(float(strategic.get("repeat_order", 0))),
                "aspect": "STRATEGIC ALIGNMENT",
                "indicator": "Repeat Order",
            },
            {
                "value": str(float(strategic.get("process_dks", 0))),
                "aspect": "PROCESS",
                "indicator": "DKS (Daftar Kunjungan Sales)",
            },
        ]

    office_raw = sales_data.get(
        "sales_office",
        "S001 - SO SERANG",
    )
    office_name = (
        office_raw.split(" - ")[-1].replace("SO ", "")
        if " - " in office_raw
        else office_raw
    )

    # Ambil matriks produktivitas dari Excel untuk office dan periode
    # yang sama. Sheet Produktifitas tetap dipertahankan.
    try:
        productivity_rows = get_scorecard_productivity(
            sales_office=sales_data.get("sales_office", office),
            report_period=report_period.isoformat(),
        )

        # Hilangkan baris ringkasan HIGH, MEDIUM, LOW yang bukan karyawan.
        productivity_rows = [
            row
            for row in (productivity_rows or [])
            if str(row.get("npk", "")).strip().isdigit()
        ]

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Gagal mengambil data produktivitas Scorecard.",
        ) from exc

    return {
        "id": sales_data.get("id"),
        "salesOffice": sales_data.get("sales_office", "-"),
        "grupProduct": sales_data.get("grup_product", "-"),
        "salesman": sales_data.get("nama", "-"),

        "identity": {
            "nama": sales_data.get("nama", "-"),
            "npk": sales_data.get("npk", "-"),
            "jabatan": "Field Salesman Domestic",
            "wilayah": f"SO {office_name}",
            "totalOutlet": (
                f"{target_info.get('total_outlet', 30)} Outlet Aktif"
            ),
            "rankSo": target_info.get("rank_so", "#1 dari 18 Sales"),
            "avgAchievement": target_info.get(
                "avg_achievement",
                "70.0%",
            ),
        },

        "finalGrade": {
            "score": f"{score:.1f}".replace(".", ","),
            "status": status_grade,
            "batasLulus": "≥ 9.0",
            "keterangan": (
                "Performa individu memenuhi standar operasional."
            ),
        },

        "breakdownPilar": {
            "pilar1": pillar_1,
            "pilar2": pillar_2,
            "pilar3": pillar_3,
        },

        # Gunakan scorecard SUMM jika tersedia.
        # Jika tidak, pertahankan scorecard lama.
        "scorecard": (
            summ_scorecard
            if summ_scorecard is not None
            else sales_data.get("scorecard", [])
        ),

        # Produktivitas tetap diambil dari Excel seperti sebelumnya.
        "productivityScorecard": {
            "salesOffice": sales_data.get("sales_office", office),
            "reportPeriod": report_period.isoformat(),
            "count": len(productivity_rows),
            "data": productivity_rows,
        },

        # Properti lama lainnya tidak diubah.
        "trackRecord": sales_data.get("trackRecord", []),
        "repeatOrderRendah": sales_data.get("repeatOrderRendah", []),
        "kekuatanSales": sales_data.get("kekuatanSales", []),
        "catatanSupervisor": sales_data.get(
            "catatanSupervisor",
            "",
        ),
    }


@router.get("/{npk}/stores")
def get_salesman_stores(npk: str):
    """Ambil data salesman dan daftar toko yang ditugaskan kepadanya."""
    try:
        salesman_response = (
            supabase.table("salesmen")
            .select("id,npk,nama,sales_office,grup_product")
            .eq("npk", npk)
            .limit(1)
            .execute()
        )

        if not salesman_response.data:
            raise HTTPException(
                status_code=404,
                detail="Salesman tidak ditemukan.",
            )

        salesman = salesman_response.data[0]

        stores_response = (
            supabase.table("stores")
            .select("*")
            .eq("salesman_id", salesman["id"])
            .order("nama_toko")
            .execute()
        )

        stores = stores_response.data or []
        customer_codes = [
            store["cust_no"]
            for store in stores
        ]

        profiles_by_code = {}

        if customer_codes:
            profiles_response = (
                supabase.table("store_profiles")
                .select("*")
                .in_("store_id", customer_codes)
                .execute()
            )

            profiles_by_code = {
                profile["store_id"]: profile
                for profile in (profiles_response.data or [])
            }

        connected_stores = []

        for store in stores:
            profile = profiles_by_code.get(store["cust_no"])

            connected_stores.append({
                **store,
                "sellingToolkit": profile,
            })

        return {
            "salesman": salesman,
            "totalStores": len(connected_stores),
            "stores": connected_stores,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Gagal mengambil daftar toko milik salesman.",
        ) from exc
