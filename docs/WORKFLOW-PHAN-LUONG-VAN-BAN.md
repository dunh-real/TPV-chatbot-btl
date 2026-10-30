# Workflow phan luong van ban den

## Hop dong bat buoc theo yeu cau

Day la contract chinh cua workflow. Backend phai gui cho AI:

- `FileUrl` hoac `ExtractedText` cua van ban den;
- danh sach phong ban tu `Dms_WorkDepartment`;
- danh muc linh vuc tu `AI_Categories`, trong do `FieldCode` la ma linh vuc hop le.

AI phai tra ve cho backend dung cac truong:

- `DocumentTypeCode`;
- `FieldCode`;
- `UrgencyLevel` (`Thuong`, `Khan`, `Hoa toc`, ...);
- `AiPrimaryUnitId` - ID don vi chu tri duoc map tu danh muc phong ban;
- `AiCoordinatingUnitsJson` - danh sach ID don vi phoi hop dang JSON;
- `AiReason` - ly do de xuat, phai co trich dan can cu trong van ban.

`AI_DocumentRoutings` la bang luu ket qua phan loai va phan luong nay. Cac truong
`routing_id`, `confidence`, `evidence`, `status` va lich su phe duyet la truong
van hanh bo sung, khong thay the sau truong bat buoc tren.

## 1. Muc tieu

Workflow nay tiep nhan mot van ban den, doc noi dung va dua ra de xuat phan luong cho can bo xu ly.

He thong can tra loi duoc cac cau hoi:

- Van ban thuoc loai nao?
- Van ban thuoc linh vuc nao?
- Muc do uu tien la Thuong, Khan, Hoa toc hay muc khac?
- Don vi nao lam chu tri?
- Don vi nao can phoi hop?
- Vi sao he thong dua ra de xuat nay?
- Ket qua da duoc can bo phe duyet hay chua?

AI chi tao **de xuat**. Quyet dinh phan luong chinh thuc van thuoc ve nguoi dung co quyen phe duyet.

## 2. Trang thai hien tai

Repository da co mot phan nen tang trong Workflow 2 - soat va phan tich tai lieu:

- parse `.docx`, `.pdf`, `.txt`, `.md`;
- OCR trang scan;
- lay danh muc phong ban tu `Dms_WorkDepartment`;
- dung LLM de phan loai loai tai lieu va chu de;
- phan ra nhiem vu va de xuat phong ban;
- validate ma phong ban theo danh muc hien co.

Phan chua co day du:

- workflow phan luong van ban den doc lap;
- bang `AI_DocumentRoutings`;
- danh muc `AI_Categories` trong code hien tai;
- cac truong don vi chu tri/phoi hop theo hop dong moi;
- `UrgencyLevel`;
- API phe duyet va lich su thay doi de xuat.

Tai lieu nay la thiet ke cho phan can hoan thien, co the tai su dung parser/OCR va cac ham phan tich dang co.

## 3. Kien truc de xuat

```text
Nguoi dung upload van ban den
            |
            v
Storage + FileId an toan
            |
            v
Parse / OCR / ExtractedText
            |
            v
Lay danh muc phong ban + danh muc linh vuc
            |
            v
Routing AI - model Qwen
            |
            v
Kiem tra schema va map code -> ID
            |
            v
Luu AI_DocumentRoutings
            |
            v
Tra ket qua AI_SUGGESTED
            |
            v
Can bo xem / sua / phe duyet
            |
            v
APPROVED -> chuyen xu ly chinh thuc
```

Workflow nay co the duoc dang ky la **Workflow 6 - Phan loai va phan luong van ban den**.

## 4. Luong xu ly chi tiet

### Buoc 1: Tiep nhan van ban

Client gui file den API. Backend khong truyen duong dan tuy y vao pipeline ma tao `file_id` thong qua storage service.

Input backend gui cho AI phai co **mot trong hai**:

