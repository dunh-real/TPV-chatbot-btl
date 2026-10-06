"""BM25 encoder: tokenize tiếng Việt và sinh sparse vector."""

from __future__ import annotations

from app.rag.embedding import BM25Encoder, _hash_term


def test_tokenize_bo_stopword_va_sinh_bigram():
    encoder = BM25Encoder()
    tokens = encoder.tokenize("Hoá đơn điện tử của công ty là gì?")
    assert "là" not in tokens and "của" not in tokens      # stopword bị loại
    assert "hoá_đơn" in tokens and "điện_tử" in tokens     # bigram được giữ


def test_hash_on_dinh_giua_cac_lan_chay():
    # Không dùng hash() của Python vì phụ thuộc PYTHONHASHSEED.
    assert _hash_term("hoá_đơn") == _hash_term("hoá_đơn")
    assert 0 <= _hash_term("bất kỳ") <= 0x7FFFFFFF


def test_document_va_query_dung_dinh_dang_qdrant():
    encoder = BM25Encoder()
    doc = encoder.encode_document("Hoá đơn điện tử phải có chữ ký số của người bán.")
    query = encoder.encode_query("chữ ký số hoá đơn")

    assert len(doc.indices) == len(doc.values)
    assert doc.indices == sorted(doc.indices)          # Qdrant cần indices tăng dần
    assert all(v > 0 for v in doc.values)
    assert set(query.values) == {1.0}                  # query chỉ đánh dấu term, IDF do server
    assert set(query.indices) & set(doc.indices)       # có giao nhau


def test_tf_bao_hoa_theo_cong_thuc_bm25():
    encoder = BM25Encoder()
    once = encoder.encode_document("thuế").as_dict()
    many = encoder.encode_document("thuế " * 10).as_dict()
    term = _hash_term("thuế")
    # Lặp 10 lần không làm điểm tăng 10 lần (saturation của BM25).
    assert many[term] < once[term] * 10


def test_van_ban_rong_tra_ve_vector_rong():
    encoder = BM25Encoder()
    assert len(encoder.encode_document("   ")) == 0
    assert len(encoder.encode_query("!!!")) == 0
