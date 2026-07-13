"""Image generation tool — local ComfyUI (default) or OpenAI DALL-E."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_VALID_SIZES = {"256x256", "512x512", "768x768", "1024x1024", "1280x720", "720x1280"}

_COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://host.docker.internal:8188")
_COMFYUI_CHECKPOINT = os.environ.get(
    "COMFYUI_CHECKPOINT", "flux1-schnell-fp8.safetensors"
)
_DEFAULT_PROVIDER = os.environ.get("JARVIS_IMAGE_PROVIDER", "comfyui")
_DEFAULT_OUTPUT_DIR = os.environ.get(
    "JARVIS_IMAGE_OUTPUT_DIR", "/root/.openjarvis/media/images"
)


def _flux_workflow(
    prompt: str, width: int, height: int, seed: int, steps: int
) -> dict:
    """ComfyUI API-format workflow for the all-in-one Flux schnell fp8
    checkpoint (CheckpointLoaderSimple + KSampler, cfg 1.0)."""
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": _COMFYUI_CHECKPOINT},
        },
        "2": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["1", 1]},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "", "clip": ["1", 1]},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["3", 0],
                "negative": ["4", 0],
                "latent_image": ["2", 0],
                "seed": seed,
                "steps": steps,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {"images": ["6", 0], "filename_prefix": "jarvis"},
        },
    }


@ToolRegistry.register("image_generate")
class ImageGenerateTool(BaseTool):
    """Generate images locally via ComfyUI, or via OpenAI DALL-E."""

    tool_id = "image_generate"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="image_generate",
            description=(
                "Generate an image from a text description using the local"
                " GPU (ComfyUI / Flux). Returns the saved image path."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Text description of the image to generate.",
                    },
                    "size": {
                        "type": "string",
                        "description": (
                            "Image size, e.g. '1024x1024' (default),"
                            " '1280x720', '720x1280', '512x512'."
                        ),
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Optional file path to save the image to.",
                    },
                    "provider": {
                        "type": "string",
                        "description": (
                            "'comfyui' (local GPU, default) or 'openai' (cloud)."
                        ),
                    },
                    "steps": {
                        "type": "integer",
                        "description": (
                            "Diffusion steps for comfyui (default 4 — Flux"
                            " schnell needs only 4)."
                        ),
                    },
                    "seed": {
                        "type": "integer",
                        "description": "Random seed for reproducibility (comfyui).",
                    },
                },
                "required": ["prompt"],
            },
            category="media",
            timeout_seconds=600.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        prompt = params.get("prompt", "")
        if not prompt:
            return ToolResult(
                tool_name="image_generate",
                content="No prompt provided.",
                success=False,
            )

        provider = params.get("provider") or _DEFAULT_PROVIDER
        if provider == "comfyui":
            return self._generate_comfyui(prompt, params)
        if provider == "openai":
            return self._generate_openai(prompt, params)
        return ToolResult(
            tool_name="image_generate",
            content=(
                f"Unsupported provider '{provider}'."
                " Use 'comfyui' (local) or 'openai'."
            ),
            success=False,
        )

    # ------------------------------------------------------------------
    # ComfyUI (local GPU)
    # ------------------------------------------------------------------

    def _generate_comfyui(self, prompt: str, params: dict) -> ToolResult:
        import httpx

        size = params.get("size", "1024x1024")
        try:
            width, height = (int(v) for v in size.lower().split("x"))
        except ValueError:
            return ToolResult(
                tool_name="image_generate",
                content=f"Invalid size '{size}' — use WIDTHxHEIGHT, e.g. 1024x1024.",
                success=False,
            )
        steps = int(params.get("steps", 4))
        seed = int(params.get("seed", uuid.uuid4().int % (2**32)))

        workflow = _flux_workflow(prompt, width, height, seed, steps)
        client_id = uuid.uuid4().hex

        try:
            with httpx.Client(base_url=_COMFYUI_URL, timeout=30.0) as client:
                resp = client.post(
                    "/prompt", json={"prompt": workflow, "client_id": client_id}
                )
                resp.raise_for_status()
                prompt_id = resp.json()["prompt_id"]

                # Poll history until the job completes (GPU gen can take a bit
                # on first call while the model loads).
                deadline = time.time() + 540
                outputs = None
                while time.time() < deadline:
                    hist = client.get(f"/history/{prompt_id}").json()
                    entry = hist.get(prompt_id)
                    if entry:
                        status = entry.get("status", {})
                        if status.get("status_str") == "error":
                            msgs = json.dumps(status.get("messages", []))[-1500:]
                            return ToolResult(
                                tool_name="image_generate",
                                content=f"ComfyUI workflow error: {msgs}",
                                success=False,
                            )
                        if entry.get("outputs"):
                            outputs = entry["outputs"]
                            break
                    time.sleep(1.0)
                if outputs is None:
                    return ToolResult(
                        tool_name="image_generate",
                        content="ComfyUI generation timed out after 9 minutes.",
                        success=False,
                    )

                # Grab the first saved image
                image_ref = None
                for node_output in outputs.values():
                    for img in node_output.get("images", []):
                        image_ref = img
                        break
                    if image_ref:
                        break
                if image_ref is None:
                    return ToolResult(
                        tool_name="image_generate",
                        content="ComfyUI finished but produced no image output.",
                        success=False,
                    )

                img_resp = client.get(
                    "/view",
                    params={
                        "filename": image_ref["filename"],
                        "subfolder": image_ref.get("subfolder", ""),
                        "type": image_ref.get("type", "output"),
                    },
                )
                img_resp.raise_for_status()
                image_bytes = img_resp.content
        except httpx.ConnectError:
            return ToolResult(
                tool_name="image_generate",
                content=(
                    f"ComfyUI is not reachable at {_COMFYUI_URL}."
                    " Start it on the host with"
                    " D:\\OpenJarvis\\host-bridge\\start-comfyui.ps1"
                ),
                success=False,
            )
        except Exception as exc:
            return ToolResult(
                tool_name="image_generate",
                content=f"ComfyUI error: {exc}",
                success=False,
            )

        output_path = params.get("output_path")
        if output_path:
            out = Path(output_path)
        else:
            out = (
                Path(_DEFAULT_OUTPUT_DIR)
                / f"image_{time.strftime('%Y%m%d_%H%M%S')}_{seed}.png"
            )
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(image_bytes)
        except Exception as exc:
            return ToolResult(
                tool_name="image_generate",
                content=f"Image generated but failed to save to {out}: {exc}",
                success=False,
            )

        return ToolResult(
            tool_name="image_generate",
            content=f"Image saved to {out}",
            success=True,
            metadata={
                "path": str(out),
                "provider": "comfyui",
                "seed": seed,
                "steps": steps,
                "size": f"{width}x{height}",
            },
        )

    # ------------------------------------------------------------------
    # OpenAI DALL-E (cloud fallback)
    # ------------------------------------------------------------------

    def _generate_openai(self, prompt: str, params: dict) -> ToolResult:
        size = params.get("size", "1024x1024")
        if size not in {"256x256", "512x512", "1024x1024"}:
            size = "1024x1024"
        output_path = params.get("output_path")

        try:
            import openai
        except ImportError:
            return ToolResult(
                tool_name="image_generate",
                content=(
                    "openai package not installed. Install with: pip install openai"
                ),
                success=False,
            )

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return ToolResult(
                tool_name="image_generate",
                content="No API key configured. Set OPENAI_API_KEY.",
                success=False,
            )

        try:
            client = openai.OpenAI()
            response = client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size=size,
                n=1,
            )
            url = response.data[0].url
        except Exception as exc:
            return ToolResult(
                tool_name="image_generate",
                content=f"Image generation error: {exc}",
                success=False,
            )

        if output_path:
            try:
                import httpx

                resp = httpx.get(url, follow_redirects=True, timeout=60.0)
                resp.raise_for_status()
                Path(output_path).write_bytes(resp.content)
            except Exception as exc:
                return ToolResult(
                    tool_name="image_generate",
                    content=(
                        f"Image generated but failed to save: {exc}. URL: {url}"
                    ),
                    success=False,
                    metadata={"url": url, "size": size, "provider": "openai"},
                )

        return ToolResult(
            tool_name="image_generate",
            content=url,
            success=True,
            metadata={"url": url, "size": size, "provider": "openai"},
        )


__all__ = ["ImageGenerateTool"]
