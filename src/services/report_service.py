"""Service to generate department reports for personnel and assets.
This module queries MSSQL to get department, employee, personnel and asset data,
creates summary statistics and charts, fills a Word template and saves the report.
"""
import os
import re
import json
import datetime
import tempfile
from typing import List, Dict, Tuple, Optional

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import seaborn as sns
except Exception:
    plt = None
    sns = None

from docx import Document
from docx.shared import Inches

from src.services.mssql_retrieval_service import MSSQLRetrievalService


def _safe_get(rows, idx, default=None):
    try:
        return rows[idx]
    except Exception:
        return default


def _is_report_request(query: str) -> bool:
    q = query.lower()
    keywords = [
        'nhân sự', 'nhan su', 'nhân sự', 'trang thiết bị', 'thiet bi', 'thiết bị',
        'báo cáo', 'kiểm kê', 'kiem ke', 'tình trạng', 'tinh trang', 'báo cáo kiểm kê',
        'báo cáo phòng', 'tổng hợp', 'tổng hợp nhân sự', 'tổng hợp trang thiết bị'
    ]
    for kw in keywords:
        if kw in q:
            return True
    return False


def generate_department_report(tenant_id: str, employee_id: str, report_types: List[str] = None, template_path: str = 'src/templates/Bao_cao_kiem_ke_Phong_Ky_Thuat.docx', output_dir: str = 'src/output_templates') -> Tuple[str, str]:
    """
    Generate a department report for the employee's department.
    report_types: subset of ['personnel', 'assets']
    Returns: (text_summary, out_docx_path)
    """
    if report_types is None:
        report_types = ['personnel', 'assets']

    sql = MSSQLRetrievalService()

    # 1. Find employee and department
    emp_q = f"SELECT Id, TenantID, FullName, WorkDepartmentId FROM Hrm_EmployeeProfile WHERE Id = '{employee_id}' AND TenantID = '{tenant_id}'"
    emp_rows = sql.get_rows(emp_q)
    if not emp_rows:
        raise ValueError('Không tìm thấy thông tin nhân viên cho employee_id cung cấp.')

    emp = emp_rows[0]
    # row columns: use description from cursor if available
    # fallback indices
    try:
        emp_id = emp[0]
        emp_fullname = emp[2]
        emp_dept_id = emp[3]
    except Exception:
        emp_id = getattr(emp, 'Id', None)
        emp_fullname = getattr(emp, 'FullName', None)
        emp_dept_id = getattr(emp, 'WorkDepartmentId', None)

    if not emp_dept_id:
        raise ValueError('Nhân viên hiện không thuộc phòng ban nào (WorkDepartmentId trống).')

    dept_q = f"SELECT Id, TenantId, Code, DepartmentCode, DisplayName FROM Dms_WorkDepartment WHERE Id = '{emp_dept_id}' AND TenantId = '{tenant_id}'"
    dept_rows = sql.get_rows(dept_q)
    if not dept_rows:
        dept_name = f'ID phòng {emp_dept_id}'
    else:
        d = dept_rows[0]
        try:
            dept_name = d[4]  # DisplayName
        except Exception:
            dept_name = getattr(d, 'DisplayName', str(emp_dept_id))

    summary_lines = []
    summary_lines.append(f"Báo cáo nhanh cho phòng: {dept_name}")
    summary_lines.append(f"Thực hiện cho người yêu cầu: {emp_fullname} (ID: {emp_id})")
    summary_lines.append(f"Ngày tạo: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    summary_lines.append("")

    charts = []
    tmp_chart_dir = os.path.join(output_dir, 'charts')
    os.makedirs(tmp_chart_dir, exist_ok=True)

    # personnel
    if 'personnel' in report_types:
        employees_q = f"SELECT Id, FullName, Gender, HireDate, OfficialDate, ResignationDate, WorkPositionId, DirectManagerId, EducationLevel, MajorName FROM Hrm_EmployeeProfile WHERE WorkDepartmentId = '{emp_dept_id}' AND TenantID = '{tenant_id}'"
        emp_rows = sql.get_rows(employees_q)
        personnel = []
        for r in emp_rows:
            personnel.append({
                'Id': r[0], 'FullName': r[1], 'Gender': r[2], 'HireDate': r[3], 'OfficialDate': r[4], 'ResignationDate': r[5], 'WorkPositionId': r[6], 'DirectManagerId': r[7], 'EducationLevel': r[8], 'MajorName': r[9]
            })

        total_personnel = len(personnel)
        summary_lines.append(f"Tổng số nhân sự hiện có trong phòng: {total_personnel}")

        # position distribution
        pos_counts = {}
        gender_counts = {}
        education_counts = {}
        hires_by_year = {}
        for p in personnel:
            pos = str(p.get('WorkPositionId') or '0')
            pos_counts[pos] = pos_counts.get(pos, 0) + 1
            g = (p.get('Gender') or 'Khác')
            gender_counts[g] = gender_counts.get(g, 0) + 1
            edu = (p.get('EducationLevel') or 'Khác')
            education_counts[edu] = education_counts.get(edu, 0) + 1
            hd = p.get('HireDate')
            if hd:
                try:
                    year = None
                    if isinstance(hd, (str,)):
                        year = hd[:4]
                    else:
                        year = str(hd.year)
                    hires_by_year[year] = hires_by_year.get(year, 0) + 1
                except Exception:
                    pass

        summary_lines.append('Phân bố theo vị trí (WorkPositionId): ' + ', '.join([f"{k}:{v}" for k, v in pos_counts.items()]))
        summary_lines.append('Phân bố theo giới tính: ' + ', '.join([f"{k}:{v}" for k, v in gender_counts.items()]))
        summary_lines.append('Phân bố theo trình độ: ' + ', '.join([f"{k}:{v}" for k, v in education_counts.items()]))

        # charts: pos_counts and gender_counts
        if plt is not None:
            try:
                # position bar
                fig, ax = plt.subplots(figsize=(6, 4))
                sns.barplot(x=list(pos_counts.keys()), y=list(pos_counts.values()), palette='Blues_d', ax=ax)
                ax.set_title('Phân bố theo vị trí (WorkPositionId)')
                ax.set_xlabel('WorkPositionId')
                ax.set_ylabel('Số lượng')
                pos_chart = os.path.join(tmp_chart_dir, f'pos_{emp_dept_id}.png')
                fig.tight_layout()
                fig.savefig(pos_chart)
                plt.close(fig)
                charts.append(pos_chart)

                # gender pie
                fig2, ax2 = plt.subplots(figsize=(5, 4))
                ax2.pie(list(gender_counts.values()), labels=list(gender_counts.keys()), autopct='%1.1f%%', startangle=140)
                ax2.set_title('Tỷ lệ theo giới tính')
                gender_chart = os.path.join(tmp_chart_dir, f'gender_{emp_dept_id}.png')
                fig2.savefig(gender_chart)
                plt.close(fig2)
                charts.append(gender_chart)
            except Exception:
                # skip charts if plotting fails
                pass

    # assets
    assets_list = []
    if 'assets' in report_types:
        assets_q = f"SELECT a.Id, a.Name, a.AssetCategoryId, c.Name as CategoryName, a.PurchaseDate, a.PurchasePrice, a.Supplier, a.Description, a.Status, a.Quantity FROM Asm_Assets a LEFT JOIN Asm_AssetCategories c ON a.AssetCategoryId = c.Id WHERE a.WorkDepartmentId = '{emp_dept_id}' AND a.TenantId = '{tenant_id}'"
        rows = sql.get_rows(assets_q)
        for r in rows:
            assets_list.append({'Id': r[0], 'Name': r[1], 'AssetCategoryId': r[2], 'CategoryName': r[3], 'PurchaseDate': r[4], 'PurchasePrice': r[5], 'Supplier': r[6], 'Description': r[7], 'Status': r[8], 'Quantity': r[9] or 1})

        total_assets = sum([int(a.get('Quantity') or 0) for a in assets_list]) if assets_list else 0
        summary_lines.append(f"Tổng số trang thiết bị (tính theo quantity): {total_assets}")

        # distribution by category
        cat_counts = {}
        status_counts = {}
        for a in assets_list:
            cat = a.get('CategoryName') or 'Khác'
            cat_counts[cat] = cat_counts.get(cat, 0) + int(a.get('Quantity') or 0)
            st = a.get('Status') or 'Không rõ'
            status_counts[st] = status_counts.get(st, 0) + int(a.get('Quantity') or 0)

        summary_lines.append('Phân bố theo loại thiết bị: ' + ', '.join([f"{k}:{v}" for k, v in cat_counts.items()]))
        summary_lines.append('Phân bố theo tình trạng thiết bị: ' + ', '.join([f"{k}:{v}" for k, v in status_counts.items()]))

        if plt is not None:
            try:
                # top categories bar
                fig3, ax3 = plt.subplots(figsize=(6, 4))
                cats = list(cat_counts.keys())
                vals = list(cat_counts.values())
                sns.barplot(x=vals, y=cats, palette='Greens_d', ax=ax3)
                ax3.set_title('Số lượng theo loại thiết bị')
                ax3.set_xlabel('Số lượng')
                cat_chart = os.path.join(tmp_chart_dir, f'cat_{emp_dept_id}.png')
                fig3.tight_layout()
                fig3.savefig(cat_chart)
                plt.close(fig3)
                charts.append(cat_chart)

                # status pie
                fig4, ax4 = plt.subplots(figsize=(5, 4))
                ax4.pie(list(status_counts.values()), labels=list(status_counts.keys()), autopct='%1.1f%%', startangle=140)
                ax4.set_title('Tình trạng thiết bị')
                status_chart = os.path.join(tmp_chart_dir, f'status_{emp_dept_id}.png')
                fig4.savefig(status_chart)
                plt.close(fig4)
                charts.append(status_chart)
            except Exception:
                pass

    # create document from template
    os.makedirs(output_dir, exist_ok=True)
    try:
        doc = Document(template_path)
    except Exception:
        # fallback to a blank document
        doc = Document()

    # replace simple placeholders in doc (e.g., {{DEPT_NAME}}, {{REPORT_DATE}}, etc.)
    def _replace_in_doc(document: Document, mapping: Dict[str, str]):
        import re
        for p in document.paragraphs:
            text = p.text
            if not text:
                continue
            new_text = text
            for k, v in mapping.items():
                new_text = re.sub(r"\{\{\s*" + re.escape(k) + r"\s*\}\}", str(v), new_text)
            if new_text != text:
                # replace runs
                for i in range(len(p.runs)-1, -1, -1):
                    p._element.remove(p.runs[i]._r)
                p.add_run(new_text)
        # tables
        for t in document.tables:
            for row in t.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        text = p.text
                        new_text = text
                        for k, v in mapping.items():
                            new_text = re.sub(r"\{\{\s*" + re.escape(k) + r"\s*\}\}", str(v), new_text)
                        if new_text != text:
                            for i in range(len(p.runs)-1, -1, -1):
                                p._element.remove(p.runs[i]._r)
                            p.add_run(new_text)

    mapping = {
        'DEPT_NAME': dept_name,
        'REPORT_DATE': datetime.datetime.now().strftime('%Y-%m-%d'),
        'REQUEST_BY': emp_fullname,
        'TOTAL_PERSONNEL': str(total_personnel) if 'personnel' in report_types else 'N/A',
        'TOTAL_ASSETS': str(total_assets) if 'assets' in report_types else 'N/A'
    }
    _replace_in_doc(doc, mapping)

    # append summary and charts at end
    doc.add_page_break()
    doc.add_heading('Tóm tắt nhanh', level=2)
    for line in summary_lines:
        doc.add_paragraph(line)

    if charts:
        doc.add_page_break()
        doc.add_heading('Biểu đồ', level=2)
        for c in charts:
            try:
                doc.add_picture(c, width=Inches(6))
            except Exception:
                # skip if add_picture fails
                pass

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    out_name = f"BaoCao_Phong_{emp_dept_id}_{ts}.docx"
    out_path = os.path.join(output_dir, out_name)
    doc.save(out_path)

    # close sql connection
    try:
        sql.close_connection()
    except Exception:
        pass

    text_summary = '\n'.join(summary_lines)
    return text_summary, out_path


# Exported
is_report_request = _is_report_request
