import ollama
import json
import os
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer
from typing import List, Any

NAME_RERANKER_MODEL = "AITeamVN/Vietnamese_Reranker"
MODEL_CACHE_FOLDER = os.path.join(os.path.dirname(__file__), "model_cache")
os.makedirs(MODEL_CACHE_FOLDER, exist_ok = True)

class RerankerService:
    def __init__(self, model_name = NAME_RERANKER_MODEL, cache_folder = MODEL_CACHE_FOLDER):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            cache_dir = cache_folder,
            use_fast = False
        )
        self.model = AutoModelForSequenceClassification.from_pretrained(
            model_name,
            cache_dir = cache_folder
        ).to(self.device)
        self.model.eval()
    
    def rerank(self, query: str, documents: List[Any], top_k: int = 5) -> List[Any]:
        """
        Chấm điểm lại danh sách documents dựa trên query.

        Args:
            query (str): Câu hỏi người dùng.
            documents (List[Any]): Danh sách kết quả trả về từ Qdrant (ScoredPoint).
            top_k (int, optional): Số lượng kết quả tốt nhất muốn giữ lại.

        Returns:
            List[Any]: Danh sách documents đã được sắp xếp lại và cắt top_k.
        """
        if not documents:
            return []
        
        pairs = []
        valid_docs = []
        
        for doc in documents:
            content = doc.payload.get('content')
            if content:
                pairs.append([query, content])
                valid_docs.append(doc)
        
        if not pairs:
            return []
        
        with torch.no_grad():
            inputs = self.tokenizer(pairs, padding = True, truncation = True, return_tensors = 'pt', max_length = 2304).to(self.device)
            scores = self.model(**inputs, return_dict = True).logits.view(-1, ).float()
        
        if isinstance(scores, float):
            scores = [scores]
        
        results = []
        for doc, score in zip(valid_docs, scores):
            doc.score = score
            results.append(doc)
        
        results.sort(key = lambda x: x.score, reverse = True)
        return results[:top_k]