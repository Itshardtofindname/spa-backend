
from database import supabase


def get_all_salesmen():
    response = supabase.table("salesmen").select("*").execute()
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
        query = query.ilike("nama", f"%{salesman.strip()}%")

    if office and office.strip():
        query = query.eq("sales_office", office.strip())

    if group and group.strip():
        query = query.eq("grup_product", group.strip())

    response = query.execute()
    return response.data or []