- `FileUrl`: URL/path noi bo cua van ban den;
- `ExtractedText`: noi dung van ban da duoc OCR/trich xuat.

Backend co the nhan file upload va chuyen thanh `FileUrl` noi bo truoc khi goi
AI. AI khong tu truy cap duong dan tuy y; backend phai resolve va kiem tra file
nam trong vung duoc phep.

### Buoc 2: Kiem tra dinh dang

Cac dinh dang uu tien:

- `.pdf`;
- `.docx`;
- `.txt`;
- `.md`;
- anh `.png`, `.jpg`, `.jpeg`, `.tiff` neu can OCR truc tiep.

File khong ho tro tra ve loi ro rang, khong goi AI.

### Buoc 3: Trich xuat noi dung

```text
PDF digital -> lay text va cau truc truc tiep
PDF scan    -> phan loai trang -> render anh -> VLM OCR
DOCX        -> parser/MarkItDown/blocks
Anh         -> OCR VLM
TXT/MD      -> doc text
```

Ket qua trung gian:

```json
{
  "FileUrl": "internal://documents/cong-van-123.pdf",
  "ExtractedText": "...",
  "source_format": "pdf",
  "page_count": 4,
  "ocr_pages": [1, 2],
  "warnings": []
}
```

Neu khong doc duoc noi dung, workflow khong duoc tu doan. Ket qua phai la `NEEDS_REVIEW` hoac loi co kiem soat.

### Buoc 4: Tai danh muc phong ban

Backend lay danh sach phong ban theo tenant tu `Dms_WorkDepartment`.

Danh muc truyen cho AI can co ca ID va ma hien thi:

```json
[
  {
    "Id": 10,
    "Code": "P.KT",
    "DisplayName": "Phong Ke toan",
    "Description": "Tai chinh, ke toan, ngan sach"
  },
  {
    "Id": 20,
    "Code": "P.HCNS",
    "DisplayName": "Phong Hanh chinh Nhan su",
    "Description": "Van thu, hanh chinh, nhan su"
  }
]
```

AI duoc phep chon `Code`, nhung backend moi co quyen map va luu `Id` vao
`AiPrimaryUnitId`/`AiCoordinatingUnitsJson`.

Khong gui toan bo cot ERP khong can thiet cho AI. Chi gui cac truong phuc vu phan luong.

### Buoc 5: Tai danh muc linh vuc

Danh muc linh vuc lay tu `AI_Categories` hoac nguon cau hinh tuong duong:

```json
[
  {
    "FieldCode": "TAI_CHINH",
    "FieldName": "Tai chinh",
    "Description": "Thu chi, ngan sach, tai san tai chinh"
  },
  {
    "FieldCode": "NHAN_SU",
    "FieldName": "Nhan su",
    "Description": "Can bo, hop dong, tuyen dung, che do"
  }
]
```

AI khong duoc tu tao `FieldCode`. Neu khong khop linh vuc, tra `null` va chuyen can bo xem lai.

### Buoc 6: Goi Routing AI

Payload backend gui cho AI gom:

- `FileUrl` hoac `ExtractedText`;
- danh sach `Dms_WorkDepartment`;
- danh muc `AI_Categories` voi `FieldCode`;
- cac gia tri hop le cua `UrgencyLevel`.

Prompt phai yeu cau model:

- chi chon ma trong danh muc;
- khong tu tao phong ban;
- khong tu tao linh vuc;
- neu khong chac chan thi tra `null`;
- moi de xuat phai co ly do va trich dan tu noi dung;
- don vi chu tri va phoi hop khong duoc trung nhau;
- tra ve JSON dung schema, khong kem van ban ngoai JSON.

JSON AI tra ve phai co dang:

```json
{
  "DocumentTypeCode": "CONG_VAN_DEN",
  "FieldCode": "TAI_CHINH",
  "UrgencyLevel": "Khan",
  "AiPrimaryUnitId": 10,
  "AiCoordinatingUnitsJson": "[20]",
  "AiReason": "[P03] Van ban yeu cau tong hop so lieu tai chinh truoc ngay 30/09/2026."
}
```

