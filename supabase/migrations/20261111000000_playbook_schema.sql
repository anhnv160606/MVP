-- =====================================================================
-- Playbook điện nhà phố Việt Nam 2–5 tầng — SCHEMA
-- Sinh tự động từ file Excel (17 sheet = 17 bảng). Dữ liệu tham khảo, cần kỹ sư điện phê duyệt.
-- Tên bảng/cột: tiếng Việt không dấu, snake_case (tiện cho PostgREST/Supabase API).
-- =====================================================================

create domain trang_thai_kiem_chung as text
  check (value in ('Tham khảo', 'Cần xác minh', 'Chờ phê duyệt'));

create table huong_dan (
  id bigint generated always as identity primary key,
  muc text not null,
  noi_dung text not null,
  ghi_chu text not null,
  constraint huong_dan_muc_key unique (muc)
);
comment on table huong_dan is 'Sheet “Hướng dẫn”: Thông tin vận hành workbook';
comment on column huong_dan.muc is 'Mục';
comment on column huong_dan.noi_dung is 'Nội dung';
comment on column huong_dan.ghi_chu is 'Ghi chú';
alter table huong_dan enable row level security;
create policy "doc_playbook" on huong_dan for select to authenticated using (true);

create table cau_hinh_cong_trinh (
  ma_cau_hinh text primary key,
  loai_cong_trinh text not null,
  so_tang_thap_nhat int not null,
  so_tang_cao_nhat int not null,
  nguon_cap_goi_y text not null,
  dien_ap_goi_y_v text not null,
  kieu_day_lam_viec text not null,
  yeu_cau_xac_nhan text not null,
  trang_thai_du_lieu trang_thai_kiem_chung not null,
  constraint cau_hinh_cong_trinh_chk1 check (so_tang_thap_nhat <= so_tang_cao_nhat)
);
comment on table cau_hinh_cong_trinh is 'Sheet “Cấu hình công trình”: Các kiểu công trình áp dụng';
comment on column cau_hinh_cong_trinh.ma_cau_hinh is 'Mã cấu hình';
comment on column cau_hinh_cong_trinh.loai_cong_trinh is 'Loại công trình';
comment on column cau_hinh_cong_trinh.so_tang_thap_nhat is 'Số tầng thấp nhất';
comment on column cau_hinh_cong_trinh.so_tang_cao_nhat is 'Số tầng cao nhất';
comment on column cau_hinh_cong_trinh.nguon_cap_goi_y is 'Nguồn cấp gợi ý';
comment on column cau_hinh_cong_trinh.dien_ap_goi_y_v is 'Điện áp gợi ý (V)';
comment on column cau_hinh_cong_trinh.kieu_day_lam_viec is 'Kiểu dây làm việc';
comment on column cau_hinh_cong_trinh.yeu_cau_xac_nhan is 'Yêu cầu xác nhận';
comment on column cau_hinh_cong_trinh.trang_thai_du_lieu is 'Trạng thái dữ liệu';
alter table cau_hinh_cong_trinh enable row level security;
create policy "doc_playbook" on cau_hinh_cong_trinh for select to authenticated using (true);

create table may_bien_ap (
  id bigint generated always as identity primary key,
  thu_tu_uu_tien int not null,
  hang_muc text not null,
  loai text not null,
  dieu_kien_ap_dung text not null,
  dien_ap_phia_nguon_v numeric,
  dien_ap_phia_tai_v numeric,
  ket_luan_goi_y text not null,
  tinh_trang_kiem_chung trang_thai_kiem_chung not null,
  constraint may_bien_ap_thu_tu_uu_tien_key unique (thu_tu_uu_tien)
);
comment on table may_bien_ap is 'Sheet “Máy biến áp”: Xác định phạm vi ngoại lệ';
comment on column may_bien_ap.thu_tu_uu_tien is 'Thứ tự ưu tiên';
comment on column may_bien_ap.hang_muc is 'Hạng mục';
comment on column may_bien_ap.loai is 'Loại';
comment on column may_bien_ap.dieu_kien_ap_dung is 'Điều kiện áp dụng';
comment on column may_bien_ap.dien_ap_phia_nguon_v is 'Điện áp phía nguồn (V)';
comment on column may_bien_ap.dien_ap_phia_tai_v is 'Điện áp phía tải (V)';
comment on column may_bien_ap.ket_luan_goi_y is 'Kết luận gợi ý';
comment on column may_bien_ap.tinh_trang_kiem_chung is 'Tình trạng kiểm chứng';
alter table may_bien_ap enable row level security;
create policy "doc_playbook" on may_bien_ap for select to authenticated using (true);

