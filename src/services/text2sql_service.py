import json
import urllib.request

def generate_sql(prompt: str, schema: str) -> str:
    url = "http://localhost:11434/api/generate"
    
    # constructing a structured system prompt to force pure sql output
    system_prompt = (
        "You are an expert Text-to-SQL assistant. Given the following database schema,"
        "translate the user's natural language request into a valid SQL query."
        "Provide ONLY the raw SQL code. Do not wrap it in markdown code blocks like ```sql, "
        "and do not write any explanations."
    )
    
    full_prompt = f"{system_prompt}\n\nSchema:\n{schema}\n\nRequest: {prompt}\n\nSQL:"
    
    data = {
        "model": "qwen3:latest",
        "prompt": full_prompt,
        "stream": False,
        "options": {
            "temperature": 0.0 # zero temperature forces deterministic, precise code generation
        }
    }
    
    req = urllib.request.Request(
        url,
        data = json.dumps(data).encode('utf-8'),
        headers = {"Content-Type": "application/json"}
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            res_body = json.loads(response.read().decode("utf-8"))
            return res_body.get("response", "").strip()
    except Exception as e:
        return f"Error connecting to Ollama: {str(e)}"

# define database schema
database_schema = """

"""