Trong implementation an toan, model co the tra ve `Code` noi bo de backend map
sang ID. Tuy nhien response cuoi cung va ban ghi `AI_DocumentRoutings` bat buoc
phai co dung `AiPrimaryUnitId` va `AiCoordinatingUnitsJson`.

Model khong nhan SQL va khong tu truy cap database. Backend chuan bi danh muc da loc truoc khi goi model.

### Buoc 7: Map code AI sang ID database

AI tra ve ma phong ban:

```json
{
  "primary_unit_code": "P.KT",
  "coordinating_unit_codes": ["P.HCNS"]
}
```

Backend map deterministically:

```python
by_code = {
  item["Code"]: item
    for item in departments
  if item.get("IsDeleted") is not True
}

primary = by_code.get(result["primary_unit_code"])
coordinating = [
    by_code[code]
    for code in result["coordinating_unit_codes"]
    if code in by_code
]
```

Ket qua sau map:

```json
{
  "AiPrimaryUnitId": 10,
  "AiCoordinatingUnitsJson": "[20]"
}
```

Quy tac bat buoc:

1. Khong tim thay `primary_unit_code` thi khong tu chon phong ban gan nhat.
2. Khong tim thay ma phoi hop thi ghi warning va khong luu ID khong hop le.
3. Ma phong ban phai thuoc tenant hien tai.
4. Phong ban da xoa/khong hoat dong khong duoc chon.
5. Don vi chu tri khong duoc xuat hien trong danh sach phoi hop.
6. Code va ID phai duoc luu cung ket qua de audit khi danh muc thay doi.

### Buoc 8: Kiem tra ket qua

Backend validate output bang Pydantic va business rules:

```text
JSON hop le?
  -> khong: NEEDS_REVIEW

`FieldCode` co trong `AI_Categories`?
  -> khong: NEEDS_REVIEW

`AiPrimaryUnitId` co ton tai va dang hoat dong?
  -> khong: NEEDS_REVIEW

UrgencyLevel co nam trong enum?
  -> khong: NEEDS_REVIEW

Co trich dan trong AiReason?
  -> khong: canh bao/NEEDS_REVIEW

Tat ca hop le?
  -> AI_SUGGESTED
```

Can luu ca `confidence` va `warnings`. Confidence thap khong duoc tu dong phe duyet.

## 5. Hop dong input

API co the dung mot endpoint rieng:

```text
POST /api/document-routings/suggest
```

Request:

```json
{
  "FileUrl": "internal://documents/cong-van-123.pdf",
  "ExtractedText": "",
  "Departments": [],
  "Categories": []
}
```

Quy tac:

- can co mot trong `FileUrl`, `ExtractedText`;
- `Departments` la danh sach phong ban tu `Dms_WorkDepartment`;
- `Categories` la danh muc `AI_Categories`, moi muc phai co `FieldCode`;
- neu co ca `FileUrl` va `ExtractedText`, backend phai ghi ro nguon noi dung su dung.

## 6. Hop dong output

```json
{
  "RoutingId": "rt_01HXYZ",
  "document_id": "VB-123",
  "Status": "AI_SUGGESTED",
  "DocumentTypeCode": "CONG_VAN_DEN",
  "FieldCode": "TAI_CHINH",
  "UrgencyLevel": "Khan",
  "AiPrimaryUnitId": 10,
  "AiCoordinatingUnitsJson": "[20]",
  "AiReason": "[P03] Van ban yeu cau tong hop so lieu tai chinh truoc ngay 30/09/2026.",
  "AiEvidenceJson": [
    {
      "quote": "De nghi cac don vi gui bao cao tai chinh truoc ngay 30/09/2026.",
      "page": 1,
      "block_id": "P03"
    }
  ],
  "AiConfidence": 0.91,
  "Warnings": [],
  "CreatedAt": "2026-10-29T10:00:00Z"
}
```

