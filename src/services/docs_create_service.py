from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

def create_element(name):
    return OxmlElement(name)

def set_cell_border(cell):
    """Ẩn toàn bộ đường viền của bảng"""
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcBorders = create_element('w:tcBorders')
    for border_name in ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']:
        border = create_element(f'w:{border_name}')
        border.set(qn('w:val'), 'none')
        tcBorders.append(border)
    tcPr.append(tcBorders)

# 1. Khởi tạo tài liệu
doc = Document()

# 2. Cài đặt lề trang chuẩn hành chính (Trái 3.0 cm, các lề còn lại 2.0 cm)
for section in doc.sections:
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(2.0)
    section.left_margin = Cm(3.0)
    section.right_margin = Cm(2.0)

# 3. Thiết lập font chữ mặc định cho toàn văn bản (Times New Roman, cỡ 13)
style = doc.styles['Normal']
font = style.font
font.name = 'Times New Roman'
font.size = Pt(13)

# 4. Tạo phần Tiêu đề đầu trang (Quốc hiệu và Cơ quan ban hành) bằng bảng 2 cột
header_table = doc.add_table(rows=1, cols=2)
header_table.autofit = False
header_table.columns[0].width = Cm(6.5)
header_table.columns[1].width = Cm(9.5)

# Cột 1: Tên cơ quan ban hành
cell_left = header_table.cell(0, 0)
set_cell_border(cell_left)
p_left_1 = cell_left.paragraphs[0]
p_left_1.alignment = WD_ALIGN_PARAGRAPH.CENTER
run_left_1 = p_left_1.add_run("VĂN PHÒNG CHÍNH PHỦ")
run_left_1.font.name = 'Times New Roman'
run_left_1.font.size = Pt(12)
run_left_1.bold = True

p_left_2 = cell_left.add_paragraph()
p_left_2.alignment = WD_ALIGN_PARAGRAPH.CENTER
run_left_2 = p_left_2.add_run("Số: ....../QĐ-VPCP")
run_left_2.font.name = 'Times New Roman'
run_left_2.font.size = Pt(13)

# Cột 2: Quốc hiệu và Tiêu ngữ
cell_right = header_table.cell(0, 1)
set_cell_border(cell_right)
p_right_1 = cell_right.paragraphs[0]
p_right_1.alignment = WD_ALIGN_PARAGRAPH.CENTER
run_right_1 = p_right_1.add_run("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM")
run_right_1.font.name = 'Times New Roman'
run_right_1.font.size = Pt(12)
run_right_1.bold = True

p_right_2 = cell_right.add_paragraph()
p_right_2.alignment = WD_ALIGN_PARAGRAPH.CENTER
run_right_2 = p_right_2.add_run("Độc lập - Tự do - Hạnh phúc")
run_right_2.font.name = 'Times New Roman'
run_right_2.font.size = Pt(13)
run_right_2.bold = True

# Thêm dòng kẻ phụ dưới Tiêu ngữ
p_right_3 = cell_right.add_paragraph()
p_right_3.alignment = WD_ALIGN_PARAGRAPH.CENTER
run_line = p_right_3.add_run("_______________")
run_line.font.name = 'Times New Roman'
run_line.font.size = Pt(13)

# 5. Thêm khoảng trống và Địa danh, ngày tháng
p_date = doc.add_paragraph()
p_date.alignment = WD_ALIGN_PARAGRAPH.RIGHT
p_date.paragraph_format.space_before = Pt(12)
run_date = p_date.add_run("Hà Nội, ngày ..... tháng ..... năm 2026")
run_date.font.italic = True

# 6. Tên văn bản (Ví dụ: QUYẾT ĐỊNH)
p_title = doc.add_paragraph()
p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
p_title.paragraph_format.space_before = Pt(24)
p_title.paragraph_format.space_after = Pt(6)
run_title = p_title.add_run("QUYẾT ĐỊNH")
run_title.font.size = Pt(14)
run_title.bold = True

# Trích yếu nội dung
p_summary = doc.add_paragraph()
p_summary.alignment = WD_ALIGN_PARAGRAPH.CENTER
p_summary.paragraph_format.space_after = Pt(18)
run_summary = p_summary.add_run("Về việc quy định chức năng, nhiệm vụ và quyền hạn...")
run_summary.font.italic = True

# 7. Nội dung đoạn văn mẫu (Căn đều 2 lề, giãn dòng 1.15, thụt đầu dòng 1.27cm)
p_body = doc.add_paragraph()
p_body.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
p_body.paragraph_format.line_spacing = 1.15
p_body.paragraph_format.space_after = Pt(6)
p_body.paragraph_format.first_line_indent = Cm(1.27)

run_body = p_body.add_run(
    "Căn cứ Nghị định của Chính phủ quy định chức năng, nhiệm vụ, quyền hạn "
    "và cơ cấu tổ chức của Văn phòng Chính phủ; Xét đề nghị của Vụ trưởng Vụ Tổ chức cán bộ..."
)

# Lưu tài liệu
doc.save("Van_Phong_Chinh_Phu_Format.docx")
print("Đã tạo file Van_Phong_Chinh_Phu_Format.docx thành công!")