create table tu_phan_phoi (
  id bigint generated always as identity primary key,
  thu_tu_uu_tien int not null,
  ma_tu_goi_y text not null,
  nha_cung_cap text not null,
  loai_tu text not null,
  kieu_day_lam_viec text not null,
  so_pha text not null,
  dien_ap_dinh_muc_v text not null,
  dong_danh_dinh_goi_y_a int not null,
  so_vi_tri_mo_dun int not null,
  cap_bao_ve_vo text not null,
  kieu_lap_dat text not null,
  thiet_bi_bao_ve_chinh text not null,
  kha_nang_chiu_ngan_mach text not null,
  ghi_chu text not null,
  tinh_trang_kiem_chung trang_thai_kiem_chung not null,
  constraint tu_phan_phoi_thu_tu_uu_tien_key unique (thu_tu_uu_tien),
  constraint tu_phan_phoi_ma_tu_goi_y_key unique (ma_tu_goi_y)
);
comment on table tu_phan_phoi is 'Sheet “Tủ phân phối”: Danh mục cấu hình tủ phân phối';
comment on column tu_phan_phoi.thu_tu_uu_tien is 'Thứ tự ưu tiên';
comment on column tu_phan_phoi.ma_tu_goi_y is 'Mã tủ gợi ý';
comment on column tu_phan_phoi.nha_cung_cap is 'Nhà cung cấp';
comment on column tu_phan_phoi.loai_tu is 'Loại tủ';
comment on column tu_phan_phoi.kieu_day_lam_viec is 'Kiểu dây làm việc';
comment on column tu_phan_phoi.so_pha is 'Số pha';
comment on column tu_phan_phoi.dien_ap_dinh_muc_v is 'Điện áp định mức (V)';
comment on column tu_phan_phoi.dong_danh_dinh_goi_y_a is 'Dòng danh định gợi ý (A)';
comment on column tu_phan_phoi.so_vi_tri_mo_dun is 'Số vị trí mô đun';
comment on column tu_phan_phoi.cap_bao_ve_vo is 'Cấp bảo vệ vỏ';
comment on column tu_phan_phoi.kieu_lap_dat is 'Kiểu lắp đặt';
comment on column tu_phan_phoi.thiet_bi_bao_ve_chinh is 'Thiết bị bảo vệ chính';
comment on column tu_phan_phoi.kha_nang_chiu_ngan_mach is 'Khả năng chịu ngắn mạch';
comment on column tu_phan_phoi.ghi_chu is 'Ghi chú';
comment on column tu_phan_phoi.tinh_trang_kiem_chung is 'Tình trạng kiểm chứng';
alter table tu_phan_phoi enable row level security;
create policy "doc_playbook" on tu_phan_phoi for select to authenticated using (true);

create table mo_dun_thiet_bi (
  id bigint generated always as identity primary key,
  ten_cau_hinh text not null,
  nhom_thiet_bi text not null,
  so_cuc_goi_y int not null,
  dong_dinh_muc_goi_y_a int,
  be_rong_minh_hoa_mm int,
  dong_ro_tac_dong_ma text not null,
  kha_nang_cat_ka text not null,
  chuc_nang text not null,
  luu_y text not null,
  tinh_trang_kiem_chung trang_thai_kiem_chung not null,
  constraint mo_dun_thiet_bi_ten_cau_hinh_key unique (ten_cau_hinh)
);
comment on table mo_dun_thiet_bi is 'Sheet “Mô đun thiết bị”: Thiết bị lắp trong tủ';
comment on column mo_dun_thiet_bi.ten_cau_hinh is 'Tên cấu hình';
comment on column mo_dun_thiet_bi.nhom_thiet_bi is 'Nhóm thiết bị';
comment on column mo_dun_thiet_bi.so_cuc_goi_y is 'Số cực gợi ý';
comment on column mo_dun_thiet_bi.dong_dinh_muc_goi_y_a is 'Dòng định mức gợi ý (A)';
comment on column mo_dun_thiet_bi.be_rong_minh_hoa_mm is 'Bề rộng minh họa (mm)';
comment on column mo_dun_thiet_bi.dong_ro_tac_dong_ma is 'Dòng rò tác động (mA)';
comment on column mo_dun_thiet_bi.kha_nang_cat_ka is 'Khả năng cắt (kA)';
comment on column mo_dun_thiet_bi.chuc_nang is 'Chức năng';
comment on column mo_dun_thiet_bi.luu_y is 'Lưu ý';
comment on column mo_dun_thiet_bi.tinh_trang_kiem_chung is 'Tình trạng kiểm chứng';
alter table mo_dun_thiet_bi enable row level security;
create policy "doc_playbook" on mo_dun_thiet_bi for select to authenticated using (true);

