"""Core script to run the document generation pipeline.
Usage: python run_pipeline.py <template_path> "<user_chat>"
"""
import sys
import json
from src.services.docs_create_service import generate_document


def main():
    if len(sys.argv) < 3:
        print("Usage: python run_pipeline.py <template_path> '<user_chat>'")
        sys.exit(1)

    template_path = sys.argv[1]
    user_chat = sys.argv[2]

    out_path = generate_document(template_path, user_chat)
    # print JSON string as requested by AI server backend contract
    print(json.dumps({"file_path": out_path}))


if __name__ == '__main__':
    main()
