import ollama
import json
import torch
import os
from langchain_core.messages import BaseMessage, AIMessage, SystemMessage, HumanMessage
from typing import List, Any
from transformers import AutoModelForSequenceClassification, AutoTokenizer

NAME_LLM_MODEL = "qwen3:latest"
NAME_RERANKER_MODEL = "AITeamVN/Vietnamese_Reranker"
MODEL_CACHE_FOLDER = os.path.join(os.path.dirname(__file__), "models_cache")
os.makedirs(MODEL_CACHE_FOLDER, exist_ok = True)

# reranking context result (top 5) -> prompt for system -> llm -> final answer (json)

# llm service
class OllamaChatLLM:
    def __init__(self, model_name: str = NAME_LLM_MODEL):
        self.model_name = model_name
        self.options = {
            "temperature": 0.2,
            "num_ctx": 8192, # context window
        }
    
    def invoke(self, messages: List[BaseMessage]):
        payload = []
        for m in messages:
            role = 'user'
            if isinstance(m, SystemMessage): role = 'system'
            elif isinstance(m, AIMessage): role = 'assistant'
            
            payload.append({
                "role": role,
                "content": m.content
            })
        
        try:
            response = ollama.chat(
                model = self.model_name,
                messages = payload,
                format = 'json',
                options = self.options
            )
            
            text_result = response['message']['content']
            
            parsed_json = json.loads(text_result)
            final_answer = parsed_json.get("answer", "")
            citation = parsed_json.get("citation", "")
            
            return AIMessage(content = final_answer), citation
        
        except Exception as e:
            return AIMessage(content = f"Connection Error: Ollama - {str(e)}"), ""
    