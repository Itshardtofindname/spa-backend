from fastapi import APIRouter, HTTPException, Query
from database import supabase
from typing import Optional

router = APIRouter(prefix="/api/salesmen", tags=["Salesmen Performance"])

@router.get("/")
def get_all_salesmen():
    res = supabase.table("salesmen").select("*").execute()
    return res.data or []

@router.get("/options")
def get_filter_options():
    """Mengambil master relasi Sales Office, Grup Product, dan Salesman"""
    res = supabase.table("salesmen").select("sales_office, grup_product, nama, npk").execute()
    if not res.data:
        return {"raw": [], "offices": [], "groups": [], "salesmen": []}

    raw_data = res.data
    offices = sorted(list(set([item["sales_office"] for item in raw_data if item.get("sales_office")])))
    groups = sorted(list(set([item["grup_product"] for item in raw_data if item.get("grup_product")])))
    salesmen = sorted(list(set([item["nama"] for item in raw_data if item.get("nama")])))

    return {
        "raw": raw_data,
        "offices": offices,
        "groups": groups,
        "salesmen": salesmen
    }

@router.get("/performance")
def get_salesman_performance(
    office: Optional[str] = Query(None),
    group: Optional[str] = Query(None),
    salesman: Optional[str] = Query(None)
):
    """Mengambil detail data performa salesman berdasarkan kombinasi filter"""
    query = supabase.table("salesmen").select("*, targets(*)")

    if salesman and salesman.strip():
        query = query.ilike("nama", f"%{salesman.strip()}%")
    if office and office.strip():
        query = query.eq("sales_office", office)
    if group and group.strip():
        query = query.eq("grup_product", group)

    res = query.execute()

    if not res.data:
        raise HTTPException(status_code=404, detail="Data performa salesman tidak ditemukan")

    sales_data = res.data[0]
    targets = sales_data.get("targets", [])
    target_info = targets[0] if targets else {}

    skor = float(target_info.get("skor_kpi", 10.0))
    pilar1 = float(target_info.get("pilar_result", 3.0))
    pilar2 = float(target_info.get("pilar_strategic", 5.0))
    pilar3 = float(target_info.get("pilar_process", 2.0))

    office_raw = sales_data.get("sales_office", "S001 - SO SERANG")
    office_name = office_raw.split(" - ")[-1].replace("SO ", "") if " - " in office_raw else office_raw

    status_grade = "HIGH" if skor >= 11.0 else ("MEDIUM" if skor >= 9.0 else "LOW")

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
            "avgAchievement": target_info.get("avg_achievement", "70.0%")
        },
        "finalGrade": {
            "score": f"{skor:.1f}".replace(".", ","),
            "status": status_grade,
            "batasLulus": "≥ 9.0",
            "keterangan": "Performa individu memenuhi standar operasional."
        },
        "breakdownPilar": {
            "pilar1": pilar1,
            "pilar2": pilar2,
            "pilar3": pilar3
        },
        "scorecard": sales_data.get("scorecard", []),
        "trackRecord": sales_data.get("trackRecord", []),
        "repeatOrderRendah": sales_data.get("repeatOrderRendah", []),
        "kekuatanSales": sales_data.get("kekuatanSales", []),
        "catatanSupervisor": sales_data.get("catatanSupervisor", "")
    }