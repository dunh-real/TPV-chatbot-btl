import os
from typing import List, Dict, Any

try:
    import pyodbc
except Exception:  # pragma: no cover - optional dependency for local runtime
    pyodbc = None

from src.services.text2sql_service import TextToSQLService


class MSSQLRetrievalService:
    def __init__(self):
        self.text_to_sql = TextToSQLService()
        self.conn_str = self._build_connection_string()
        self.connection = self._connect() if pyodbc is not None else None
        self.cursor = self.connection.cursor() if self.connection else None

    def _build_connection_string(self) -> str:
        driver = os.getenv("MSSQL_DRIVER", "ODBC Driver 17 for SQL Server")
        server = os.getenv("MSSQL_SERVER", "localhost\\SQLExpress")
        database = os.getenv("MSSQL_DATABASE", "SIPM-103-Staging")
        username = os.getenv("MSSQL_USERNAME")
        password = os.getenv("MSSQL_PASSWORD")

        if username and password:
            return (
                f"Driver={{{driver}}};"
                f"Server={server};"
                f"Database={database};"
                f"UID={username};"
                f"PWD={password};"
                "Encrypt=yes;"
                "TrustServerCertificate=yes;"
                "Connection Timeout=3;"
            )

        return (
            f"Driver={{{driver}}};"
            f"Server={server};"
            f"Database={database};"
            "Trusted_Connection=yes;"
            "TrustServerCertificate=yes;"
            "Connection Timeout=3;"
        )

    def _connect(self):
        if pyodbc is None:
            return None

        try:
            return pyodbc.connect(self.conn_str, timeout = 3)
        except Exception:
            return None

    def get_rows(self, query: str):
        if not query or not self.cursor or pyodbc is None:
            return []

        try:
            self.cursor.execute(query)
            return self.cursor.fetchall()
        except pyodbc.Error:
            return []

    def retrieve_context(self, user_query: str) -> List[Dict[str, Any]]:
        sql_query = self.text_to_sql.get_response(user_query)
        if not sql_query:
            return []

        rows = self.get_rows(sql_query)
        if not rows:
            return []

        if self.cursor and self.cursor.description:
            columns = [col[0] for col in self.cursor.description]
            context_items = []
            for row in rows:
                row_text = "; ".join(
                    f"{col}: {value}"
                    for col, value in zip(columns, row)
                    if value is not None
                )
                if row_text:
                    context_items.append({
                        "content": row_text,
                        "source_file": "mssql_database_result",
                    })
            return context_items

        return [{"content": str(rows[0]), "source_file": "mssql_database_result"}]

    def close_connection(self):
        if self.connection:
            self.connection.close()
            self.connection = None
            self.cursor = None