`AiReason` la ly do de doc nhanh. `AiEvidenceJson` la can cu co the bam mo lai nguon. Hai truong nay khac nhau va nen cung luu.

## 7. Mo hinh bang `AI_DocumentRoutings`

Bang de xuat:

```text
AI_DocumentRoutings
-------------------
Id
TenantId
DocumentId
FileId
DocumentTypeCode
FieldCode
UrgencyLevel
AiPrimaryUnitId
AiCoordinatingUnitsJson
AiReason
AiEvidenceJson
AiConfidence
Status
ReviewedBy
ReviewedAt
ReviewNote
CreatedAt
UpdatedAt
```

Y nghia status:

| Status | Y nghia |
|---|---|
| `AI_SUGGESTED` | AI da tao de xuat, chua co nguoi duyet |
| `NEEDS_REVIEW` | Thieu ma, confidence thap, loi validation hoac khong doc duoc file |
| `APPROVED` | Can bo da phe duyet |
| `REJECTED` | De xuat bi tu choi |
| `SUPERSEDED` | Bi thay the boi mot de xuat moi |
| `ROUTED` | Da chuyen sang quy trinh xu ly chinh thuc |

Khong nen update de xuat cu khi chay lai AI. Moi lan chay lai nen tao ban ghi moi va danh dau ban cu la `SUPERSEDED`.

## 8. API phe duyet

```text
GET  /api/document-routings/{routing_id}
GET  /api/document-routings?document_id=VB-123
POST /api/document-routings/{routing_id}/approve
POST /api/document-routings/{routing_id}/reject
POST /api/document-routings/{routing_id}/rerun
```

Phe duyet:

```json
{
  "AiPrimaryUnitId": 10,
  "AiCoordinatingUnitsJson": "[20]",
  "UrgencyLevel": "Khan",
  "ReviewNote": "Da kiem tra, chuyen Phong Ke toan chu tri."
}
```

Sau khi `APPROVED`, backend phai kiem tra lai ID phong ban va tenant. Khong cho client phe duyet ID tu tenant khac.

## 9. Ket qua du doan mau

### Vi du 1: van ban tai chinh

Input:

```text
De nghi cac don vi tong hop so lieu thu chi, tai san va gui bao cao quy III truoc ngay 30/09/2026.
```

Du doan:

```text
Loai van ban: CONG_VAN_DEN
Linh vuc: TAI_CHINH
Muc do: KHAN
Don vi chu tri: Phong Ke toan
Don vi phoi hop: Phong Hanh chinh - Quan tri
Confidence: 0.91
```

### Vi du 2: van ban nhan su

Input:

```text
De nghi lap danh sach can bo tham gia tap huan ve che do bao hiem.
```

Du doan:

```text
Loai van ban: CONG_VAN_DEN
Linh vuc: NHAN_SU
Muc do: THUONG
Don vi chu tri: Phong Hanh chinh Nhan su
Don vi phoi hop: Don vi lien quan neu co danh sach
Confidence: 0.87
```

### Vi du 3: van ban khong ro

Input:

```text
Thong bao ve viec trien khai mot so noi dung moi.
```

Du doan an toan:

```text
Loai van ban: THONG_BAO
Linh vuc: null
Muc do: THUONG
Don vi chu tri: null
Don vi phoi hop: []
Status: NEEDS_REVIEW
Reason: Noi dung chua du de xac dinh don vi chu tri.
```

He thong khong duoc gan bua mot phong ban chi de tra ve ket qua day du.

## 10. Cac truong hop loi

| Truong hop | Xu ly |
|---|---|
| File khong ton tai | HTTP 404, khong goi AI |
| File khong doc duoc | `NEEDS_REVIEW`, ghi warning |
| OCR khong chac chan | Van cho de xuat nhung danh dau can kiem tra |
| AI tra JSON loi | Retry co gioi han, sau do `NEEDS_REVIEW` |
| AI tra unit code la | Khong map, khong tu doan |
| AI tra field code la | Khong luu field code, can xem lai |
| Confidence thap | `NEEDS_REVIEW` |
| Don vi khong cung tenant | Tu choi luu |
| Van ban trung da co de xuat | Tra de xuat hien tai hoac tao version moi theo `force_refresh` |
| Hai don vi trung priority | Khong tu chon, yeu cau xem lai policy |