create table dau_noi_dien (
  id bigint generated always as identity primary key,
  ten_dau_noi text not null,
  loai_dau_noi text not null,
  tiet_dien_danh_dinh_mm2 numeric not null,
  dong_dien_cho_phep_a text not null,
  kieu_kep text not null,
  vi_tri_du_kien text not null,
  luu_y text not null,
  tinh_trang_kiem_chung trang_thai_kiem_chung not null,
  constraint dau_noi_dien_ten_dau_noi_key unique (ten_dau_noi)
);
comment on table dau_noi_dien is 'Sheet “Đầu nối điện”: Cầu đấu dây và đầu nối';
comment on column dau_noi_dien.ten_dau_noi is 'Tên đầu nối';
comment on column dau_noi_dien.loai_dau_noi is 'Loại đầu nối';
comment on column dau_noi_dien.tiet_dien_danh_dinh_mm2 is 'Tiết diện danh định (mm²)';
comment on column dau_noi_dien.dong_dien_cho_phep_a is 'Dòng điện cho phép (A)';
comment on column dau_noi_dien.kieu_kep is 'Kiểu kẹp';
comment on column dau_noi_dien.vi_tri_du_kien is 'Vị trí dự kiến';
comment on column dau_noi_dien.luu_y is 'Lưu ý';
comment on column dau_noi_dien.tinh_trang_kiem_chung is 'Tình trạng kiểm chứng';
alter table dau_noi_dien enable row level security;
create policy "doc_playbook" on dau_noi_dien for select to authenticated using (true);

create table thiet_bi_dong_cat (
  id bigint generated always as identity primary key,
  ma_cau_hinh_mo_ta text not null,
  loai_thiet_bi text not null,
  dong_dinh_muc_a int not null,
  so_cuc int not null,
  dien_ap_dinh_muc_v text not null,
  dong_ro_dinh_muc_ma text not null,
  kha_nang_cat_ngan_mach_ka text not null,
  dieu_kien_chon text not null,
  tinh_trang_kiem_chung trang_thai_kiem_chung not null,
  constraint thiet_bi_dong_cat_ma_cau_hinh_mo_ta_key unique (ma_cau_hinh_mo_ta)
);
comment on table thiet_bi_dong_cat is 'Sheet “Thiết bị đóng cắt”: Thiết bị bảo vệ và cách ly';
comment on column thiet_bi_dong_cat.ma_cau_hinh_mo_ta is 'Mã cấu hình mô tả';
comment on column thiet_bi_dong_cat.loai_thiet_bi is 'Loại thiết bị';
comment on column thiet_bi_dong_cat.dong_dinh_muc_a is 'Dòng định mức (A)';
comment on column thiet_bi_dong_cat.so_cuc is 'Số cực';
comment on column thiet_bi_dong_cat.dien_ap_dinh_muc_v is 'Điện áp định mức (V)';
comment on column thiet_bi_dong_cat.dong_ro_dinh_muc_ma is 'Dòng rò định mức (mA)';
comment on column thiet_bi_dong_cat.kha_nang_cat_ngan_mach_ka is 'Khả năng cắt ngắn mạch (kA)';
comment on column thiet_bi_dong_cat.dieu_kien_chon is 'Điều kiện chọn';
comment on column thiet_bi_dong_cat.tinh_trang_kiem_chung is 'Tình trạng kiểm chứng';
alter table thiet_bi_dong_cat enable row level security;
create policy "doc_playbook" on thiet_bi_dong_cat for select to authenticated using (true);

