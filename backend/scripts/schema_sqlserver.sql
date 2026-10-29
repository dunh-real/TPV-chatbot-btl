-- Schema CSDL nghiệp vụ TPV cho SQL Server 2016+.
-- KHÔNG sửa tay: sinh từ app/db/models.py bằng
--     uv run python scripts/gen_schema.py -o scripts/schema_sqlserver.sql
--
-- Bảng tài nguyên được tách làm hai (don_vi / trang_bi) thay vì gộp một bảng:
-- gộp lại thì quân số bị lặp theo từng dòng trang bị, sửa sót một dòng là báo
-- cáo ra số sai.
--
-- Cột truong_du_lieu của template_bao_cao là JSON lưu dạng NVARCHAR(MAX)
-- (SQL Server không có kiểu JSON riêng).


CREATE TABLE don_vi (
	ma_don_vi NVARCHAR(50) NOT NULL, 
	ten_don_vi NVARCHAR(255) NOT NULL, 
	quan_so INTEGER NOT NULL, 
	quan_so_kiem_ke DATE NULL, 
	PRIMARY KEY (ma_don_vi)
);

CREATE TABLE phong_ban (
	ma_phong_ban NVARCHAR(50) NOT NULL, 
	ten_phong_ban NVARCHAR(255) NOT NULL, 
	email NVARCHAR(255) NOT NULL, 
	mo_ta NVARCHAR(max) NOT NULL, 
	PRIMARY KEY (ma_phong_ban)
);

CREATE TABLE template_bao_cao (
	ma_template NVARCHAR(50) NOT NULL, 
	ten_bao_cao NVARCHAR(255) NOT NULL, 
	loai_bao_cao NVARCHAR(100) NOT NULL, 
	mo_ta NVARCHAR(max) NOT NULL, 
	file_path NVARCHAR(1000) NOT NULL, 
	truong_du_lieu NVARCHAR(max) NOT NULL, 
	PRIMARY KEY (ma_template)
);

CREATE TABLE van_ban (
	ma_van_ban NVARCHAR(100) NOT NULL, 
	ten_van_ban NVARCHAR(500) NOT NULL, 
	loai_van_ban NVARCHAR(100) NOT NULL, 
	noi_gui NVARCHAR(255) NOT NULL, 
	noi_nhan NVARCHAR(500) NOT NULL, 
	mo_ta NVARCHAR(max) NOT NULL, 
	ngay_van_ban DATE NULL, 
	file_path NVARCHAR(1000) NOT NULL, 
	dang_file NVARCHAR(20) NOT NULL, 
	doc_id NVARCHAR(64) NULL, 
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
	PRIMARY KEY (ma_van_ban)
);

CREATE INDEX ix_van_ban_doc_id ON van_ban (doc_id);

CREATE TABLE kiem_ke (
	id INTEGER NOT NULL IDENTITY, 
	ma_don_vi NVARCHAR(50) NOT NULL, 
	ky NVARCHAR(7) NOT NULL, 
	ngay_kiem_ke DATE NULL, 
	quan_so INTEGER NOT NULL, 
	co_mat INTEGER NOT NULL, 
	vang INTEGER NOT NULL, 
	di_hoc INTEGER NOT NULL, 
	nghi_phep INTEGER NOT NULL, 
	ghi_chu NVARCHAR(max) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_kiem_ke_don_vi_ky UNIQUE (ma_don_vi, ky), 
	FOREIGN KEY(ma_don_vi) REFERENCES don_vi (ma_don_vi)
);

CREATE INDEX ix_kiem_ke_ky ON kiem_ke (ky);

CREATE INDEX ix_kiem_ke_ma_don_vi ON kiem_ke (ma_don_vi);

CREATE TABLE trang_bi (
	id INTEGER NOT NULL IDENTITY, 
	ma_don_vi NVARCHAR(50) NOT NULL, 
	ten_trang_bi NVARCHAR(255) NOT NULL, 
	so_luong INTEGER NOT NULL, 
	tinh_trang NVARCHAR(100) NOT NULL, 
	bao_duong_cuoi DATE NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(ma_don_vi) REFERENCES don_vi (ma_don_vi)
);

CREATE INDEX ix_trang_bi_ma_don_vi ON trang_bi (ma_don_vi);

CREATE TABLE kiem_ke_trang_bi (
	id INTEGER NOT NULL IDENTITY, 
	kiem_ke_id INTEGER NOT NULL, 
	ten_trang_bi NVARCHAR(255) NOT NULL, 
	so_luong INTEGER NOT NULL, 
	tinh_trang NVARCHAR(100) NOT NULL, 
	bao_duong_cuoi DATE NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(kiem_ke_id) REFERENCES kiem_ke (id)
);

CREATE INDEX ix_kiem_ke_trang_bi_kiem_ke_id ON kiem_ke_trang_bi (kiem_ke_id);
