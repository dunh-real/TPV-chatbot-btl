"""Dựng lại khoảng trắng mà khâu trích xuất PDF đã nuốt mất.

PDF xuất từ LaTeX (bài báo, luận văn, tài liệu kỹ thuật) hay đặt chữ có dấu vào
một font khác với chữ không dấu. Bộ trích xuất thấy hai span font khác nhau nên
không chèn khoảng trắng giữa chúng, và chữ dính liền nhau ngay tại chỗ có dấu:

    "mỗi bộ học i được gán điểm Score"  ->  "mỗi bộhọc i được gán điểm Score"
    "entropy trọng số nhãn"             ->  "entropy trọng sốnhãn"
    "Trả về mô hình toàn cục"           ->  "Trảvềmô hình toàn cục"

Đây KHÔNG phải lỗi hiển thị. Chữ dính là chữ đi vào chunk, vào embedding, vào
BM25 và vào ngữ cảnh gửi cho model: "bộhọc" không khớp với ai hỏi "bộ học", và
model đọc ngữ cảnh gãy thì viết ra câu trả lời gãy y như vậy.

Tách lại được vì tiếng Việt viết rời từng âm tiết, và âm tiết có cấu trúc đóng:
PHỤ ÂM ĐẦU + VẦN, với bộ vần hữu hạn (`VAN`). Một chuỗi chữ dính chỉ có rất ít
cách cắt thành toàn âm tiết hợp lệ - thường là đúng một cách.

Ba luật ngữ âm ở `la_am_tiet` gạt phần lớn cách cắt sai, nên chúng đáng giá hơn
vẻ ngoài vụn vặt của chúng:

    vần tắc chỉ mang thanh sắc/nặng   "khảthi" không cắt được thành "khảt|hi"
    vần "iê" phải có phụ âm đầu       "kếtiếp" không cắt được thành "kết|iếp"
    mỗi âm tiết đúng một dấu thanh    chặn mọi mảnh ghép từ hai âm tiết

Còn lại thì `tach_am_tiet` chọn theo thứ tự: ít mảnh nhất (từ viết đúng luôn
thắng vì nó là MỘT mảnh), ít mảnh lạ nhất so với vốn từ của chính tài liệu, rồi
ranh giới sớm nhất - phụ âm dồn về đầu âm tiết sau, đúng như tiếng Việt phát âm.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

__all__ = ["la_am_tiet", "sua_dinh_chu", "tach_am_tiet", "von_tu_cua"]

HUYEN, SAC, NGA, HOI, NANG = "̀", "́", "̃", "̉", "̣"
DAU_THANH = frozenset((HUYEN, SAC, NGA, HOI, NANG))

# Thử từ dài tới ngắn: "ngh" phải được xét trước "ng", "ng" trước "n". Chuỗi rỗng
# ở cuối là âm tiết mở đầu bằng nguyên âm ("ăn", "ở", "uống").
PHU_AM_DAU: tuple[str, ...] = (
    "ngh", "ng", "nh", "ch", "gh", "gi", "kh", "ph", "th", "tr", "qu",
    "b", "c", "d", "đ", "g", "h", "k", "l", "m", "n", "p", "r", "s", "t", "v", "x",
    "",
)

# Vần viết ở dạng KHÔNG THANH; dấu thanh được gỡ ra trước khi tra.
VAN: frozenset[str] = frozenset("""
a ac ach ai am an ang anh ao ap at au ay
ăc ăm ăn ăng ăp ăt
âc âm ân âng âp ât âu ây
e ec em en eng eo ep et
ê êch êm ên ênh êp êt êu
i ia ich iêc iêm iên iêng iêp iêt iêu im in inh ip it iu
o oa oac oach oai oam oan oang oanh oao oap oat oay
oăc oăm oăn oăng oăp oăt
oc oe oem oen oeo oet oi om on ong ooc oong op ot
ô ôc ôi ôm ôn ông ôp ôt
ơ ơi ơm ơn ơp ơt
u ua uc uê uêch uênh ui um un ung
uôc uôi uôm uôn uông uôt up ut uy uya uych uyn uynh uyp uyt uyu
uân uâng uât uây uyên uyêt uơ
uai uao uay uan uang uanh uat uăc uăn uăng uăt ue uen ueo uet
ư ưa ưc ưi ưn ưng ươc ươi ươm ươn ương ươp ươt ươu ưt ưu
y yêm yên yêng yêt yêu ynh
""".split())

# Vần đóng bằng phụ âm tắc. Tiếng Việt chỉ ghép chúng với thanh sắc hoặc nặng -
# nên "khảt", "trảch", "đểt" là những mảnh KHÔNG THỂ có, dù ghép đúng khuôn vần.
VAN_TAC: tuple[str, ...] = ("p", "t", "c", "ch")

# Vần "iê..." không đứng đầu âm tiết được: không có phụ âm đầu thì chính tả viết
# "yê..." ("yên", "yêu"). Nhờ luật này "kếtiếp" không cắt thành "kết" + "iếp".
VAN_CAN_PHU_AM: frozenset[str] = frozenset(v for v in VAN if v.startswith("iê"))

# Chữ CHỈ tiếng Việt mới có. Cổng vào của cả module: token không mang dấu tiếng
# Việt thì không đụng tới, nên "Batman", "boosting", "FedAvg" luôn an toàn.
_CHU_RIENG = re.compile(r"[ăâêôơưđ]", re.IGNORECASE)

# Một cụm chữ cái liền nhau, kể cả chữ có dấu. `\w` dính cả chữ số và gạch dưới
# nên không dùng được: "H_i" và "top5" phải giữ nguyên.
_CUM_CHU = re.compile(r"[^\W\d_]+", re.UNICODE)

# Dài dưới mức này thì không đủ chỗ cho hai âm tiết.
DAI_TOI_THIEU = 3


def _tach_thanh(tu: str) -> tuple[str, str]:
    """(dạng không thanh, các dấu thanh đã gỡ). Giữ nguyên ă â ê ô ơ ư đ."""
    phan_ra = unicodedata.normalize("NFD", tu)
    thanh = "".join(c for c in phan_ra if c in DAU_THANH)
    goc = "".join(c for c in phan_ra if c not in DAU_THANH)
    return unicodedata.normalize("NFC", goc), thanh


def co_dau_viet(tu: str) -> bool:
    """Có ít nhất một chữ chỉ tiếng Việt mới có, hoặc một dấu thanh."""
    if _CHU_RIENG.search(tu):
        return True
    return any(c in DAU_THANH for c in unicodedata.normalize("NFD", tu))


@lru_cache(maxsize=200_000)
def la_am_tiet(tu: str) -> bool:
    """Chuỗi này có phải MỘT âm tiết tiếng Việt viết đúng chính tả không."""
    goc, thanh = _tach_thanh(tu.lower())
    # Hai dấu thanh trong một cụm nghĩa là đã dính từ hai âm tiết.
    if len(thanh) > 1:
        return False

    for dau in PHU_AM_DAU:
        if not goc.startswith(dau):
            continue
        van = goc[len(dau):]
        if van not in VAN:
            continue
        if not dau and van in VAN_CAN_PHU_AM:
            continue
        # Vần tắc: bắt buộc có thanh, và chỉ được sắc hoặc nặng.
        if van.endswith(VAN_TAC) and thanh not in (SAC, NANG):
            continue
        return True
    return False


def _manh_dung_duoc(manh: str) -> bool:
    """Một mảnh chỉ được nhận nếu nó là một âm tiết tiếng Việt viết đúng.

    Mảnh một chữ phải mang dấu ("ở", "ý"); không có luật này thì mọi nguyên âm
    trần đều thành mảnh hợp lệ và mở ra hàng loạt cách cắt vô nghĩa.

    Chữ nước ngoài không được nhận thành một mảnh riêng, kể cả khi nó rõ ràng là
    một từ: muốn cắt "AdaBoostduytrìmộtphân" thì phải biết tiếng Anh dừng ở đâu,
    mà "AdaBoostduy" cũng là một cụm chữ Latin hợp lệ y như "AdaBoost". Cụm lai
    như vậy để nguyên - dính chữ thì người đọc vẫn đoán ra, còn cắt bậy thì đẻ
    ra một từ không có thật.
    """
    if len(manh) == 1:
        return co_dau_viet(manh)
    return la_am_tiet(manh)


def tach_am_tiet(tu: str, von_tu: frozenset[str] | set[str] = frozenset()) -> list[str]:
    """Cắt một cụm chữ dính thành các âm tiết; trả về `[tu]` nếu không cắt được.

    `von_tu` là các âm tiết đứng RỜI ở đâu đó trong cùng tài liệu, và nó gánh hai
    việc. Một: mảnh không dấu bắt buộc phải nằm trong đó (xem `_manh_dung_duoc`).
    Hai: khi hai cách cắt cùng số mảnh, cách nào dùng toàn chữ tài liệu đã dùng
    thì thắng - đó là chỗ phân biệt "tín hiệu" với "tí nhiệu", điều mà luật ngữ
    âm không làm được. Gọi mà không truyền vốn từ thì chỉ cắt được chữ có dấu.
    """
    n = len(tu)
    # tot[i] = (số mảnh, số mảnh lạ, các mốc cắt) cho phần tu[i:], hoặc None.
    tot: dict[int, tuple[int, int, tuple[int, ...]] | None] = {n: (0, 0, ())}
    for i in range(n - 1, -1, -1):
        tot[i] = None
        for j in range(i + 1, n + 1):
            manh = tu[i:j]
            if not _manh_dung_duoc(manh):
                continue
            if (sau := tot[j]) is None:
                continue
            la = 0 if manh.lower() in von_tu else 1
            ung_vien = (sau[0] + 1, sau[1] + la, (j, *sau[2]))
            if tot[i] is None or ung_vien < tot[i]:  # type: ignore[operator]
                tot[i] = ung_vien

    if (ket := tot[0]) is None:
        return [tu]
    moc = (0, *ket[2])
    manh = [tu[a:b] for a, b in zip(moc, moc[1:])]
    return manh if _dang_tin(manh, von_tu) else [tu]


def _dang_tin(manh: list[str], von_tu: frozenset[str] | set[str]) -> bool:
    """Cách cắt này có phải chữ thật, hay là một từ nước ngoài vừa khít khuôn vần?

    Nhiều từ tiếng Anh vỡ ra đúng khuôn âm tiết tiếng Việt: "entropy" cắt được
    thành "en" + "tro" + "py", cả ba đều hợp lệ về ngữ âm. Dấu hiệu phân biệt là
    chúng đi thành CHUỖI LIỀN NHAU - mảnh nào cũng không dấu, và tài liệu chưa
    từng dùng chữ nào trong số đó.

    Một mảnh không dấu lẻ loi thì ngược lại, gần như luôn là chữ Việt thật: "khả
    thi", "để thu", "thể mang" đều có vế sau không dấu. Vì vậy chỉ chặn khi có
    từ hai mảnh đáng ngờ đứng LIỀN nhau.
    """
    lien = 0
    for m in manh:
        if co_dau_viet(m) or m.lower() in von_tu:
            lien = 0
            continue
        lien += 1
        if lien >= 2:
            return False
    return True


def von_tu_cua(text: str) -> frozenset[str]:
    """Các âm tiết đứng RỜI trong văn bản - vốn từ để chấm điểm cách cắt."""
    return frozenset(
        cum.lower() for cum in _CUM_CHU.findall(text) if la_am_tiet(cum)
    )


def _cat_cho_viet_hoa(cum: str) -> list[str]:
    """Cắt tại chỗ nối chữ thường với chữ HOA: "sởFL", "từAdaBoost", "nghệThông".

    Tiếng Việt không viết hoa giữa từ, nên đây luôn là hai từ bị dính - một ranh
    giới chắc chắn hơn mọi suy đoán ngữ âm. Không viết được bằng lớp ký tự
    `[a-zà-ỹ]`: chữ hoa và chữ thường tiếng Việt nằm xen kẽ nhau trong bảng mã
    (U+1EA0 trở đi), nên một khoảng mã bất kỳ đều vơ cả hai.

    Chỉ áp cho ranh giới VIỆT sang hoa. "AdaBoost" tự nó viết hoa giữa từ, nên
    "từAdaBoost" phải ra "từ" + "AdaBoost", không phải "từ" + "Ada" + "Boost".
    """
    ra: list[str] = []
    dau = 0
    for i in range(1, len(cum)):
        # Chỉ cắt khi phần đang dở là chữ VIỆT. Chữ hoa giữa một cụm Latin thuần
        # là lối viết tên riêng ("AdaBoost", "FedAvg"), không phải hai từ dính.
        if cum[i].isupper() and cum[i - 1].islower() and co_dau_viet(cum[dau:i]):
            ra.append(cum[dau:i])
            dau = i
    ra.append(cum[dau:])
    return ra


def _sua_cum(cum: str, von_tu: frozenset[str]) -> str:
    if len(cum) < DAI_TOI_THIEU or not co_dau_viet(cum) or la_am_tiet(cum):
        return cum
    # Cắt theo chữ hoa TRƯỚC rồi mới dò âm tiết, để "từAdaBoost" không phải nhờ
    # vào việc "AdaBoost" có tách được thành âm tiết hay không.
    ra: list[str] = []
    for phan_nho in _cat_cho_viet_hoa(cum):
        ra.extend(tach_am_tiet(phan_nho, von_tu) if len(phan_nho) >= DAI_TOI_THIEU
                  else [phan_nho])
    return " ".join(ra)


def sua_dinh_chu(text: str, von_tu: frozenset[str] | None = None) -> str:
    """Chèn lại khoảng trắng vào những cụm chữ tiếng Việt bị dính liền.

    `von_tu` nên lấy từ TOÀN tài liệu (`von_tu_cua`) chứ không riêng đoạn đang
    sửa: chữ bị dính ở trang này thường đứng rời ở trang khác, và đó chính là
    bằng chứng cần để chọn đúng chỗ cắt.
    """
    if not text:
        return text
    von = von_tu if von_tu is not None else von_tu_cua(text)
    return _CUM_CHU.sub(lambda m: _sua_cum(m.group(0), von), text)