create table lop_phu_tai (
  ma_lop_phu_tai text primary key,
  ten_lop_phu_tai text not null,
  nhom_phu_tai text not null,
  cong_suat_min_w int,
  cong_suat_max_w int,
  co_so_xac_dinh_chinh text not null,
  he_so_nhu_cau numeric,
  don_vi_khoi_luong text not null,
  ghi_chu_ky_thuat text not null,
  nguon_quy_tac text not null,
  tinh_trang_kiem_chung trang_thai_kiem_chung not null,
  constraint lop_phu_tai_ten_lop_phu_tai_key unique (ten_lop_phu_tai),
  constraint lop_phu_tai_chk1 check (cong_suat_min_w <= cong_suat_max_w),
  constraint lop_phu_tai_chk2 check (he_so_nhu_cau > 0 and he_so_nhu_cau <= 1)
);
comment on table lop_phu_tai is 'Sheet “Lớp phụ tải”: Phân loại tải và công suất gợi ý';
comment on column lop_phu_tai.ma_lop_phu_tai is 'Mã lớp phụ tải';
comment on column lop_phu_tai.ten_lop_phu_tai is 'Tên lớp phụ tải';
comment on column lop_phu_tai.nhom_phu_tai is 'Nhóm phụ tải';
comment on column lop_phu_tai.cong_suat_min_w is 'Công suất tham khảo nhỏ nhất (W)';
comment on column lop_phu_tai.cong_suat_max_w is 'Công suất tham khảo lớn nhất (W)';
comment on column lop_phu_tai.co_so_xac_dinh_chinh is 'Cơ sở xác định chính';
comment on column lop_phu_tai.he_so_nhu_cau is 'Hệ số nhu cầu';
comment on column lop_phu_tai.don_vi_khoi_luong is 'Đơn vị khối lượng';
comment on column lop_phu_tai.ghi_chu_ky_thuat is 'Ghi chú kỹ thuật';
comment on column lop_phu_tai.nguon_quy_tac is 'Nguồn quy tắc';
comment on column lop_phu_tai.tinh_trang_kiem_chung is 'Tình trạng kiểm chứng';
alter table lop_phu_tai enable row level security;
create policy "doc_playbook" on lop_phu_tai for select to authenticated using (true);

create table lop_dau_cuoi (
  ma_lop_dau_cuoi text primary key,
  ten_lop_dau_cuoi text not null,
  lop_phu_tai_ma text references lop_phu_tai(ma_lop_phu_tai) not null,
  kieu_cap_dien text not null,
  he_so_cong_suat_tam numeric,
  nhom_mach text not null,
  kieu_diem_ket_noi text not null,
  khong_gian_ap_dung text not null,
  gioi_han_sut_ap_pct numeric,
  ghi_chu text not null,
  tinh_trang_kiem_chung trang_thai_kiem_chung not null,
  constraint lop_dau_cuoi_ten_lop_dau_cuoi_key unique (ten_lop_dau_cuoi),
  constraint lop_dau_cuoi_chk1 check (he_so_cong_suat_tam > 0 and he_so_cong_suat_tam <= 1),
  constraint lop_dau_cuoi_chk2 check (gioi_han_sut_ap_pct > 0 and gioi_han_sut_ap_pct <= 100)
);
create index lop_dau_cuoi_lop_phu_tai_ma_idx on lop_dau_cuoi (lop_phu_tai_ma);
comment on table lop_dau_cuoi is 'Sheet “Lớp đầu cuối”: Định nghĩa điểm điện';
comment on column lop_dau_cuoi.ma_lop_dau_cuoi is 'Mã lớp đầu cuối';
comment on column lop_dau_cuoi.ten_lop_dau_cuoi is 'Tên lớp đầu cuối';
comment on column lop_dau_cuoi.lop_phu_tai_ma is 'Lớp phụ tải liên kết';
comment on column lop_dau_cuoi.kieu_cap_dien is 'Kiểu cấp điện';
comment on column lop_dau_cuoi.he_so_cong_suat_tam is 'Hệ số công suất dùng tạm';
comment on column lop_dau_cuoi.nhom_mach is 'Nhóm mạch';
comment on column lop_dau_cuoi.kieu_diem_ket_noi is 'Kiểu điểm kết nối';
comment on column lop_dau_cuoi.khong_gian_ap_dung is 'Không gian áp dụng';
comment on column lop_dau_cuoi.gioi_han_sut_ap_pct is 'Giới hạn sụt áp thiết kế (%)';
comment on column lop_dau_cuoi.ghi_chu is 'Ghi chú';
comment on column lop_dau_cuoi.tinh_trang_kiem_chung is 'Tình trạng kiểm chứng';
alter table lop_dau_cuoi enable row level security;
create policy "doc_playbook" on lop_dau_cuoi for select to authenticated using (true);

