import os
from database import supabase

sales_office_list = [
    "S001 - SO SERANG", "S002 - SO UTIM", "S003 - SO SERPONG",
    "S004 - SO SELATAN", "S005 - SO PUSPAR"
]

grup_product_list = [
    "TL1 - AP2", "TL2 - ATI-ATU-CCO-OA2", "TL3 - AB4-ATD-OA4",
    "TL4A - AP4-F/NF", "TL4B - INB-TI4-TU4", "TL5 - GS2-KY2-KYZ",
    "TL6 - GS4-KY4-AKB", "TL7 - FP2-FTU-FBO"
]

salesman_list = [
    "4225 - IQBAL", "5178 - TEGAR", "4025 - RIO", "2528 - HENDRY",
    "5060 - ANGGA", "5207 - AZIZ", "4618 - RAVI", "4410 - EDI PURWANTO",
    "2451 - ABU YAJID", "4402 - BERNARDO", "4413 - LUKMAN", "4947 - RAHMAT",
    "4405 - KAMAL", "1584 - SENO AJI", "3474 - MARTINO", "4561 - HENDRA", "4906 - DAMAR"
]

def generate_and_seed():
    print("🚀 Memulai proses seeding data performa salesman ke Supabase...")
    index = 0

    for office in sales_office_list:
        office_name = office.split(" - ")[-1].replace("SO ", "")
        
        for group in grup_product_list:
            for sales_item in salesman_list:
                npk, nama = sales_item.split(" - ", 1)

                # Hitung logic formula sesuai JS
                achievement = 70 + ((index * 7) % 25)
                sales_score = 3.0 if achievement >= 85 else (2.0 if achievement >= 75 else 1.0)
                active_outlet_score = 3.0 if index % 3 == 0 else (2.0 if index % 3 == 1 else 1.0)
                repeat_order_score = 2.0 if index % 2 == 0 else 1.0
                cross_sell_score = 3.0 if index % 3 == 0 else 2.0
                dks_score = 3.0 if index % 4 == 0 else 2.0

                final_score = sales_score + active_outlet_score + repeat_order_score + cross_sell_score + dks_score
                pilar_strategic = active_outlet_score + repeat_order_score + cross_sell_score
                grade = "A" if final_score >= 11 else ("B" if final_score >= 9 else "C")

                scorecard = [
                    {"aspect": "RESULT", "indicator": "Sales Achievement & Growth", "value": str(sales_score)},
                    {"aspect": "STRATEGIC ALIGNMENT", "indicator": "Cross Selling - Up Selling", "value": str(cross_sell_score)},
                    {"aspect": "STRATEGIC ALIGNMENT", "indicator": "Active Outlet (OA)", "value": str(active_outlet_score)},
                    {"aspect": "STRATEGIC ALIGNMENT", "indicator": "Repeat Order", "value": str(repeat_order_score)},
                    {"aspect": "PROCESS", "indicator": "DKS (Daftar Kunjungan Sales)", "value": str(dks_score)}
                ]

                track_record = [{
                    "territory": f"SO {office_name}",
                    "jan": f"{70 + (index % 15)}%", "feb": f"{72 + (index % 15)}%", "mar": f"{75 + (index % 15)}%",
                    "apr": f"{74 + (index % 15)}%", "mei": f"{76 + (index % 15)}%", "jun": f"{78 + (index % 15)}%",
                    "sep": "AKTIF", "avgTotal": f"{final_score:.1f}".replace(".", ",")
                }]

                supervisor_notes = (
                    f"{nama} menunjukkan performa yang konsisten dan memenuhi target."
                    if final_score >= 11 else
                    f"{nama} membutuhkan evaluasi dan pendampingan lebih lanjut."
                )

                # 1. Upsert Salesman
                salesman_payload = {
                    "npk": npk,
                    "nama": nama,
                    "sales_office": office,
                    "grup_product": group,
                    "scorecard": scorecard,
                    "trackRecord": track_record,
                    "repeatOrderRendah": [{"store": f"Outlet {nama}", "detail": f"PO Terakhir: {10 + (index % 20)} Ags"}],
                    "catatanSupervisor": supervisor_notes
                }
                
                res_sales = supabase.table("salesmen").upsert(salesman_payload, on_conflict="npk").execute()
                
                if res_sales.data:
                    salesman_id = res_sales.data[0]["id"]
                    
                    # 2. Upsert Target Performance
                    target_payload = {
                        "salesman_id": salesman_id,
                        "periode": "Oct-26",
                        "target_omzet": 150000000,
                        "realisasi_omzet": 120000000,
                        "skor_kpi": final_score,
                        "grade": grade,
                        "total_outlet": 30 + (index % 25),
                        "rank_so": f"#{(index % 18) + 1} dari 18 Sales",
                        "avg_achievement": f"{achievement}.0%",
                        "pilar_result": sales_score,
                        "pilar_strategic": pilar_strategic,
                        "pilar_process": dks_score
                    }
                    supabase.table("targets").upsert(target_payload, on_conflict="id").execute()

                index += 1

    print(f"✅ Berhasil menyinkronkan {index} kombinasi data performa ke Supabase!")

if __name__ == "__main__":
    generate_and_seed()