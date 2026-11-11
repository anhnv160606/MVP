"""Small in-memory Supabase fixture using representative Playbook rows."""
from __future__ import annotations


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.filters = {}

    def select(self, _columns):
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def execute(self):
        rows = [row for row in self.rows if all(row.get(k) == v for k, v in self.filters.items())]
        return _Result(rows)


class SamplePlaybookClient:
    """Only the chained table/select/eq/execute calls used by the tools."""
    data = {
        "mo_dun_thiet_bi": [{
            "id": 1, "ten_cau_hinh": "THIẾT BỊ TỰ ĐỘNG 1 CỰC 6 A",
            "nhom_thiet_bi": "Áptômát tép", "so_cuc_goi_y": 1,
            "dong_dinh_muc_goi_y_a": 6, "be_rong_minh_hoa_mm": 18,
            "dong_ro_tac_dong_ma": "Theo sản phẩm", "kha_nang_cat_ka": "Theo sản phẩm",
            "chuc_nang": "Ngắn mạch và quá tải", "luu_y": "Dữ liệu minh họa",
            "tinh_trang_kiem_chung": "Chờ phê duyệt",
        }],
        "lop_phu_tai": [{
            "ma_lop_phu_tai": "TẢI-001", "ten_lop_phu_tai": "Chiếu sáng chung",
            "nhom_phu_tai": "Chiếu sáng", "cong_suat_min_w": 8,
            "cong_suat_max_w": 14, "he_so_nhu_cau": None,
            "nguon_quy_tac": "Giả định minh họa; tham chiếu TCVN 9206:2012",
            "tinh_trang_kiem_chung": "Tham khảo",
        }],
        "lop_dau_cuoi": [{
            "ma_lop_dau_cuoi": "ĐIỂM-001", "ten_lop_dau_cuoi": "Điểm đèn trần",
            "lop_phu_tai_ma": "TẢI-001", "kieu_cap_dien": "Một pha hai dây",
            "he_so_cong_suat_tam": None, "nhom_mach": "Chiếu sáng",
            "kieu_diem_ket_noi": "Điểm cấp nguồn đèn", "khong_gian_ap_dung": "Trong phòng",
            "gioi_han_sut_ap_pct": None, "ghi_chu": "Theo yêu cầu chiếu sáng",
            "tinh_trang_kiem_chung": "Chờ phê duyệt",
        }],
        "quy_tac_doi_tuong": [{
            "ma_quy_tac": "NHẬN-001", "ten_quy_tac": "đèn trần",
            "truong_ifc_doi_chieu": "Tên hoặc thuộc tính đối tượng",
            "tu_khoa_nhan_dien": ["đèn", "đèn âm trần", "đèn led", "đèn rọi"],
            "phuong_thuc_khop": "Không phân biệt hoa thường",
            "lop_dau_cuoi_ma": "ĐIỂM-001",
            "khong_gian_thuong_gap": "Phòng khách; phòng ngủ; hành lang; bếp",
            "dieu_kien_bo_sung": "Cần kiểm tra không gian và tọa độ",
            "xu_ly_canh_bao": "Tạo điểm chờ điện nếu có vị trí chắc chắn",
            "tinh_trang_kiem_chung": "Tham khảo",
        }],
        "quy_tac_phong": [{
            "id": 1, "loai_khong_gian": "Phòng ngủ",
            "thiet_bi_thuong_xet": "Đèn, ổ cắm, điều hòa nếu có",
            "nguyen_tac_bo_tri": "Hai bên giường và khu bàn làm việc khi có",
            "cao_do_lap_dat": "Theo nội thất và an toàn",
            "kiem_tra_bo_sung": "Tách mạch điều hòa khi phù hợp",
            "gioi_han_canh_bao": "Phụ thuộc chủ nhà",
            "loai_du_lieu": "Quy tắc thiết kế tham khảo",
        }],
        "quy_tac_thiet_ke": [{
            "ma_quy_tac": "QT-03", "ten_quy_tac": "Không tự mặc định hệ số nhu cầu",
            "phan_loai": "Quy tắc dữ liệu",
            "dau_vao_can_thiet": "Nhập hệ số phù hợp dự án / trường hợp áp dụng",
            "tieu_chi_xu_ly": "Không gán 1 hoặc giá trị tùy tiện cho mọi nhóm tải",
            "nguon_tham_khao": "TCVN 9206:2012",
            "trang_thai_xac_nhan": "Yêu cầu phê duyệt",
        }],
        "nguon_tieu_chuan": [{
            "so_hieu_van_ban": "TCVN 9206:2012",
            "pham_vi_lien_quan": "Đặt thiết bị điện trong nhà ở và công trình công cộng",
            "loai_tai_lieu": "Tiêu chuẩn",
            "duong_dan_tra_cuu": "https://tieuchuan.vsqi.gov.vn/",
            "luu_y_ap_dung": "Chưa đối chiếu đầy đủ điều khoản chi tiết",
            "trang_thai_ra_soat": "Chưa đối chiếu đầy đủ điều khoản chi tiết",
        }],
    }

    def table(self, table_name):
        return _Query(self.data.get(table_name, []))
