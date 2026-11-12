# 🏗️ Hệ Thống AI Thiết Kế Điện MEP Tự Động từ IFC (Phần B)

Dự án đọc thiết bị MEP hiện có từ IFC (Tool 7), chuyển dữ liệu qua `proposal_provider`, rồi kiểm định phương án trả về bằng Tool 9. Hiện provider dùng proposal mẫu; sau này thay provider bằng hàm gọi AI. Python không tự tính tọa độ/hướng quay.

## 🚀 3 Bước Chạy Nhanh Trong VS Code:

### Cách 1: Dùng phím tắt F5 (Run & Debug)
1. Nhấn **`F5`** (hoặc mở tab **Run & Debug** `Ctrl + Shift + D` ở thanh bên trái).
2. Chọn một trong các cấu hình có sẵn:
   - `🚀 1. Chạy kiểm tra Phần B (Tools 7, 9)`
   - `🤖 2. Chạy Demo Agent Pipeline`
3. Nhấn **Play ▶** để chạy và xem kết quả trực tiếp trên Terminal bên dưới.

### Cách 2: Dùng phím tắt Build Task
- Nhấn tổ hợp phím **`Ctrl + Shift + B`** $\rightarrow$ Hệ thống sẽ tự động kích hoạt bài test chuyên sâu cho Phần B.

### Cách 3: Dùng Terminal tích hợp của VS Code
Mở Terminal (`Ctrl + ~`) và gõ:
```powershell
python test_part_b.py
```
hoặc:
```powershell
python demo_part_b_pipeline.py
```

---

## 📂 Cấu trúc thư mục:
- `agent/agent_part_b.py`: Điều phối Tool 7 -> proposal provider -> kiểm tra schema -> Tool 9. Provider hiện là mẫu, chưa kết nối AI.
- `tools_part_b.py`: Cung cấp Tool 7 (`ifc_list_placed_devices`) và Tool 9 (`engine_validate_placements`).
- `core/placement_engine.py`: Kiểm định bounds, host, va chạm cửa, cao độ và thiết bị trùng/gần nhau.
- `core/ifc_reader.py`: Module phân tích file IFC (không gian, tường, cửa đi, cửa sổ).
- `data/ctr.ifc`: File IFC mặc định dùng cho demo và Tool 7/Tool 9.
- `data/playbook.db`: Database SQLite chứa catalog thiết bị và tiêu chuẩn kỹ thuật.
