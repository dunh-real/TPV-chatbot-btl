import re
from typing import Optional

try:
    import ollama
except Exception:  # pragma: no cover - optional dependency for local runtime
    ollama = None


class TextToSQLService:
    def __init__(self, model_name: str = "qwen3:latest"):
        self.model_name = model_name
        self.system_prompt = """
        Bạn là chuyên gia SQL Server T-SQL. Chuyển câu hỏi tiếng Việt hoặc tiếng Anh thành một câu lệnh SELECT hợp lệ.
        Chỉ được sử dụng các bảng/cột trong schema sau:
        - dbo.AbpUsers: Id, UserName, EmailAddress, Name, IsActive
        - dbo.Dms_Employee: Id, Email, WorkDepartmentId, WorkPositionId, Name
        - dbo.Dms_InvestmentProject: Id, Code, Name, EnterpriseProfileId, IndustrialParkId, LocationDetail, ExpectedStartDate, ExpectedEndDate, ActualStartDate, ActualEndDate
        - dbo.SIPM_EnterpriseProfile: Id, Name, ShortName, TaxCode, LegalRepresentative, ContactPerson, ContactPhone, ContactEmail, Address, IndustrialParkId
        - dbo.SIPM_IndustrialPark: Id, Name, TotalArea, Address, OperationStatus
        - dbo.SIPM_Procedure: Id, Name, Description, ReceivingUnitName, ProcessingTimeInDays, FeeAmount
        - dbo.SIPM_Procedure_Document: Id, ProcedureId, DocumentName, Description, IsRequired
        - dbo.SIPM_Procedure_Step: Id, ProcedureId, StepNo, StepName, Description, ResponsibleUnitName, ExpectedProcessingDays
        - dbo.SIPM_Asset: Id, Code, Name, IndustrialParkId, Description, Latitude, Longitude, InstallationDate, LastMaintenanceDate
        - dbo.SIPM_SafetyAssets: Id, Code, Name, DeviceType, Latitude, Longitude

        Quy tắc bắt buộc:
        1. Chỉ trả về một câu SQL SELECT hợp lệ, không giải thích, không markdown, không có văn bản phụ.
        2. Dùng alias cho bảng, ví dụ: FROM dbo.SIPM_Procedure p.
        3. Dùng TOP thay vì LIMIT.
        4. Dùng N'' cho chuỗi tiếng Việt.
        5. Không sinh ra INSERT, UPDATE, DELETE, DROP.
        6. Nếu không chắc chắc, ưu tiên trả về một câu SELECT tối giản có thể chạy được.
        """

    def _clean_sql(self, text: Optional[str]) -> str:
        if not text:
            return ""

        cleaned = text.strip()
        cleaned = cleaned.replace("```sql", "").replace("```", "").strip()
        cleaned = cleaned.replace("`", "")
        cleaned = cleaned.replace("SQL:", "", 1).strip()
        cleaned = re.sub(r"^\s*SELECT\s*", "SELECT ", cleaned, flags = re.IGNORECASE)

        if cleaned.upper().startswith("SELECT") is False:
            return ""

        if not cleaned.endswith(";"):
            cleaned += ";"

        return cleaned

    def get_response(self, query: str) -> str:
        if not query or not query.strip():
            return ""

        if ollama is None:
            return ""

        try:
            response = ollama.chat(
                model = self.model_name,
                messages = [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": query},
                ],
                options = {"temperature": 0.0},
            )
            content = response.get("message", {}).get("content", "")
            return self._clean_sql(content)
        except Exception:
            return ""