create table quy_tac_doi_tuong (
  ma_quy_tac text primary key,
  ten_quy_tac text not null,
  truong_ifc_doi_chieu text not null,
  tu_khoa_nhan_dien text[] not null,
  phuong_thuc_khop text not null,
  lop_dau_cuoi_ma text references lop_dau_cuoi(ma_lop_dau_cuoi) not null,
  khong_gian_thuong_gap text not null,
  dieu_kien_bo_sung text not null,
  xu_ly_canh_bao text not null,
  tinh_trang_kiem_chung trang_thai_kiem_chung not null,
  constraint quy_tac_doi_tuong_ten_quy_tac_key unique (ten_quy_tac)
);
create index quy_tac_doi_tuong_lop_dau_cuoi_ma_idx on quy_tac_doi_tuong (lop_dau_cuoi_ma);
create index quy_tac_doi_tuong_tu_khoa_nhan_dien_gin on quy_tac_doi_tuong using gin (tu_khoa_nhan_dien);
comment on table quy_tac_doi_tuong is 'Sheet “Quy tắc đối tượng”: Ánh xạ tên đối tượng IFC';
comment on column quy_tac_doi_tuong.ma_quy_tac is 'Mã quy tắc';
comment on column quy_tac_doi_tuong.ten_quy_tac is 'Tên quy tắc';
comment on column quy_tac_doi_tuong.truong_ifc_doi_chieu is 'Trường IFC cần đối chiếu';
comment on column quy_tac_doi_tuong.tu_khoa_nhan_dien is 'Từ khóa nhận diện';
comment on column quy_tac_doi_tuong.phuong_thuc_khop is 'Phương thức khớp';
comment on column quy_tac_doi_tuong.lop_dau_cuoi_ma is 'Lớp đầu cuối gán';
comment on column quy_tac_doi_tuong.khong_gian_thuong_gap is 'Không gian thường gặp';
comment on column quy_tac_doi_tuong.dieu_kien_bo_sung is 'Điều kiện bổ sung';
comment on column quy_tac_doi_tuong.xu_ly_canh_bao is 'Xử lý hoặc cảnh báo';
comment on column quy_tac_doi_tuong.tinh_trang_kiem_chung is 'Tình trạng kiểm chứng';
alter table quy_tac_doi_tuong enable row level security;
create policy "doc_playbook" on quy_tac_doi_tuong for select to authenticated using (true);

create table danh_muc_day_cap (
  ma_cau_hinh text primary key,
  vat_lieu_ruot_dan text not null,
  tiet_dien_danh_dinh_mm2 numeric not null,
  loai_cach_dien text not null,
  cau_tao text not null,
  dien_ap_danh_dinh_cap_v text not null,
  phuong_phap_lap_dat text not null,
  dong_mang_tai_sau_hieu_chinh_a numeric,
  tinh_trang_tinh_toan text not null,
  luu_y_lua_chon text not null,
  tinh_trang_kiem_chung trang_thai_kiem_chung not null
);
comment on table danh_muc_day_cap is 'Sheet “Danh mục dây cáp”: Catalog cấu tạo và tiết diện';
comment on column danh_muc_day_cap.ma_cau_hinh is 'Mã cấu hình';
comment on column danh_muc_day_cap.vat_lieu_ruot_dan is 'Vật liệu ruột dẫn';
comment on column danh_muc_day_cap.tiet_dien_danh_dinh_mm2 is 'Tiết diện danh định (mm²)';
comment on column danh_muc_day_cap.loai_cach_dien is 'Loại cách điện';
comment on column danh_muc_day_cap.cau_tao is 'Cấu tạo';
comment on column danh_muc_day_cap.dien_ap_danh_dinh_cap_v is 'Điện áp danh định của cáp (V)';
comment on column danh_muc_day_cap.phuong_phap_lap_dat is 'Phương pháp lắp đặt';
comment on column danh_muc_day_cap.dong_mang_tai_sau_hieu_chinh_a is 'Dòng mang tải sau hiệu chỉnh (A)';
comment on column danh_muc_day_cap.tinh_trang_tinh_toan is 'Tình trạng tính toán';
comment on column danh_muc_day_cap.luu_y_lua_chon is 'Lưu ý lựa chọn';
comment on column danh_muc_day_cap.tinh_trang_kiem_chung is 'Tình trạng kiểm chứng';
alter table danh_muc_day_cap enable row level security;
create policy "doc_playbook" on danh_muc_day_cap for select to authenticated using (true);

