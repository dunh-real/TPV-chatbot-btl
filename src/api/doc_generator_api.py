"""Simple RESTful API exposing document generation endpoint.
Endpoint: POST /generate_doc
Request JSON: { "template_path": "src/templates/Template_Cong_Van.docx", "user_chat": "..." }
Response JSON: { "result": "<json string with file_path>" }
"""
from flask import Flask, request, jsonify
import json
from src.services.docs_create_service import generate_document

app = Flask(__name__)

@app.route('/generate_doc', methods=['POST'])
def generate_doc():
    data = request.get_json(force=True)
    template_path = data.get('template_path')
    user_chat = data.get('user_chat')

    if not template_path or not user_chat:
        return jsonify({'error': 'template_path and user_chat are required'}), 400

    try:
        out_path = generate_document(template_path, user_chat)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # AI server contract: send back a JSON string with info including path
    result_obj = {"file_path": out_path}
    result_json_str = json.dumps(result_obj)

    return jsonify({'result': result_json_str})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
