"""Bộ thử áp lực cho bước Qwen đặt nhãn/vai trò cột (nhan_cot.gan_nhan_cot) trên dữ liệu ERP thật, chỉ đọc.

Mỗi ca là một bảng kết quả với alias theo đủ kiểu Text-to-SQL hay sinh (không dấu viết liền, tiếng Anh,
camelCase, viết tắt, vô nghĩa) và các loại cột khó (tỷ lệ, trung bình, cộng dồn, số tồn theo kỳ/nhóm, năm
dạng số, tháng 1-9, mã số nguyên, bảng chỉ tiêu khác loại). Mỗi cột có đáp án:
    vai trò (nhom|so|ky), cộng được (True/False, chỉ cột số), từ khoá nhãn (nhãn phải chứa ít nhất một).
Chạy N lần mỗi ca để đo độ ổn định. Kết quả: <out>/stress.json + bảng in ra.

    CHECK=stress ./run_eval.sh <out_dir> <so_lan>
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ALIVE = "ep.IsDeleted = 0 AND ep.ResignationDate IS NULL"
QTY = "COALESCE(a.Quantity, 1)"

# (id, nội dung bảng, SQL, {alias: (vai_tro, cong_duoc|None, [từ khoá nhãn])})
CASES = [
    ("S01", "Số nhân sự theo phòng ban",
     f"SELECT d.DisplayName AS tenphongban, COUNT(*) AS soluongnhansu FROM dbo.Hrm_EmployeeProfile ep JOIN dbo.Dms_WorkDepartment d "
     f"ON d.Id = ep.WorkDepartmentId WHERE ep.TenantId = 94 AND {ALIVE} GROUP BY d.DisplayName",
     {"tenphongban": ("nhom", None, ["phòng ban"]), "soluongnhansu": ("so", True, ["nhân sự", "số lượng"])}),
    ("S02", "Headcount by department",
     f"SELECT d.DisplayName AS department_name, COUNT(*) AS employee_count FROM dbo.Hrm_EmployeeProfile ep JOIN dbo.Dms_WorkDepartment d "
     f"ON d.Id = ep.WorkDepartmentId WHERE ep.TenantId = 63 AND {ALIVE} GROUP BY d.DisplayName",
     {"department_name": ("nhom", None, ["phòng ban", "bộ phận"]), "employee_count": ("so", True, ["nhân", "số lượng"])}),
    ("S03", "Nhân sự theo chức vụ",
     f"SELECT p.Name AS positionName, COUNT(*) AS headCount FROM dbo.Hrm_EmployeeProfile ep JOIN dbo.Dms_WorkPosition p "
     f"ON p.Id = ep.WorkPositionId WHERE ep.TenantId = 94 AND {ALIVE} GROUP BY p.Name",
     {"positionName": ("nhom", None, ["chức vụ", "vị trí"]), "headCount": ("so", True, ["nhân", "số lượng", "người"])}),
    ("S04", "Nhân sự theo phòng ban (viết tắt)",
     f"SELECT d.DisplayName AS pb, COUNT(*) AS sl_ns FROM dbo.Hrm_EmployeeProfile ep JOIN dbo.Dms_WorkDepartment d "
     f"ON d.Id = ep.WorkDepartmentId WHERE ep.TenantId = 63 AND {ALIVE} GROUP BY d.DisplayName",
     {"pb": ("nhom", None, ["phòng ban"]), "sl_ns": ("so", True, ["nhân sự", "số lượng"])}),
    ("S05", "Tỷ trọng nhân sự theo phòng ban",
     f"SELECT d.DisplayName AS phong_ban, COUNT(*) AS so_nhan_su, CAST(100.0 * COUNT(*) / SUM(COUNT(*)) OVER () AS decimal(5,2)) "
     f"AS ty_le_phan_tram FROM dbo.Hrm_EmployeeProfile ep JOIN dbo.Dms_WorkDepartment d ON d.Id = ep.WorkDepartmentId "
     f"WHERE ep.TenantId = 63 AND {ALIVE} GROUP BY d.DisplayName",
     {"phong_ban": ("nhom", None, ["phòng ban"]), "so_nhan_su": ("so", True, ["nhân sự"]),
      "ty_le_phan_tram": ("so", False, ["tỷ lệ", "tỷ trọng", "%"])}),
    ("S06", "Thiết bị bình quân mỗi nhân sự theo đơn vị",
     f"WITH ns AS (SELECT ep.TenantId, COUNT(*) AS n FROM dbo.Hrm_EmployeeProfile ep WHERE {ALIVE} GROUP BY ep.TenantId), "
     f"tb AS (SELECT a.TenantId, SUM({QTY}) AS q FROM dbo.Asm_Assets a WHERE a.IsDeleted = 0 GROUP BY a.TenantId) "
     "SELECT t.TenancyName AS don_vi, ns.n AS so_nhan_su, tb.q AS so_thiet_bi, CAST(1.0 * tb.q / ns.n AS decimal(6,2)) AS tb_thiet_bi_moi_nguoi "
     "FROM dbo.AbpTenants t JOIN ns ON ns.TenantId = t.Id JOIN tb ON tb.TenantId = t.Id WHERE t.IsDeleted = 0",
     {"don_vi": ("nhom", None, ["đơn vị"]), "so_nhan_su": ("so", True, ["nhân sự"]), "so_thiet_bi": ("so", True, ["thiết bị"]),
      "tb_thiet_bi_moi_nguoi": ("so", False, ["bình quân", "trung bình", "mỗi"])}),
    ("S07", "Lũy kế thiết bị theo phòng ban",
     f"SELECT d.DisplayName AS phong_ban, SUM({QTY}) AS so_luong, SUM(SUM({QTY})) OVER (ORDER BY SUM({QTY}) DESC, d.DisplayName "
     "ROWS UNBOUNDED PRECEDING) AS luy_ke FROM dbo.Asm_Assets a JOIN dbo.Dms_WorkDepartment d ON d.Id = a.WorkDepartmentId "
     "WHERE a.TenantId = 94 AND a.IsDeleted = 0 GROUP BY d.DisplayName",
     {"phong_ban": ("nhom", None, ["phòng ban"]), "so_luong": ("so", True, ["số lượng", "thiết bị"]),
      "luy_ke": ("so", False, ["lũy kế", "cộng dồn", "tích lũy"])}),
    ("S08", "Tuyển mới theo năm",
     "SELECT YEAR(ep.HireDate) AS nam, COUNT(*) AS so_tuyen_moi FROM dbo.Hrm_EmployeeProfile ep WHERE ep.TenantId = 63 "
     "AND ep.IsDeleted = 0 AND ep.HireDate >= '2020-01-01' GROUP BY YEAR(ep.HireDate)",
     {"nam": ("ky", None, ["năm"]), "so_tuyen_moi": ("so", True, ["tuyển"])}),
    ("S09", "Tuyển mới theo tháng năm 2026",
     "SELECT FORMAT(ep.HireDate, 'yyyy-MM') AS thang_nam, COUNT(*) AS tuyen_moi FROM dbo.Hrm_EmployeeProfile ep WHERE ep.TenantId = 63 "
     "AND ep.IsDeleted = 0 AND ep.HireDate >= '2026-01-01' AND ep.HireDate < '2026-10-01' GROUP BY FORMAT(ep.HireDate, 'yyyy-MM')",
     {"thang_nam": ("ky", None, ["tháng"]), "tuyen_moi": ("so", True, ["tuyển"])}),
    ("S10", "Nhân sự cuối mỗi năm",
     "WITH y AS (SELECT 2023 AS nam UNION ALL SELECT 2024 UNION ALL SELECT 2025) SELECT y.nam, (SELECT COUNT(*) FROM "
     "dbo.Hrm_EmployeeProfile ep WHERE ep.TenantId = 63 AND ep.IsDeleted = 0 AND COALESCE(ep.HireDate, ep.CreationTime) < "
     "DATEFROMPARTS(y.nam + 1, 1, 1) AND (ep.ResignationDate IS NULL OR ep.ResignationDate >= DATEFROMPARTS(y.nam + 1, 1, 1))) "
     "AS nhan_su_cuoi_nam FROM y",
     {"nam": ("ky", None, ["năm"]), "nhan_su_cuoi_nam": ("so", False, ["nhân sự"])}),
    ("S11", "Nhân sự hiện có theo phòng ban",
     f"SELECT d.DisplayName AS phong_ban, COUNT(*) AS nhan_su_hien_co FROM dbo.Hrm_EmployeeProfile ep JOIN dbo.Dms_WorkDepartment d "
     f"ON d.Id = ep.WorkDepartmentId WHERE ep.TenantId = 94 AND {ALIVE} GROUP BY d.DisplayName",
     {"phong_ban": ("nhom", None, ["phòng ban"]), "nhan_su_hien_co": ("so", True, ["nhân sự"])}),
    ("S12", "Chỉ tiêu tổng hợp nhân sự TPV",
     f"SELECT N'Nhân sự đang làm việc' AS chi_tieu, COUNT(*) AS gia_tri FROM dbo.Hrm_EmployeeProfile ep WHERE ep.TenantId = 63 AND {ALIVE} "
     f"UNION ALL SELECT N'Số phòng ban', COUNT(*) FROM dbo.Dms_WorkDepartment d WHERE d.TenantId = 63 AND d.IsDeleted = 0 "
     f"UNION ALL SELECT N'Số chức vụ', COUNT(DISTINCT ep.WorkPositionId) FROM dbo.Hrm_EmployeeProfile ep WHERE ep.TenantId = 63 AND {ALIVE}",
     {"chi_tieu": ("nhom", None, ["chỉ tiêu"]), "gia_tri": ("so", False, ["giá trị"])}),
    ("S13", "Tồn kho thiết bị sau cấp phát theo chủng loại",
     f"WITH tb AS (SELECT a.AssetCategoryId, SUM({QTY}) AS tong FROM dbo.Asm_Assets a WHERE a.TenantId = 94 AND a.IsDeleted = 0 "
     "GROUP BY a.AssetCategoryId), cp AS (SELECT a.AssetCategoryId, SUM(ad.Quantity) AS dc FROM dbo.Asm_AssetAssignmentDetail ad "
     "JOIN dbo.Asm_Assets a ON a.Id = ad.AssetId WHERE a.TenantId = 94 AND ad.IsDeleted = 0 AND ad.ReturnDate IS NULL "
     "GROUP BY a.AssetCategoryId) SELECT c.Name AS chungloai, tb.tong - ISNULL(cp.dc, 0) AS conlai FROM tb JOIN dbo.Asm_AssetCategories c "
     "ON c.Id = tb.AssetCategoryId LEFT JOIN cp ON cp.AssetCategoryId = tb.AssetCategoryId",
     {"chungloai": ("nhom", None, ["chủng loại", "loại"]), "conlai": ("so", True, ["còn lại", "tồn"])}),
    ("S14", "Nhân sự theo phòng ban kèm mã",
     f"SELECT d.Id AS ma_phong_ban, d.DisplayName AS ten_phong_ban, COUNT(*) AS so_nhan_su FROM dbo.Hrm_EmployeeProfile ep "
     f"JOIN dbo.Dms_WorkDepartment d ON d.Id = ep.WorkDepartmentId WHERE ep.TenantId = 94 AND {ALIVE} GROUP BY d.Id, d.DisplayName",
     {"ma_phong_ban": ("nhom", None, ["mã"]), "ten_phong_ban": ("nhom", None, ["phòng ban"]), "so_nhan_su": ("so", True, ["nhân sự"])}),
    ("S15", "Nhân sự theo giới tính (mã số)",
     f"SELECT ep.Gender AS gioi_tinh, COUNT(*) AS so_nhan_su FROM dbo.Hrm_EmployeeProfile ep WHERE ep.TenantId = 63 AND {ALIVE} "
     "GROUP BY ep.Gender",
     {"gioi_tinh": ("nhom", None, ["giới tính"]), "so_nhan_su": ("so", True, ["nhân sự"])}),
    ("S16", "Nhân sự theo tình trạng làm việc",
     f"SELECT COALESCE(s.Name, N'(chưa có)') AS [Tình trạng], COUNT(*) AS [Số nhân sự] FROM dbo.Hrm_EmployeeProfile ep "
     f"LEFT JOIN dbo.Hrm_EmployeeStatus s ON s.Id = ep.EmploymentStatusId WHERE ep.TenantId = 94 AND {ALIVE} GROUP BY s.Name",
     {"Tình trạng": ("nhom", None, ["tình trạng"]), "Số nhân sự": ("so", True, ["nhân sự"])}),
    ("S17", "Số nhân sự theo phòng ban (cột đặt tên chung chung)",
     f"SELECT d.DisplayName AS c1, COUNT(*) AS c2 FROM dbo.Hrm_EmployeeProfile ep JOIN dbo.Dms_WorkDepartment d "
     f"ON d.Id = ep.WorkDepartmentId WHERE ep.TenantId = 63 AND {ALIVE} GROUP BY d.DisplayName",
     {"c1": ("nhom", None, ["phòng ban"]), "c2": ("so", True, ["nhân sự"])}),
    ("S18", "Tỷ lệ nghỉ việc theo phòng ban (%)",
     "SELECT d.DisplayName AS phong_ban, CAST(ROUND(100.0 * SUM(CASE WHEN ep.ResignationDate IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 0) "
     "AS int) AS pct_nghi_viec FROM dbo.Hrm_EmployeeProfile ep JOIN dbo.Dms_WorkDepartment d ON d.Id = ep.WorkDepartmentId "
     "WHERE ep.TenantId = 63 AND ep.IsDeleted = 0 GROUP BY d.DisplayName",
     {"phong_ban": ("nhom", None, ["phòng ban"]), "pct_nghi_viec": ("so", False, ["tỷ lệ", "%"])}),
    ("S19", "Nhân sự và thiết bị theo đơn vị thuê bao",
     f"WITH ns AS (SELECT ep.TenantId, COUNT(*) AS n FROM dbo.Hrm_EmployeeProfile ep WHERE {ALIVE} GROUP BY ep.TenantId), "
     f"tb AS (SELECT a.TenantId, SUM({QTY}) AS q FROM dbo.Asm_Assets a WHERE a.IsDeleted = 0 GROUP BY a.TenantId) "
     "SELECT t.TenancyName AS tenant, ns.n AS sl1, tb.q AS sl2 FROM dbo.AbpTenants t JOIN ns ON ns.TenantId = t.Id "
     "JOIN tb ON tb.TenantId = t.Id WHERE t.IsDeleted = 0",
     {"tenant": ("nhom", None, ["đơn vị"]), "sl1": ("so", True, ["nhân sự"]), "sl2": ("so", True, ["thiết bị"])}),
    ("S20", "Thiết bị theo chủng loại",
     f"SELECT COALESCE(c.Name, N'(chưa phân loại)') AS category, SUM({QTY}) AS qty FROM dbo.Asm_Assets a LEFT JOIN "
     "dbo.Asm_AssetCategories c ON c.Id = a.AssetCategoryId WHERE a.TenantId = 63 AND a.IsDeleted = 0 GROUP BY c.Name",
     {"category": ("nhom", None, ["chủng loại", "loại", "danh mục"]), "qty": ("so", True, ["số lượng", "thiết bị"])}),
    ("S21", "Tuyển mới lũy kế theo tháng 2026",
     "WITH m AS (SELECT MONTH(ep.HireDate) AS m, COUNT(*) AS n FROM dbo.Hrm_EmployeeProfile ep WHERE ep.TenantId = 63 AND ep.IsDeleted = 0 "
     "AND ep.HireDate >= '2026-01-01' AND ep.HireDate < '2027-01-01' GROUP BY MONTH(ep.HireDate)) "
     "SELECT N'Tháng ' + CAST(m AS nvarchar(2)) AS month_label, n AS new_hires, SUM(n) OVER (ORDER BY m ROWS UNBOUNDED PRECEDING) "
     "AS cumulative_hires FROM m",
     {"month_label": ("ky", None, ["tháng"]), "new_hires": ("so", True, ["tuyển"]),
      "cumulative_hires": ("so", False, ["lũy kế", "cộng dồn", "tích lũy"])}),
    ("S22", "Tuyển mới theo quý năm 2025",
     "SELECT N'Q' + CAST(DATEPART(QUARTER, ep.HireDate) AS nvarchar(1)) AS quy, COUNT(*) AS so_tuyen FROM dbo.Hrm_EmployeeProfile ep "
     "WHERE ep.TenantId = 63 AND ep.IsDeleted = 0 AND ep.HireDate >= '2025-01-01' AND ep.HireDate < '2026-01-01' "
     "GROUP BY DATEPART(QUARTER, ep.HireDate)",
     {"quy": ("ky", None, ["quý"]), "so_tuyen": ("so", True, ["tuyển"])}),
    ("S23", "Tuyển mới theo tháng năm 2026 (tháng dạng số)",
     "SELECT MONTH(ep.HireDate) AS thang, COUNT(*) AS so_luong FROM dbo.Hrm_EmployeeProfile ep WHERE ep.TenantId = 63 AND ep.IsDeleted = 0 "
     "AND ep.HireDate >= '2026-01-01' AND ep.HireDate < '2027-01-01' GROUP BY MONTH(ep.HireDate)",
     {"thang": ("ky", None, ["tháng"]), "so_luong": ("so", True, ["tuyển", "số lượng", "nhân sự"])}),
    ("S24", "Cấp phát thiết bị theo chủng loại",
     f"WITH tb AS (SELECT a.AssetCategoryId, SUM({QTY}) AS tong FROM dbo.Asm_Assets a WHERE a.TenantId = 94 AND a.IsDeleted = 0 "
     "GROUP BY a.AssetCategoryId), cp AS (SELECT a.AssetCategoryId, SUM(ad.Quantity) AS dc FROM dbo.Asm_AssetAssignmentDetail ad "
     "JOIN dbo.Asm_Assets a ON a.Id = ad.AssetId WHERE a.TenantId = 94 AND ad.IsDeleted = 0 AND ad.ReturnDate IS NULL "
     "GROUP BY a.AssetCategoryId) SELECT c.Name AS loai, tb.tong AS total, ISNULL(cp.dc, 0) AS assigned, "
     "tb.tong - ISNULL(cp.dc, 0) AS remaining FROM tb JOIN dbo.Asm_AssetCategories c ON c.Id = tb.AssetCategoryId "
     "LEFT JOIN cp ON cp.AssetCategoryId = tb.AssetCategoryId",
     {"loai": ("nhom", None, ["chủng loại", "loại"]), "total": ("so", True, ["tổng"]), "assigned": ("so", True, ["cấp phát"]),
      "remaining": ("so", True, ["còn lại", "tồn"])}),
    # ---- đợt 2: kiểu alias và dạng dữ liệu còn thiếu
    ("S25", "Nhân sự theo phòng ban",
     f"SELECT d.DisplayName AS PhongBan, COUNT(*) AS SoLuongNhanSu FROM dbo.Hrm_EmployeeProfile ep JOIN dbo.Dms_WorkDepartment d "
     f"ON d.Id = ep.WorkDepartmentId WHERE ep.TenantId = 94 AND {ALIVE} GROUP BY d.DisplayName",
     {"PhongBan": ("nhom", None, ["phòng ban"]), "SoLuongNhanSu": ("so", True, ["nhân sự"])}),
    ("S26", "Thiết bị theo chủng loại",
     f"SELECT COALESCE(c.Name, N'(chưa phân loại)') AS CL, SUM({QTY}) AS SL FROM dbo.Asm_Assets a LEFT JOIN dbo.Asm_AssetCategories c "
     "ON c.Id = a.AssetCategoryId WHERE a.TenantId = 94 AND a.IsDeleted = 0 GROUP BY c.Name",
     {"CL": ("nhom", None, ["chủng loại", "loại"]), "SL": ("so", True, ["số lượng", "thiết bị"])}),
    ("S27", "Nhân sự theo phòng ban",
     f"SELECT d.DisplayName AS [ten phong ban], COUNT(*) AS [so nhan su] FROM dbo.Hrm_EmployeeProfile ep JOIN dbo.Dms_WorkDepartment d "
     f"ON d.Id = ep.WorkDepartmentId WHERE ep.TenantId = 63 AND {ALIVE} GROUP BY d.DisplayName",
     {"ten phong ban": ("nhom", None, ["phòng ban"]), "so nhan su": ("so", True, ["nhân sự"])}),
    ("S28", "Nhân sự theo chức vụ (số trả về dạng chuỗi)",
     f"SELECT p.Name AS chuc_vu, CAST(COUNT(*) AS nvarchar(10)) AS so_nguoi FROM dbo.Hrm_EmployeeProfile ep JOIN dbo.Dms_WorkPosition p "
     f"ON p.Id = ep.WorkPositionId WHERE ep.TenantId = 63 AND {ALIVE} GROUP BY p.Name",
     {"chuc_vu": ("nhom", None, ["chức vụ"]), "so_nguoi": ("so", True, ["người", "nhân sự", "số lượng"])}),
    ("S29", "Tỷ lệ nhân sự theo tình trạng",
     f"SELECT COALESCE(s.Name, N'(chưa có)') AS tinh_trang, CAST(CAST(ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS decimal(5,1)) "
     f"AS nvarchar(10)) + N'%' AS ty_le FROM dbo.Hrm_EmployeeProfile ep LEFT JOIN dbo.Hrm_EmployeeStatus s ON s.Id = ep.EmploymentStatusId "
     f"WHERE ep.TenantId = 63 AND {ALIVE} GROUP BY s.Name",
     {"tinh_trang": ("nhom", None, ["tình trạng"]), "ty_le": ("so", False, ["tỷ lệ"])}),
    ("S30", "Tổng hợp quy mô BQP1",
     f"SELECT (SELECT COUNT(*) FROM dbo.Hrm_EmployeeProfile ep WHERE ep.TenantId = 94 AND {ALIVE}) AS tong_nhan_su, "
     f"(SELECT SUM({QTY}) FROM dbo.Asm_Assets a WHERE a.TenantId = 94 AND a.IsDeleted = 0) AS tong_thiet_bi, "
     "(SELECT COUNT(*) FROM dbo.Dms_WorkDepartment d WHERE d.TenantId = 94 AND d.IsDeleted = 0) AS so_phong_ban",
     {"tong_nhan_su": ("so", True, ["nhân sự"]), "tong_thiet_bi": ("so", True, ["thiết bị"]),
      "so_phong_ban": ("so", True, ["phòng ban"])}),
    ("S31", "Tuyển mới theo năm và tháng",
     "SELECT YEAR(ep.HireDate) AS nam, MONTH(ep.HireDate) AS thang, COUNT(*) AS tuyen_moi FROM dbo.Hrm_EmployeeProfile ep "
     "WHERE ep.TenantId = 63 AND ep.IsDeleted = 0 AND ep.HireDate >= '2025-10-01' AND ep.HireDate < '2026-04-01' "
     "GROUP BY YEAR(ep.HireDate), MONTH(ep.HireDate)",
     {"nam": ("ky", None, ["năm"]), "thang": ("ky", None, ["tháng"]), "tuyen_moi": ("so", True, ["tuyển"])}),
    ("S32", "Tuyển mới theo tháng (dạng tháng/năm)",
     "SELECT FORMAT(ep.HireDate, 'MM/yyyy') AS ky_bao_cao, COUNT(*) AS so_tuyen FROM dbo.Hrm_EmployeeProfile ep WHERE ep.TenantId = 63 "
     "AND ep.IsDeleted = 0 AND ep.HireDate >= '2026-01-01' AND ep.HireDate < '2026-10-01' GROUP BY FORMAT(ep.HireDate, 'MM/yyyy')",
     {"ky_bao_cao": ("ky", None, ["kỳ", "tháng"]), "so_tuyen": ("so", True, ["tuyển"])}),
    ("S33", "Ngày vào làm gần nhất theo phòng ban",
     f"SELECT d.DisplayName AS phong_ban, MAX(ep.HireDate) AS ngay_vao_gan_nhat, COUNT(*) AS so_nhan_su FROM dbo.Hrm_EmployeeProfile ep "
     f"JOIN dbo.Dms_WorkDepartment d ON d.Id = ep.WorkDepartmentId WHERE ep.TenantId = 63 AND {ALIVE} GROUP BY d.DisplayName",
     {"phong_ban": ("nhom", None, ["phòng ban"]), "ngay_vao_gan_nhat": ("nhom", None, ["ngày"]),
      "so_nhan_su": ("so", True, ["nhân sự"])}),
    ("S34", "Thiết bị theo phòng ban (có thiết bị chưa gán)",
     f"SELECT d.DisplayName AS phong_ban, SUM({QTY}) AS so_luong FROM dbo.Asm_Assets a LEFT JOIN dbo.Dms_WorkDepartment d "
     "ON d.Id = a.WorkDepartmentId WHERE a.TenantId = 63 AND a.IsDeleted = 0 GROUP BY d.DisplayName",
     {"phong_ban": ("nhom", None, ["phòng ban"]), "so_luong": ("so", True, ["số lượng", "thiết bị"])}),
    ("S35", "Resignation rate by department",
     "SELECT d.DisplayName AS dept, CAST(100.0 * SUM(CASE WHEN ep.ResignationDate IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*) AS decimal(5,2)) "
     "AS resignation_rate FROM dbo.Hrm_EmployeeProfile ep JOIN dbo.Dms_WorkDepartment d ON d.Id = ep.WorkDepartmentId "
     "WHERE ep.TenantId = 63 AND ep.IsDeleted = 0 GROUP BY d.DisplayName",
     {"dept": ("nhom", None, ["phòng ban", "bộ phận"]), "resignation_rate": ("so", False, ["tỷ lệ"])}),
    ("S36", "Average assets per employee by tenant",
     f"WITH ns AS (SELECT ep.TenantId, COUNT(*) AS n FROM dbo.Hrm_EmployeeProfile ep WHERE {ALIVE} GROUP BY ep.TenantId), "
     f"tb AS (SELECT a.TenantId, SUM({QTY}) AS q FROM dbo.Asm_Assets a WHERE a.IsDeleted = 0 GROUP BY a.TenantId) "
     "SELECT t.TenancyName AS tenant_name, CAST(1.0 * tb.q / ns.n AS decimal(6,2)) AS avg_assets_per_employee FROM dbo.AbpTenants t "
     "JOIN ns ON ns.TenantId = t.Id JOIN tb ON tb.TenantId = t.Id WHERE t.IsDeleted = 0",
     {"tenant_name": ("nhom", None, ["đơn vị"]), "avg_assets_per_employee": ("so", False, ["trung bình", "bình quân"])}),
    ("S37", "Nhân sự theo phòng ban (mã dạng chuỗi)",
     f"SELECT CAST(d.Id AS nvarchar(20)) AS code, d.DisplayName AS name, COUNT(*) AS cnt FROM dbo.Hrm_EmployeeProfile ep "
     f"JOIN dbo.Dms_WorkDepartment d ON d.Id = ep.WorkDepartmentId WHERE ep.TenantId = 94 AND {ALIVE} GROUP BY d.Id, d.DisplayName",
     {"code": ("nhom", None, ["mã"]), "name": ("nhom", None, ["phòng ban", "tên"]), "cnt": ("so", True, ["nhân sự", "số lượng"])}),
    ("S38", "Biến động nhân sự theo phòng ban tháng 9/2026",
     "SELECT d.DisplayName AS pb, SUM(CASE WHEN ep.HireDate >= '2026-09-01' AND ep.HireDate < '2026-10-01' THEN 1 ELSE 0 END) AS vao, "
     "SUM(CASE WHEN ep.ResignationDate >= '2026-09-01' AND ep.ResignationDate < '2026-10-01' THEN 1 ELSE 0 END) AS ra, "
     "SUM(CASE WHEN ep.ResignationDate IS NULL OR ep.ResignationDate >= '2026-10-01' THEN 1 ELSE 0 END) AS con "
     "FROM dbo.Hrm_EmployeeProfile ep JOIN dbo.Dms_WorkDepartment d ON d.Id = ep.WorkDepartmentId "
     "WHERE ep.TenantId = 63 AND ep.IsDeleted = 0 GROUP BY d.DisplayName",
     {"pb": ("nhom", None, ["phòng ban"]), "vao": ("so", True, ["vào", "tuyển", "tăng"]),
      "ra": ("so", True, ["ra", "nghỉ", "giảm"]), "con": ("so", True, ["còn", "hiện có", "nhân sự"])}),
]


def _kieu(t) -> str:
    return {int: "int", float: "float", Decimal: "decimal", bool: "bit"}.get(t, "datetime" if t in (date, datetime) else "nvarchar")


def _gia_tri(v):
    return str(v) if isinstance(v, Decimal) else (v.isoformat() if isinstance(v, (date, datetime)) else v)


def fetch(conn, cid, bang, sql) -> dict:
    cur = conn.cursor()
    cur.execute(sql)
    rows = [[_gia_tri(v) for v in r] for r in cur.fetchall()]
    cols = [{"name": d[0], "data_type": _kieu(d[1]), "label": None, "unit": None} for d in cur.description]
    # Như Text-to-SQL: không bảo đảm thứ tự; kỳ thì sắp theo kỳ để dấu hiệu cộng dồn đọc được.
    return {"query_id": cid, "question": bang, "columns": cols, "rows": rows, "row_count": len(rows), "truncated": False,
            "schema_version": "stress", "policy_version": "stress", "source_tables": [], "warnings": []}


def cham(cols: list[dict], expect: dict) -> list[str]:
    from app.documents.nhan_cot import nhan_hop_le

    loi = []
    for c in cols:
        role, cong, kws = expect[c["name"]]
        label = (c.get("label") or "").lower()
        if c.get("role") != role:
            loi.append(f"{c['name']}: vai trò {c.get('role')} != {role}")
        if role == "so" and c.get("cong_duoc") is not cong:
            loi.append(f"{c['name']}: cộng được {c.get('cong_duoc')} != {cong}")
        if not nhan_hop_le(c.get("label"), c["name"]) or not any(k in label for k in kws):
            loi.append(f"{c['name']}: nhãn \"{c.get('label')}\" không chứa {kws}")
    return loi


async def main() -> None:
    import pyodbc

    from app.documents.nhan_cot import gan_nhan_cot
    from app.documents.slide_deck import query_parts

    out, n = Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 3
    out.mkdir(parents=True, exist_ok=True)
    conn = pyodbc.connect(os.environ["T2S_ODBC"], readonly=True, timeout=30)
    ket_qua, tong_cot, cot_dung, on_dinh, giay = [], 0, 0, 0, []
    for cid, bang, sql, expect in CASES:
        try:
            goc = fetch(conn, cid, bang, sql)
        except Exception as exc:  # noqa: BLE001
            print(cid, "LỖI SQL", str(exc)[:160])
            ket_qua.append({"id": cid, "sql_error": str(exc)[:300]})
            continue
        if goc["rows"] and any(c["name"] in ("nam", "thang", "quy", "thang_nam", "month_label", "ky_bao_cao") for c in goc["columns"]):
            goc["rows"].sort(key=lambda r: str(r[0]).zfill(4) if isinstance(r[0], int) else str(r[0]))
        lan = []
        for _ in range(n):
            r = copy.deepcopy(goc)
            t = time.monotonic()
            await gan_nhan_cot(r, bang)
            giay.append(time.monotonic() - t)
            lan.append(r)
        nhan_moi_lan = [[(c["label"], c.get("role"), c.get("cong_duoc")) for c in r["columns"]] for r in lan]
        loi_moi_lan = [cham(r["columns"], expect) for r in lan]
        dung = sum(1 for c in range(len(goc["columns"])) if all(
            not any(e.startswith(goc["columns"][c]["name"] + ":") for e in loi) for loi in loi_moi_lan))
        bien = sum(1 for j in range(len(goc["columns"])) if len({tuple(x[j]) for x in nhan_moi_lan}) > 1)
        tong_cot += len(goc["columns"])
        cot_dung += dung
        on_dinh += len(goc["columns"]) - bien
        ds = query_parts({"queries": [copy.deepcopy(lan[0])]}).datasets
        ds_info = [{"title": d.title, "time_axis": d.time_axis, "total_card": d.total_card,
                    "series": [s for s, _ in d.series], "categories": d.categories[:4]} for d in ds]
        rec = {"id": cid, "bang": bang, "rows": len(goc["rows"]), "labels": nhan_moi_lan[0], "errors": loi_moi_lan[0],
               "errors_any_run": sorted({e for loi in loi_moi_lan for e in loi}), "unstable_cols": bien, "datasets": ds_info}
        ket_qua.append(rec)
        (out / "cases").mkdir(exist_ok=True)       # dữ liệu đã gắn nhãn (lần 1) cho stress_deck.py dựng slide
        (out / "cases" / f"{cid}.json").write_text(json.dumps(lan[0], ensure_ascii=False), encoding="utf-8")
        print(cid, f"{dung}/{len(goc['columns'])} cột đúng mọi lần, {bien} cột không ổn định |",
              [(c[0], c[1], c[2]) for c in nhan_moi_lan[0]], "|", rec["errors_any_run"])
    conn.close()
    tong = {"ca": len(CASES), "cot": tong_cot, "cot_dung_moi_lan": cot_dung, "cot_on_dinh": on_dinh, "so_lan": n,
            "giay_tb": round(sum(giay) / max(len(giay), 1), 2), "giay_max": round(max(giay or [0]), 2)}
    print(json.dumps(tong, ensure_ascii=False))
    (out / "stress.json").write_text(json.dumps({"tong": tong, "ca": ket_qua}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
