from typing import Any
from supabase import Client

def list_room_rules(client: Client) -> list[dict[str, Any]]:
    """Liệt kê các quy tắc phòng từ Supabase"""
    response = (client.table("quy_tac_phong").select("loai_khong_gian,"
            "thiet_bi_thuong_xet,"
            "nguyen_tac_bo_tri,"
            "cao_do_lap_dat").order("loai_khong_gian").limit(5).execute())

    return response.data