## 11. Bao mat va quyen

Workflow phan luong phai kiem tra:

- quyen doc van ban den;
- tenant cua tai lieu;
- tenant cua danh muc phong ban;
- quyen xem danh muc `Dms_WorkDepartment`;
- quyen phe duyet phan luong;
- khong cho AI xem cac cot ERP khong lien quan;
- khong cho client tu gui `AiPrimaryUnitId` cua tenant khac;
- log `routing_id`, model, version prompt va version danh muc de audit.

Neu dang chay demo voi `X-User-Id`/`X-Tenant-Id`, chi nen dung trong mang tin cay. Production can auth that va dat `TRUST_IDENTITY_HEADERS=false`.

## 12. Cac phan tai su dung tu code hien tai

| Thanh phan hien tai | Cach tai su dung |
|---|---|
| `app.services.storage` | Luu va resolve file an toan |
| `app.documents.parser` | Doc block, heading va metadata |
| `app.rag.ocr` | OCR trang scan qua VLM |
| `app.documents.ocr_tai_lieu` | Lap ke hoach va ghep Markdown OCR |
| `app.db.erp_repository.ErpDonViRepository` | Lay danh muc phong ban |
| `app.agents.nodes.document.classify_node` | Goi y phan loai loai van ban/chude |
| `app.agents.nodes.document.tasks_node` | Giao nhiem vu va de xuat phong ban |
| `app.agents.references` | Luu trich dan va evidence |
| `app.core.context` | Tenant/principal cua request |
| `app.db.session` | CSDL ung dung va transaction |

Phan can viet them:

- schema/model `AI_DocumentRouting`;
- repository routing;
- `RoutingRequest`, `RoutingResponse`, `ApproveRequest`;
- routing service va validation map code -> ID;
- API router `/api/document-routings`;
- prompt/output schema rieng cho routing;
- migration/database setup;
- UI xem de xuat, sua va phe duyet;
- test map don vi, tenant, confidence, retry va audit.

## 13. Tieu chi nghiem thu

Workflow duoc xem la dat khi:

- upload duoc van ban PDF digital, PDF scan va DOCX;
- OCR chi chay khi can va ket qua co the truy ve trang/block;
- AI chi chon phong ban trong danh muc backend cung cap;
- ket qua AI code map dung sang ID database;
- khong luu ID khong ton tai, phong ban da xoa hoac sai tenant;
- don vi chu tri khac don vi phoi hop;
- van ban khong ro duoc danh dau `NEEDS_REVIEW`;
- `AiReason` va `AiEvidenceJson` co can cu tu van ban;
- can bo co the sua va phe duyet;
- chay lai AI tao version moi, khong lam mat lich su;
- co test cho ket qua dung, ma sai, confidence thap, OCR loi va tenant sai;
- API tra OpenAPI schema ro rang;
- moi con so/thong tin suy ra deu co nguon hoac warning.

## 14. Tom tat

```text
Van ban den
→ OCR/trich text
→ AI doc noi dung
→ AI chon loai, linh vuc, muc do khan, ma don vi
→ Backend map ma don vi sang ID that
→ Validate tenant, danh muc, confidence, evidence
→ Luu AI_DocumentRoutings
→ Can bo sua/phe duyet
→ Chuyen xu ly chinh thuc
```

Day la workflow phan luong doc lap, nhung tai su dung phan parser/OCR va mot so node dang co cua Workflow 2. AI khong duoc tu quyet dinh vuot qua danh muc; backend moi la noi quyet dinh ket qua co hop le de luu va phe duyet hay khong.
