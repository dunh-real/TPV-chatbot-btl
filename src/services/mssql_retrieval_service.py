import pyodbc

conn_str = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=localhost\SQLEXPRESS;"
    "Database=SIPM-103-Staging;"
    "Trusted_Connection=yes;"
)

try:
    connection = pyodbc.connect(conn_str)
    cursor = connection.cursor()
    
    query = "SELECT * FROM SIPM_Asset"
    cursor.execute(query)
    
    rows = cursor.fetchall()
    for row in rows:
        print(row)

except pyodbc.Error as e:
    print(f"Database error: {e}")

finally:
    if 'connection' in locals():
        connection.close()


class MSSQLRetriever:
    def __init__(self):
        self.conn_str = (
            "Driver={ODBC Driver 17 for SQL Server};"
            "Server=localhost\SQLExpress;"
            "Database=SIPM-103-Staging;"
            "Trusted_Connection=yes;"
        )
        self.connection = pyodbc.connect(self.conn_str)
        self.cursor = self.connection.cursor()
    
    def get_rows(self, query: str):
        self.cursor.execute(query)
        rows = self.cursor.fetchall()
        return rows
    
    def close_connection(self):
        if 'connection' in locals():
            self.connection.close()