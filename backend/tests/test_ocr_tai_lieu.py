"""Màn OCR tài liệu: phân loại trang, chế độ ép OCR, và hai cửa API.

Mô hình thị giác được thay bằng bản giả - bài test này soát phần logic quanh nó:
trang nào bị đẩy sang OCR, trang nào lấy thẳng lớp text, số trang có giữ đúng khi
một trang đọc lỗi không, và stream có phát đủ `start` → `trang` → `done` không.
Chất lượng chữ mà model đọc ra không thuộc phạm vi ở đây.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pymupdf
import pytest

from app.api import ocr as ocr_api
from app.documents import ocr_tai_lieu as odt
from app.main import app

pytestmark = pytest.mark.anyio


# --------------------------------------------------------------------------- #
# Đồ giả
# --------------------------------------------------------------------------- #
class OcrGia:
    """Đứng thay vLLM: trả về một dòng Markdown cố định cho mỗi trang."""

    def __init__(self, *, enabled: bool = True, hong: set[int] | None = None) -> None:
        self.enabled = enabled
        self.hong = hong or set()      # trang (0-based) giả vờ đọc lỗi
        self.da_goi: list[int] = []

    def classify_pages(self, path: Path) -> list[bool]:  # pragma: no cover - bị ghi đè
        raise AssertionError("test phải dùng bản phân loại thật")

    def ocr_pages_stream(self, path, page_numbers):
        for index in page_numbers:
            if index in self.hong:
                continue           # bản thật nuốt lỗi và bỏ qua trang
            self.da_goi.append(index)
            yield index, f"## Trang {index + 1}\n\nDòng do mô hình đọc."


def _pdf(tmp_path: Path, trang: list[str], ten: str = "tai-lieu.pdf") -> Path:
    """PDF nhiều trang; trang nào truyền chuỗi rỗng thì để trắng (giống trang scan).

    Chữ phải đi qua `insert_textbox` chứ không phải `insert_text`: chữ đặt tại một
    điểm không thành khối văn bản nào, và pymupdf4llm trả về trang rỗng - test sẽ
    đỏ vì cách dựng file mẫu chứ không phải vì mã sản phẩm. Nội dung để không dấu
    vì phông base-14 của PDF không có glyph tiếng Việt.
    """
    doc = pymupdf.open()
    for noi_dung in trang:
        page = doc.new_page()
        if noi_dung:
            page.insert_textbox(pymupdf.Rect(72, 72, 520, 700), noi_dung,
                                fontsize=11, fontname="helv")
    path = tmp_path / ten
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def ocr_gia(monkeypatch):
    """Thay mô hình thật nhưng GIỮ bản phân loại thật của service."""
    from app.rag.ocr import get_ocr_service

    that = get_ocr_service()
    gia = OcrGia()
    gia.classify_pages = that.classify_pages  # type: ignore[method-assign]
    monkeypatch.setattr(odt, "get_ocr_service", lambda: gia)
    return gia


# --------------------------------------------------------------------------- #
# Lập kế hoạch
# --------------------------------------------------------------------------- #
async def test_trang_digital_khong_bi_day_sang_ocr(tmp_path, ocr_gia):
    """Trang có chữ số hoá thì đọc thẳng, không tốn một lượt mô hình nào."""
    day = " ".join(["Dieu 1. Ban hanh kem theo quyet dinh nay quy che."] * 8)
    path = _pdf(tmp_path, [day, day])

    ke_hoach = odt.lap_ke_hoach(path, "auto")

    assert ke_hoach.so_trang == 2
    assert ke_hoach.trang_can_ocr == []
    assert len(ke_hoach.trang_digital) == 2
    assert "Dieu 1" in ke_hoach.trang_digital[0]


async def test_trang_trang_bi_coi_la_scan(tmp_path, ocr_gia):
    """Trang không chữ, không font nhúng -> đủ phiếu để coi là trang scan."""
    day = " ".join(["Quy che lam viec cua co quan ban hanh kem quyet dinh."] * 8)
    path = _pdf(tmp_path, [day, ""])

    ke_hoach = odt.lap_ke_hoach(path, "auto")

    assert ke_hoach.trang_can_ocr == [1]
    assert set(ke_hoach.trang_digital) == {0}


async def test_che_do_tat_ca_ep_moi_trang_qua_mo_hinh(tmp_path, ocr_gia):
    """Lớp text có sẵn cũng bị bỏ: người dùng đã nói là không tin nó."""
    day = " ".join(["Dieu 2. Quyet dinh nay co hieu luc ke tu ngay ky."] * 8)
    path = _pdf(tmp_path, [day, day])

    ke_hoach = odt.lap_ke_hoach(path, "tat_ca")

    assert ke_hoach.trang_can_ocr == [0, 1]
    assert ke_hoach.trang_digital == {}


async def test_che_do_la(tmp_path, ocr_gia):
    path = _pdf(tmp_path, ["Noi dung cua trang thu nhat."])
    with pytest.raises(odt.OcrError):
        odt.lap_ke_hoach(path, "nhanh-len")


def test_dinh_dang_khong_nhan_van_ban_co_san_chu():
    """.docx đã có chữ - nhận vào đây là đánh lừa người dùng rằng vừa OCR."""
    with pytest.raises(odt.OcrError):
        odt.kiem_tra_dinh_dang("bao-cao.docx")
    assert odt.kiem_tra_dinh_dang("scan.PDF") == ".pdf"
    assert odt.kiem_tra_dinh_dang("anh.png") == ".png"


# --------------------------------------------------------------------------- #
# Đọc trang
# --------------------------------------------------------------------------- #
async def test_ocr_tat_thi_bao_thay_vi_tra_ve_trang_rong(tmp_path, ocr_gia):
    ocr_gia.enabled = False
    path = _pdf(tmp_path, [""])
    ke_hoach = odt.lap_ke_hoach(path, "auto")

    with pytest.raises(odt.OcrError, match="OCR đang tắt"):
        list(odt.doc_trang_ocr(path, ke_hoach))


async def test_trang_loi_van_giu_cho_de_so_trang_khong_lech(tmp_path, ocr_gia):
    """Bỏ hẳn trang hỏng thì mọi trang sau bị lùi một số - người xem đọc sai trang."""
    ocr_gia.hong = {1}
    path = _pdf(tmp_path, ["", "", ""])
    ke_hoach = odt.lap_ke_hoach(path, "auto")

    da_doc = {t.so_trang: t for t in odt.doc_trang_ocr(path, ke_hoach)}
    day_du = odt.dung_day_du(ke_hoach, da_doc)

    assert [t.so_trang for t in day_du] == [1, 2, 3]
    assert day_du[1].nguon == odt.NGUON_LOI and day_du[1].markdown == ""
    assert day_du[2].nguon == odt.NGUON_OCR and "Trang 3" in day_du[2].markdown


def test_ghep_markdown_ngan_trang_bang_duong_ke():
    trang = [
        odt.TrangKetQua(so_trang=1, nguon=odt.NGUON_OCR, markdown="# Một"),
        odt.TrangKetQua(so_trang=2, nguon=odt.NGUON_OCR, markdown="# Hai"),
    ]
    assert odt.ghep_markdown(trang) == "# Một\n\n---\n\n# Hai"
    assert odt.ghep_markdown(trang[:1]) == "# Một"
    assert odt.ghep_markdown([]) == ""


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
async def _call(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


@pytest.fixture
def kho_upload(tmp_path, monkeypatch):
    """Thư mục upload riêng cho test, khỏi rải file vào data/uploads thật."""
    from app.core.config import get_settings

    cfg = get_settings()
    monkeypatch.setattr(cfg, "upload_dir", str(tmp_path / "uploads"))
    (tmp_path / "uploads").mkdir(parents=True, exist_ok=True)
    return tmp_path


async def test_extract_tra_ve_markdown_va_thong_ke(tmp_path, ocr_gia, kho_upload):
    day = " ".join(["Dieu 3. Thu truong don vi chiu trach nhiem thi hanh."] * 8)
    path = _pdf(tmp_path, [day, ""])

    res = await _call("POST", "/api/ocr/extract",
                      files={"file": ("hop-dong.pdf", path.read_bytes(), "application/pdf")},
                      data={"che_do": "auto"})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["so_trang"] == 2
    assert body["so_trang_ocr"] == 1
    assert body["so_trang_digital"] == 1
    assert [t["so_trang"] for t in body["trang"]] == [1, 2]
    assert body["trang"][0]["nguon"] == "digital"
    assert body["trang"][1]["nguon"] == "ocr"
    assert "Dieu 3" in body["markdown"]
    assert "Trang 2" in body["markdown"]
    assert "\n---\n" in body["markdown"]


async def test_extract_tu_choi_dinh_dang_khong_co_anh_trang(kho_upload):
    res = await _call("POST", "/api/ocr/extract",
                      files={"file": ("bao-cao.docx", b"PK\x03\x04", "application/octet-stream")})
    assert res.status_code == 415


async def test_stream_phat_du_start_trang_done(tmp_path, ocr_gia, kho_upload):
    from app.services import storage

    path = _pdf(tmp_path, ["", ""], ten="scan.pdf")
    ref = storage.save_upload(path.read_bytes(), "scan.pdf", kind="upload")

    res = await _call("POST", "/api/ocr/extract/stream",
                      json={"file_id": ref.file_id, "che_do": "auto"})
    assert res.status_code == 200

    su_kien: list[tuple[str, dict]] = []
    for khoi in res.text.split("\n\n"):
        if not khoi.strip():
            continue
        ten = next(d[len("event:"):].strip() for d in khoi.split("\n") if d.startswith("event:"))
        data = next(d[len("data:"):].strip() for d in khoi.split("\n") if d.startswith("data:"))
        su_kien.append((ten, json.loads(data)))

    ten_su_kien = [e for e, _ in su_kien]
    assert ten_su_kien[0] == "start"
    assert ten_su_kien[-1] == "done"
    assert ten_su_kien.count("trang") == 2

    start = su_kien[0][1]
    assert start["so_trang"] == 2 and start["so_trang_ocr"] == 2

    done = su_kien[-1][1]
    assert done["so_trang_ocr"] == 2 and done["so_trang_trong"] == 0
    assert sorted(p["so_trang"] for e, p in su_kien if e == "trang") == [1, 2]


async def test_ten_file_kieu_email_van_giu_duoc_duoi(tmp_path, ocr_gia, kho_upload):
    """Cổng ABP gửi tên file tiếng Việt dưới dạng "=?utf-8?B?...?=" (RFC 2047).

    Đuôi `.pdf` nằm trong phần base64. Lọc ký tự lạ trước khi giải mã thì `=` và
    `?` thành `_`, file lưu xuống mất đuôi, và `/extract/stream` trả 415 "chỉ đọc
    được ảnh trang" - đúng file đó tải thẳng từ trình duyệt lại chạy ngon. Đó là
    lý do cùng một tài liệu lúc nhận lúc không.
    """
    import base64

    from app.services import storage

    path = _pdf(tmp_path, ["", ""], ten="scan.pdf")
    ten_that = "Bảng PL3a Hợp đồng 54.pdf"
    ten_gui = "=?utf-8?B?" + base64.b64encode(ten_that.encode()).decode() + "?="

    res = await _call("POST", "/api/agent/upload",
                      files={"file": (ten_gui, path.read_bytes(), "application/pdf")},
                      data={"muc_dich": "upload"})
    assert res.status_code == 200, res.text
    file_id = res.json()["file_id"]
    assert file_id.endswith(".pdf"), file_id

    # Cùng tài liệu, tải thẳng từ trình duyệt: hai đường phải ra cùng một tên,
    # không thì kho upload có hai bản của một file.
    assert storage.safe_name(ten_gui) == storage.safe_name(ten_that)

    res = await _call("POST", "/api/ocr/extract/stream",
                      json={"file_id": file_id, "che_do": "auto"})
    assert res.status_code == 200, res.text
    assert "event: start" in res.text


@pytest.mark.parametrize("duong, ten", [
    ("/api/documents/upload", "Báo cáo kiểm kê.docx"),
    ("/api/documents/review", "Báo cáo kiểm kê.docx"),
    ("/api/ocr/extract", "Công văn số 1516.pdf"),
])
async def test_cua_nap_tai_lieu_cung_giai_duoc_ten_kieu_email(duong, ten, kho_upload):
    """Kiểm đuôi file phải đọc trên tên ĐÃ làm sạch, ở mọi cửa nhận file.

    Ba cửa `/api/ocr/extract`, `/api/documents/upload` và `/api/documents/review`
    đều tự kiểm đuôi rồi mới nhận. Cửa nào đọc tên thô thì tên kiểu
    "=?utf-8?B?...?=" bị chặn ngay bằng 415, dù đó là .docx thật.
    """
    import base64

    ma = "=?utf-8?B?" + base64.b64encode(ten.encode()).decode() + "?="

    res = await _call("POST", duong,
                      files={"file": (ma, b"noi dung gia", "application/octet-stream")})
    # Nội dung là file giả nên bước đọc sẽ hỏng, nhưng phải hỏng VÌ nội dung -
    # không được dừng ngay ở cửa vì tưởng sai định dạng.
    assert res.status_code != 415, res.text


def test_moi_kieu_ma_hoa_ten_deu_ra_mot_ten():
    """Năm cách client gửi cùng một tên file phải cho cùng một kết quả.

    Hai cái bẫy nằm ở đây:

    - Bảng chữ cái base64 có cả `/`. Cắt thư mục TRƯỚC khi giải mã thì phần
      base64 bị xén ở dấu `/` cuối, còn lại một mẩu rác không giải được và cũng
      không còn đuôi - đúng cái tên `Mm8yAbmcgeHV5ZcyCbi5wZGY` đã thấy trong kho.
    - macOS gửi tên ở dạng NFD ("e" + dấu mũ rời), Windows gửi NFC ("ê"). Không
      chuẩn hoá thì mỗi dấu rời thành thêm một `_`, và cùng một file nằm hai bản
      trong kho dưới hai cái tên.
    """
    import base64
    import unicodedata
    from email.header import Header

    from app.services import storage

    ten = ("681 QĐ vv phê duyệt nhiệm vụ và dự toán chi tiết kinh phí thực hiện "
           "nhiệm vụ sử dụng nguồn chi thường xuyên.pdf")
    nfd = unicodedata.normalize("NFD", ten)

    def mot_word(raw: str) -> str:
        return "=?utf-8?B?" + base64.b64encode(raw.encode()).decode() + "?="

    cach_gui = [
        ten,                          # trình duyệt gửi thẳng
        nfd,                          # macOS, dạng NFD
        mot_word(ten),                # cổng ABP, một encoded-word
        mot_word(nfd),                # ... và base64 của nó có chứa "/"
        Header(ten, "utf-8").encode(),  # chẻ thành nhiều encoded-word
    ]
    assert "/" in mot_word(nfd), "ca thử mất ý nghĩa nếu base64 không có dấu /"

    ket_qua = {storage.safe_name(c) for c in cach_gui}
    assert len(ket_qua) == 1, ket_qua
    ten_luu = ket_qua.pop()
    assert ten_luu.endswith(".pdf")
    assert storage.safe_name(ten_luu) == ten_luu


def test_ten_van_ban_qua_dai_van_giu_duoc_duoi():
    """Cắt phần thân, đừng cắt cụt cả tên.

    Tên văn bản hành chính tiếng Việt vượt 180 ký tự là chuyện thường - mỗi chữ
    có dấu thành một `_` sau khi lọc. Cắt thẳng `name[:180]` thì đuôi `.pdf` rơi
    mất, và file hoá ra không OCR được y như trường hợp encoded-word.
    """
    from app.services import storage

    dai = ("Quyết định về việc phê duyệt nhiệm vụ và dự toán chi tiết kinh phí "
           "thực hiện nhiệm vụ sử dụng nguồn chi thường xuyên lĩnh vực chuyển đổi "
           "số của Ngành Nông nghiệp và Môi trường năm 2026.pdf")
    assert len(dai) > 180

    ten = storage.safe_name(dai)
    assert len(ten) <= 180
    assert ten.endswith(".pdf")
    assert storage.safe_name(ten) == ten


def test_ten_file_kieu_email_khong_mo_duong_vuot_thu_muc():
    """Giải mã xong vẫn phải cắt thư mục: encoded-word giấu được cả `../`."""
    import base64

    from app.services import storage

    doc_hai = "=?utf-8?B?" + base64.b64encode(b"../../etc/passwd").decode() + "?="
    assert storage.safe_name(doc_hai) == "passwd"


async def test_stream_bao_loi_khi_file_khong_ton_tai(kho_upload):
    res = await _call("POST", "/api/ocr/extract/stream",
                      json={"file_id": "upload:khong-co-that.pdf"})
    assert res.status_code == 404


async def test_status_noi_ro_model_nao_dang_doc():
    res = await _call("GET", "/api/ocr/status")
    assert res.status_code == 200
    body = res.json()
    assert ".pdf" in body["dinh_dang"]
    assert body["model"]


async def test_stream_phat_ca_trang_doc_hong(tmp_path, ocr_gia, kho_upload):
    """Trang mô hình không đọc được vẫn phải có mặt, mang nhãn riêng.

    Client dựng danh sách trang từ chính các sự kiện `trang`; thiếu một trang là
    thiếu một ô trên màn hình, và người xem không biết mình đang thiếu chữ.
    """
    from app.services import storage

    ocr_gia.hong = {1}
    path = _pdf(tmp_path, ["", "", ""], ten="ba-trang.pdf")
    ref = storage.save_upload(path.read_bytes(), "ba-trang.pdf", kind="upload")

    res = await _call("POST", "/api/ocr/extract/stream",
                      json={"file_id": ref.file_id, "che_do": "auto"})

    trang, done = {}, {}
    for khoi in res.text.split("\n\n"):
        if not khoi.strip():
            continue
        ten = next(d[len("event:"):].strip() for d in khoi.split("\n") if d.startswith("event:"))
        data = json.loads(next(d[len("data:"):].strip() for d in khoi.split("\n") if d.startswith("data:")))
        if ten == "trang":
            trang[data["so_trang"]] = data
        elif ten == "done":
            done = data

    assert sorted(trang) == [1, 2, 3]
    assert trang[2]["nguon"] == "loi" and trang[2]["markdown"] == ""
    assert done["so_trang_ocr"] == 2 and done["so_trang_loi"] == 1


# --------------------------------------------------------------------------- #
# Gỡ rào ```...``` model tự quấn quanh câu trả lời
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("vao, ra", [
    # Thật sự gặp trên Qwen3.6-35B: cả trang văn bản bị đóng gói thành khối mã.
    ("```markdown\n## CÔNG TY TPV\n\n| a | b |\n```", "## CÔNG TY TPV\n\n| a | b |"),
    ("```\n# Rào trơn\n```", "# Rào trơn"),
    # Không có rào bao ngoài thì giữ nguyên từng ký tự.
    ("## Không rào\n\nnội dung", "## Không rào\n\nnội dung"),
    # Khối mã THẬT của tài liệu nằm giữa bài: không được đụng vào.
    ("# Tài liệu\n\n```python\nx = 1\n```\n\nhết", "# Tài liệu\n\n```python\nx = 1\n```\n\nhết"),
    # Mở mà không đóng: không đủ hình dạng "cả câu trả lời trong một khối".
    ("```markdown\n# Cụt", "```markdown\n# Cụt"),
])
def test_go_rao_markdown(vao, ra):
    from app.rag.ocr import go_rao_markdown

    assert go_rao_markdown(vao) == ra
