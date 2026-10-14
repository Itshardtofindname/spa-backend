
from fastapi import APIRouter, HTTPException, Query

from services.salesmen_service import (
    get_all_salesmen,
    get_filter_options,
    get_salesman_performance,
)

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
    office: str | None = Query(default=None),
    group: str | None = Query(default=None),
    salesman: str | None = Query(default=None),
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
            "totalOutlet": f"{target_info.get('total_outlet', 30)} Outlet Aktif",
            "rankSo": target_info.get("rank_so", "#1 dari 18 Sales"),
            "avgAchievement": target_info.get("avg_achievement", "70.0%"),
        },
        "finalGrade": {
            "score": f"{score:.1f}".replace(".", ","),
            "status": status_grade,
            "batasLulus": "≥ 9.0",
            "keterangan": "Performa individu memenuhi standar operasional.",
        },
        "breakdownPilar": {
            "pilar1": pillar_1,
            "pilar2": pillar_2,
            "pilar3": pillar_3,
        },
        "scorecard": sales_data.get("scorecard", []),
        "trackRecord": sales_data.get("trackRecord", []),
        "repeatOrderRendah": sales_data.get("repeatOrderRendah", []),
        "kekuatanSales": sales_data.get("kekuatanSales", []),
        "catatanSupervisor": sales_data.get("catatanSupervisor", ""),
    }
