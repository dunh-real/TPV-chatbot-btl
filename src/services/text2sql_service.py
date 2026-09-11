from ollama import chat
import time

start_time = time.perf_counter()
response = chat(
    model = 'debopam/Text-to-SQL__Qwen2.5-Coder-3B-Finetuned',
    messages = [{
        'role': 'user', 'content': 'Return just the SQL command to retrieve the column named employee_email in table SIPM_employee, filter the email with the starting letter of "a". DONT provide any extra explanation.'
    }]
)

print(response.message.content)
end_time = time.perf_counter() - start_time
print(f"Executed time: {end_time:.4f} seconds.")