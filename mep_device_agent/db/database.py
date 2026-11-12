import sqlite3
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "playbook.db"
SCHEMA_PATH = BASE_DIR / "db" / "schema.sql"

def get_db_connection():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        cursor.executescript(f.read())
        
    # Check if catalog already populated
    cursor.execute("SELECT COUNT(*) FROM device_catalog")
    if cursor.fetchone()[0] == 0:
        # Populate standard devices
        devices = [
            ("socket_standard", "Ổ cắm đôi 2P+E 16A âm tường", "socket", 0.3, 300.0, 220, "IfcOutlet"),
            ("socket_waterproof", "Ổ cắm chống nước IP55 16A", "socket", 1.1, 400.0, 220, "IfcOutlet"),
            ("socket_floor", "Ổ cắm âm sàn Floor Pop-up 16A", "socket", 0.05, 300.0, 220, "IfcOutlet"),
            ("switch_single", "Công tắc đơn 1 chiều 10A", "switch", 1.2, 10.0, 220, "IfcSwitchingDevice"),
            ("switch_double", "Công tắc đôi 1 chiều 10A", "switch", 1.2, 10.0, 220, "IfcSwitchingDevice"),
            ("light_panel_600x600", "Đèn LED Panel 600x600 40W", "light", 2.8, 40.0, 220, "IfcFlowTerminal"),
            ("data_point_cat6", "Ổ cắm mạng đôi RJ45 Cat6", "data", 0.3, 50.0, 220, "IfcOutlet"),
        ]
        cursor.executemany(
            "INSERT INTO device_catalog (device_type, name, category, standard_height, default_load_w, voltage, ifc_class) VALUES (?, ?, ?, ?, ?, ?, ?)",
            devices
        )

        # Standard rules for 'office' & 'residential'
        rules = [
            (
                "office",
                "open_office",
                json.dumps(["socket_standard", "switch_double", "light_panel_600x600", "data_point_cat6"]),
                json.dumps({"socket": 0.3, "switch": 1.2, "light": 2.8, "data": 0.3}),
                json.dumps({"class": "power_general", "demand_factor": 0.7, "density_w_m2": 25.0, "description": "Tải ổ cắm văn phòng và máy trạm"})
            ),
            (
                "office",
                "meeting_room",
                json.dumps(["socket_standard", "socket_floor", "switch_double", "light_panel_600x600"]),
                json.dumps({"socket": 0.3, "floor_socket": 0.05, "switch": 1.2, "light": 2.8}),
                json.dumps({"class": "power_av_meeting", "demand_factor": 0.8, "density_w_m2": 30.0, "description": "Tải phòng họp, máy chiếu, thiết bị hội thảo"})
            ),
            (
                "office",
                "director_room",
                json.dumps(["socket_standard", "switch_single", "light_panel_600x600", "data_point_cat6"]),
                json.dumps({"socket": 0.3, "switch": 1.2, "light": 2.8}),
                json.dumps({"class": "power_executive", "demand_factor": 0.75, "density_w_m2": 20.0, "description": "Tải phòng giám đốc/trưởng phòng"})
            ),
            (
                "office",
                "pantry_kitchen",
                json.dumps(["socket_waterproof", "switch_single", "light_panel_600x600"]),
                json.dumps({"socket": 1.1, "switch": 1.2, "light": 2.8}),
                json.dumps({"class": "power_appliance", "demand_factor": 0.6, "density_w_m2": 45.0, "description": "Tải thiết bị bếp, lò vi sóng, tủ lạnh, ấm đun"})
            ),
            (
                "office",
                "wc",
                json.dumps(["socket_waterproof", "switch_single"]),
                json.dumps({"socket": 1.2, "switch": 1.2}),
                json.dumps({"class": "power_sanitary", "demand_factor": 0.5, "density_w_m2": 15.0, "description": "Tải khu vệ sinh, máy sấy tay"})
            )
        ]
        cursor.executemany(
            "INSERT INTO standard_rules (standard_profile, space_type, required_devices, height_rules, load_class_summary) VALUES (?, ?, ?, ?, ?)",
            rules
        )
        
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully at", DB_PATH)