create table cau_hinh_di_day (
  id bigint generated always as identity primary key,
  ten_duong_day text not null,
  vai_tro_mach text not null,
  kieu_nguon text not null,
  thanh_phan_day text not null,
  kieu_bo_tri_day text not null,
  phuong_phap_chon_tiet_dien text not null,
  tiet_dien_de_xuat_mm2 numeric,
  bao_ve_yeu_cau text not null,
  ghi_chu text not null,
  tinh_trang_kiem_chung trang_thai_kiem_chung not null,
  constraint cau_hinh_di_day_ten_duong_day_key unique (ten_duong_day)
);
comment on table cau_hinh_di_day is 'Sheet “Cấu hình đi dây”: Mục đích và phương thức đi dây';
comment on column cau_hinh_di_day.ten_duong_day is 'Tên đường dây';
comment on column cau_hinh_di_day.vai_tro_mach is 'Vai trò mạch';
comment on column cau_hinh_di_day.kieu_nguon is 'Kiểu nguồn';
comment on column cau_hinh_di_day.thanh_phan_day is 'Thành phần dây';
comment on column cau_hinh_di_day.kieu_bo_tri_day is 'Kiểu bố trí dây';
comment on column cau_hinh_di_day.phuong_phap_chon_tiet_dien is 'Phương pháp chọn tiết diện';
comment on column cau_hinh_di_day.tiet_dien_de_xuat_mm2 is 'Tiết diện đề xuất (mm²)';
comment on column cau_hinh_di_day.bao_ve_yeu_cau is 'Bảo vệ yêu cầu';
comment on column cau_hinh_di_day.ghi_chu is 'Ghi chú';
comment on column cau_hinh_di_day.tinh_trang_kiem_chung is 'Tình trạng kiểm chứng';
alter table cau_hinh_di_day enable row level security;
create policy "doc_playbook" on cau_hinh_di_day for select to authenticated using (true);

create table kiem_tra_day_cap (
  ma_kiem_tra text primary key,
  ten_buoc_kiem_tra text not null,
  du_lieu_dau_vao text not null,
  cach_xu_ly text not null,
  dieu_kien_chap_nhan text not null,
  nguon_lien_quan text not null,
  trang_thai_phe_duyet text not null
);
comment on table kiem_tra_day_cap is 'Sheet “Kiểm tra dây cáp”: Chuỗi kiểm tra lựa chọn cáp';
comment on column kiem_tra_day_cap.ma_kiem_tra is 'Mã kiểm tra';
comment on column kiem_tra_day_cap.ten_buoc_kiem_tra is 'Tên bước kiểm tra';
comment on column kiem_tra_day_cap.du_lieu_dau_vao is 'Dữ liệu đầu vào';
comment on column kiem_tra_day_cap.cach_xu_ly is 'Cách xử lý';
comment on column kiem_tra_day_cap.dieu_kien_chap_nhan is 'Điều kiện chấp nhận';
comment on column kiem_tra_day_cap.nguon_lien_quan is 'Nguồn liên quan';
comment on column kiem_tra_day_cap.trang_thai_phe_duyet is 'Trạng thái phê duyệt';
alter table kiem_tra_day_cap enable row level security;
create policy "doc_playbook" on kiem_tra_day_cap for select to authenticated using (true);

