from datetime import date

from database import supabase
from fastapi import APIRouter, HTTPException, Query

from services.salesmen_service import (
    get_all_salesmen,
    get_filter_options,
    get_salesman_performance,
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
    targets = sales_data.get("targets") or []
    target_info = targets[0] if targets else {}

    score = float(target_info.get("skor_kpi", 10.0))
    pillar_1 = float(target_info.get("pilar_result", 3.0))
    pillar_2 = float(target_info.get("pilar_strategic", 5.0))
    pillar_3 = float(target_info.get("pilar_process", 2.0))

    office_raw = sales_data.get("sales_office", "S001 - SO SERANG")
    office_name = (
        office_raw.split(" - ")[-1].replace("SO ", "")
        if " - " in office_raw
        else office_raw
    )

    status_grade = (
        "HIGH" if score >= 11
        else "MEDIUM" if score >= 9
        else "LOW"
    )

    # Ambil matriks produktivitas dari Excel untuk office dan periode
    # yang sama. Data ini mencakup seluruh salesman di office tersebut.
    try:
        productivity_rows = get_scorecard_productivity(
            sales_office=sales_data.get("sales_office", office),
            report_period=report_period.isoformat(),
        )

        # NPK karyawan harus berupa angka. Baris ringkasan seperti
        # HIGH, MEDIUM, dan LOW tidak ditampilkan sebagai karyawan.
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

        # Data scorecard lama tetap dipertahankan.
        "scorecard": sales_data.get("scorecard", []),

        # Data produktivitas Excel yang baru.
        "productivityScorecard": {
            "salesOffice": sales_data.get("sales_office", office),
            "reportPeriod": report_period.isoformat(),
            "count": len(productivity_rows),
            "data": productivity_rows,
        },

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