import os
import torch
from sentence_transformers import SentenceTransformer
from transformers import AutoModelForMaskedLM, AutoTokenizer

# Model Name
DENSE_MODEL_NAME = "AITeamVN/Vietnamese_Embedding"
SPARSE_MODEL_NAME = "prithivida/Splade_PP_en_v1"

# Save Model
MODEL_CACHE_FOLDER = os.path.join(os.path.dirname(__file__), "models_cache")
os.makedirs(MODEL_CACHE_FOLDER, exist_ok = True)

# Create embedding dense-vector
class LocalDenseEmbedding:
    def __init__(self, model_name = DENSE_MODEL_NAME, cache_folder = MODEL_CACHE_FOLDER):
        self.model_name = model_name
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        
        self.model = SentenceTransformer(
            self.model_name,
            device = self.device,
            cache_folder = cache_folder
        )
    
    # get model name
    def get_model_name(self):
        return self.model_name
    
    # get model
    def get_model(self):
        return self.model
    
    # processing query input
    def get_dense_vector(self, query: str):
        embedding = self.model.encode(query, normalize_embeddings = True)
        return embedding.tolist()
    
    # processing enterprise docs
    def embed(self, texts: list[str]):
        embeddings = self.model.encode(texts, batch_size = 32, convert_to_numpy = True, show_progress_bar = True)
        return embeddings.tolist()


# Create embedding sparse-vector
class LocalSparseEmbedding:
    def __init__(self, model_name = SPARSE_MODEL_NAME, cache_folder = MODEL_CACHE_FOLDER):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.tokenizer = AutoTokenizer.from_pretrained(model_name, cache_dir = cache_folder)
        self.model = AutoModelForMaskedLM.from_pretrained(model_name, cache_dir = cache_folder, use_safetensors = True)
        self.model.to(self.device)
        self.model.eval()
    
    # processing query input
    def get_sparse_vector(self, query: str):
        tokens = self.tokenizer(query, return_tensors = "pt").to(self.device)
        
        with torch.no_grad():
            output = self.model(**tokens)
            
        logits = output.logits
        weights = torch.max(torch.log(1 + torch.relu(logits)), dim = 1).values.squeeze()
        cols = weights.nonzero().squeeze().cpu().tolist()
        weights = weights[cols].cpu().tolist()
        
        if isinstance(cols, int): cols = [cols]
        if isinstance(weights, float): weights = [weights]
        
        return {
            "indices": cols,
            "values": weights
        }
    
    # processing enterprise docs
    def embed(self, texts: list[str], batch_size = 32):
        results = []
        
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i : i + batch_size]
            inputs = self.tokenizer(
                batch_texts,
                return_tensors = "pt",
                padding = True,
                truncation = True,
                max_length = 512
            ).to(self.device)
            
            with torch.no_grad():
                outputs = self.model(**inputs)
                logits = outputs.logits
                
                relu_logits = torch.relu(logits)
                log_logits = torch.log(1 + relu_logits)
                masked_logits = log_logits * inputs.attention_mask.unsqueeze(-1)
                sparse_vec_batch, _ = torch.max(masked_logits, dim = 1)
            
            for vec in sparse_vec_batch:
                indices = vec.nonzero().squeeze().cpu().tolist()
                values = vec[indices].cpu().tolist()
                
                if isinstance(indices, int): indices = [indices]
                if isinstance(values, float): values = [values]
                
                results.append({"indices": indices, "values": values})
        
        return results