create table quy_tac_phong (
  id bigint generated always as identity primary key,
  loai_khong_gian text not null,
  thiet_bi_thuong_xet text not null,
  nguyen_tac_bo_tri text not null,
  cao_do_lap_dat text not null,
  kiem_tra_bo_sung text not null,
  gioi_han_canh_bao text not null,
  loai_du_lieu text not null,
  constraint quy_tac_phong_loai_khong_gian_key unique (loai_khong_gian)
);
comment on table quy_tac_phong is 'Sheet “Quy tắc phòng”: Quy tắc gợi ý theo phòng';
comment on column quy_tac_phong.loai_khong_gian is 'Loại không gian';
comment on column quy_tac_phong.thiet_bi_thuong_xet is 'Thiết bị thường xét';
comment on column quy_tac_phong.nguyen_tac_bo_tri is 'Nguyên tắc bố trí';
comment on column quy_tac_phong.cao_do_lap_dat is 'Cao độ lắp đặt';
comment on column quy_tac_phong.kiem_tra_bo_sung is 'Kiểm tra bổ sung';
comment on column quy_tac_phong.gioi_han_canh_bao is 'Giới hạn / cảnh báo';
comment on column quy_tac_phong.loai_du_lieu is 'Loại dữ liệu';
alter table quy_tac_phong enable row level security;
create policy "doc_playbook" on quy_tac_phong for select to authenticated using (true);

create table quy_tac_thiet_ke (
  ma_quy_tac text primary key,
  ten_quy_tac text not null,
  phan_loai text not null,
  dau_vao_can_thiet text not null,
  tieu_chi_xu_ly text not null,
  nguon_tham_khao text not null,
  trang_thai_xac_nhan text not null
);
comment on table quy_tac_thiet_ke is 'Sheet “Quy tắc thiết kế”: Các phép kiểm tra cho AI Agent';
comment on column quy_tac_thiet_ke.ma_quy_tac is 'Mã quy tắc';
comment on column quy_tac_thiet_ke.ten_quy_tac is 'Tên quy tắc';
comment on column quy_tac_thiet_ke.phan_loai is 'Phân loại';
comment on column quy_tac_thiet_ke.dau_vao_can_thiet is 'Đầu vào cần thiết';
comment on column quy_tac_thiet_ke.tieu_chi_xu_ly is 'Tiêu chí xử lý';
comment on column quy_tac_thiet_ke.nguon_tham_khao is 'Nguồn tham khảo';
comment on column quy_tac_thiet_ke.trang_thai_xac_nhan is 'Trạng thái xác nhận';
alter table quy_tac_thiet_ke enable row level security;
create policy "doc_playbook" on quy_tac_thiet_ke for select to authenticated using (true);

create table nguon_tieu_chuan (
  so_hieu_van_ban text primary key,
  pham_vi_lien_quan text not null,
  loai_tai_lieu text not null,
  duong_dan_tra_cuu text not null,
  luu_y_ap_dung text not null,
  trang_thai_ra_soat text not null
);
comment on table nguon_tieu_chuan is 'Sheet “Nguồn tiêu chuẩn”: Các đường dẫn nguồn tham khảo';
comment on column nguon_tieu_chuan.so_hieu_van_ban is 'Số hiệu văn bản';
comment on column nguon_tieu_chuan.pham_vi_lien_quan is 'Phạm vi liên quan';
comment on column nguon_tieu_chuan.loai_tai_lieu is 'Loại tài liệu';
comment on column nguon_tieu_chuan.duong_dan_tra_cuu is 'Đường dẫn tra cứu';
comment on column nguon_tieu_chuan.luu_y_ap_dung is 'Lưu ý áp dụng';
comment on column nguon_tieu_chuan.trang_thai_ra_soat is 'Trạng thái rà soát';
alter table nguon_tieu_chuan enable row level security;
create policy "doc_playbook" on nguon_tieu_chuan for select to authenticated using (true);

create table tu_dien_du_lieu (
  ten_bang text primary key,
  so_ban_ghi int not null,
  y_nghia text not null,
  nguon_du_lieu text not null,
  cap_do_su_dung text not null
);
comment on table tu_dien_du_lieu is 'Sheet “Từ điển dữ liệu”: Mô tả các bảng trong Playbook';
comment on column tu_dien_du_lieu.ten_bang is 'Tên bảng';
comment on column tu_dien_du_lieu.so_ban_ghi is 'Số bản ghi';
comment on column tu_dien_du_lieu.y_nghia is 'Ý nghĩa';
comment on column tu_dien_du_lieu.nguon_du_lieu is 'Nguồn dữ liệu';
comment on column tu_dien_du_lieu.cap_do_su_dung is 'Cấp độ sử dụng';
alter table tu_dien_du_lieu enable row level security;
create policy "doc_playbook" on tu_dien_du_lieu for select to authenticated using (true);
