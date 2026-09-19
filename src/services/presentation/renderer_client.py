"""
Python Client Wrapper for Node.js PPTX Renderer.
Invokes the Node CLI subprocess to generate PowerPoint (.pptx) files from RenderPlan JSON.
"""

import os
import json
import logging
import subprocess
import tempfile

logger = logging.getLogger(__name__)

RENDERER_DIR = os.path.join(os.path.dirname(__file__), "renderer")
CLI_ENTRY = os.path.join(RENDERER_DIR, "dist", "cli.js")


def render_pptx_from_plan(render_plan: dict, output_pptx_path: str) -> str:
    """
    Invoke Node.js renderer CLI to create a PPTX file from a RenderPlan dictionary.

    Args:
        render_plan: RenderPlan dict containing slides and deck metadata.
        output_pptx_path: Absolute or relative output path for the .pptx file.

    Returns:
        Absolute path to the created .pptx file.
    """
    if not os.path.exists(CLI_ENTRY):
        logger.info("Compiled renderer CLI not found. Attempting 'npm run build'...")
        subprocess.run(["npm", "run", "build"], cwd=RENDERER_DIR, check=True)

    # Write temporary render plan JSON
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        tmp_plan_path = tmp.name
        json.dump(render_plan, tmp, ensure_ascii=False, indent=2)

    try:
        abs_output_path = os.path.abspath(output_pptx_path)
        os.makedirs(os.path.dirname(abs_output_path), exist_ok=True)

        cmd = ["node", CLI_ENTRY, tmp_plan_path, abs_output_path]
        logger.info(f"Invoking Node PPTX Renderer: {' '.join(cmd)}")

        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60
        )

        if result.returncode != 0:
            logger.error(f"Node Renderer failed (code {result.returncode}):\n{result.stderr}")
            raise RuntimeError(f"PPTX rendering failed: {result.stderr}")

        logger.info(f"PPTX file created successfully at '{abs_output_path}'")
        return abs_output_path

    finally:
        if os.path.exists(tmp_plan_path):
            os.remove(tmp_plan_path)
