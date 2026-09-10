import torch
import base64
from pathlib import Path
import pypdfium2 as pdfium
import io
from transformers import LightOnOcrForConditionalGeneration, LightOnOcrProcessor

NAME_OCR_MODEL = "lightonai/LightOnOCR-2-1B"

# Keep existing path constants for backward-compatibility / local debugging if needed
PATH_INPUT_FILE = "./data/raw_dir"
PATH_OUTPUT_FILE = "./data/md_dir"

class OCRService:
    def __init__(self, model_name = NAME_OCR_MODEL):
        self.device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
        self.dtype = torch.float32 if self.device == "mps" else torch.bfloat16
        
        self.model = LightOnOcrForConditionalGeneration.from_pretrained(model_name, torch_dtype = self.dtype).to(self.device)
        self.processor = LightOnOcrProcessor.from_pretrained(model_name)
    
    def processing_data(self, path_input) -> str:
        """
        Process the PDF at path_input and return the full OCR result as a single markdown string.

        Note: Previously this method wrote page-by-page output to a local file. Now it returns the
        aggregated markdown text so the caller can decide where to store it (local or remote).
        """
        pdf = pdfium.PdfDocument(path_input)
        num_pages = len(pdf)

        output_lines = []
        try:
            for i in range(num_pages):
                page = pdf[i]
                pil_image = page.render(scale = 2.0).to_pil()
                page.close()

                buffer = io.BytesIO()
                pil_image.save(buffer, format = "PNG")
                image_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                pil_image.close()
                buffer.close()

                conversation = [{
                    "role": "user",
                    "content": [
                        {"type": "image", "url": f"data:image/png;base64,{image_base64}"},
                        {"type": "text", "text": "Extract all text from this document and convert to markdown format."}
                    ]
                }]

                inputs = self.processor.apply_chat_template(
                    conversation,
                    add_generation_prompt = True,
                    tokenize = True,
                    return_dict = True,
                    return_tensors = "pt",
                )

                inputs = {k: v.to(device=self.device, dtype=self.dtype) if v.is_floating_point() else v.to(self.device) for k, v in inputs.items()}

                output_ids = self.model.generate(**inputs, max_new_tokens = 1024)
                generated_ids = output_ids[0, inputs['input_ids'].shape[1]:]
                output_text = self.processor.decode(generated_ids, skip_special_tokens = True)

                output_lines.append(output_text)

        finally:
            pdf.close()

        # Join page outputs with two newlines to separate pages in markdown
        return "\n\n".join(output_lines)
