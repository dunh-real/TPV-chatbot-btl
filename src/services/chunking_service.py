import uuid
import re
from typing import List, Dict
from langchain_experimental.text_splitter import SemanticChunker
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter
from transformers import AutoTokenizer
from src.services.embedding_service import LocalDenseEmbedding

# CONFIG TOKEN BASED
MIN_TOKENS = 200
IDEAL_TOKENS = 700
MAX_TOKENS = 1200
HARD_CAP = 1500

embedding_client = LocalDenseEmbedding()

class ChunkingService:
    def __init__(self):
        
        # load model embedding
        self.embeddings = embedding_client.get_model()
        
        # load tokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(embedding_client.get_model_name())
        
        # markdown splitter
        self.header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on = [
                ("#", "H1"),
                ("##", "H2"),
            ],
            strip_headers = False
        )
        
        # semantic splitter
        self.semantic_splitter = SemanticChunker(
            self.embeddings,
            breakpoint_threshold_type = "gradient"
        )
        
        # structure splitter
        self.structure_splitter = RecursiveCharacterTextSplitter(
            chunk_size = 2000,
            chunk_overlap = 0,
            separators = [
                "\n\n", "\n",
                "\n1. ", "\n2. ", "\n3. ", "\n4. ", "\n5. ",
                "\na) ", "\nb) ", "\nc) ", "\nd) ",
                ". ",
            ]
        )
        
        # fallback splitter
        self.fallback_splitter = RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
            tokenizer = self.tokenizer,
            chunk_size = HARD_CAP,
            chunk_overlap = 100
        )
    
    def _count_tokens(self, text: str) -> int:
        return len(self.tokenizer.encode(text))
    
    def _preprocess_tables(self, text: str) -> str:
        """
        Tìm bảng Markdown và cô lập bằng \n\n để tránh bị cắt đôi
        """
        table_block_pattern = r'((?:^\|.*\|$\n?)+)'
        
        def isolate_table(match):
            table_content = match.group(1).strip()
            return f"\n\n{table_content}\n\n"
        
        return re.sub(table_block_pattern, isolate_table, text, flags = re.MULTILINE)
    
    def _smart_merge_sections(self, splits):
        """
        Logic merge:
        - Ưu tiên 1: Nếu < MIN_TOKENS -> BẮT BUỘC GỘP.
        - Ưu tiên 2: Nếu < IDEAL_TOKENS -> Gộp để tối ưu context.
        """
        if not splits: return []
        
        merged = []
        current_doc = splits[0]
        
        for next_doc in splits[1:]:
            curr_tokens = self._count_tokens(current_doc.page_content)
            next_tokens = self._count_tokens(next_doc.page_content)
            
            # Điều kiện kiểm tra topic (H1): Không gộp các section khác header (H1) để tránh hallucination
            curr_h1 = current_doc.metadata.get("H1")
            next_h1 = next_doc.metadata.get("H1")
            same_topic = (curr_h1 == next_h1)
            
            # Điều kiện an toàn kích thước
            total_size = curr_tokens + next_tokens
            is_safe_size = total_size < HARD_CAP
            
            should_merge = False
            
            if same_topic and is_safe_size:
                # 1. force to merge if the number of tokens is low (<200)
                if curr_tokens < MIN_TOKENS:
                    should_merge = True
                # 2. merge if the total of tokens if still good/acceptable (<700)
                elif total_size < IDEAL_TOKENS:
                    should_merge = True
            
            if should_merge:
                current_doc.page_content += "\n\n" + next_doc.page_content
            else:
                merged.append(current_doc)
                current_doc = next_doc
        
        merged.append(current_doc)
        return merged
    
    def _apply_semantic_split(self, chunks_list, content, headers, tenant_id, src_file, accessed_role):
        try:
            sub_docs = self.semantic_splitter.create_documents([content])
            
            for sub in sub_docs:
                t_count = self._count_tokens(sub.page_content)
                
                if t_count > HARD_CAP:
                    hard_splits = self.fallback_splitter.split_text(sub.page_content)
                    for hard_txt in hard_splits:
                        self._add_chunk(chunks_list, sub.page_content, headers, tenant_id, src_file, accessed_role, "hard_cap_fallback")
                else:
                    self._add_chunk(chunks_list, sub.page_content, headers, tenant_id, src_file, accessed_role, "hybrid_semantic")
        
        except Exception as e:
            hard_splits = self.fallback_splitter.split_text(content)
            for hard_txt in hard_splits:
                self._add_chunk(chunks_list, hard_txt, headers, tenant_id, src_file, accessed_role, "hard_cap_error_fallback")
    
    def process_hybrid_splitting(self, text: str, tenant_id: str, src_file: str, accessed_role: List[int]) -> List[Dict]:
        """
        1. pre-process tables -> 2. MD split -> 3. structure split -> 4. smart merge -> 5. semantic/hard cap
        """
        # 1. process tables
        text_safe_tables = self._preprocess_tables(text)
        
        # 2. markdown split
        raw_splits = self.header_splitter.split_text(text_safe_tables)
        
        # 3. structure split
        refined_splits = []
        for doc in raw_splits:
            if self._count_tokens(doc.page_content) > MAX_TOKENS:
                sub_docs = self.structure_splitter.split_documents([doc])
                refined_splits.extend(sub_docs)
            else:
                refined_splits.append(doc)
        
        # 4. smart merge
        merged_splits = self._smart_merge_sections(refined_splits)
        
        final_chunks = []
        
        # 5. final processing
        for doc in merged_splits:
            content = doc.page_content
            headers = doc.metadata
            token_count = self._count_tokens(content)
            
            # case A: chunk > MAX_TOKENS -> semantic split
            if token_count > MAX_TOKENS:
                self._apply_semantic_split(final_chunks, content, headers, tenant_id, src_file, accessed_role)
            
            # case B: chunk <= MAX_TOKENS -> save
            else:
                method = "markdown_adaptive" if token_count > MIN_TOKENS else "merged_small"
                self._add_chunk(final_chunks, content, headers, tenant_id, src_file, accessed_role, method)
                
        return final_chunks
    
    def _add_chunk(self, chunks_list, content, headers, tenant_id, src_file, accessed_role, method):
        enriched_content = self._inject_header_context(content, headers)
        
        flat_metadata = {
            "token_count": self._count_tokens(content),
            "h1": headers.get("H1", ""),
            "h2": headers.get("H2", ""),
            "h3": headers.get("H3", "")
        }
        
        chunks_list.append({
            "tenant_id": tenant_id,
            "src_file": src_file,
            "accessed_role": accessed_role,
            "chunk_id": str(uuid.uuid4()),
            "content": enriched_content,
            "metadata": flat_metadata
        })
    
    def _inject_header_context(self, content: str, metadata: Dict) -> str:
        headers = [metadata[h] for h in ["H1", "H2", "H3"] if h in metadata]
        if not headers: return content
        context_str = " > ".join(headers)
        if context_str not in content[:300]:
            return f"**Bối cảnh: {context_str}**\n\n{content}"
        return content