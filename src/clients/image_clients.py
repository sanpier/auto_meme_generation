import os
import base64
import replicate
import requests
import time
from abc import ABC, abstractmethod
from google import genai
from openai import OpenAI
from pathlib import Path
from dataclasses import dataclass


def get_secret(name):
    return os.getenv(name)


# -----------------------------
# Abstract Base Classes
# -----------------------------
@dataclass
class ImageResult:
    path: str
    provider: str
    model: str
    prompt: str

class BaseImageClient(ABC):
    def __init__(self, model, output_dir="data/memes", verbose=False):
        self.model = model
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose

    def _save_bytes(self, image_bytes, filename):
        path = self.output_dir / filename
        with open(path, "wb") as f:
            f.write(image_bytes)
        return str(path)

    @abstractmethod
    def generate(self, prompt, filename="image.png", **kwargs) -> ImageResult:
        pass
    
    def edit(self, image_path, prompt, filename="image.png", size="1024x1024", **kwargs) -> ImageResult:
        raise NotImplementedError(f"{self.__class__.__name__} does not support image editing")


# -----------------------------
# OpenAI image client
# -----------------------------
class OpenAIImageClient(BaseImageClient):
    required_secret = "API_KEY_OPENAI"

    def __init__(
        self,
        model="gpt-image-1",
        output_dir="data/memes",
        verbose=False,
    ):
        super().__init__(model=model, output_dir=output_dir, verbose=verbose)
        api_key = get_secret(self.required_secret)
        if not api_key:
            raise ValueError("Missing API_KEY_OPENAI")
        self.client = OpenAI(api_key=api_key)

    def generate(
        self,
        prompt,
        filename="image.png",
        size="1024x1024",
        **kwargs,
    ):
        for _ in range(3):
            try:
                response = self.client.images.generate(
                    model=self.model,
                    prompt=prompt,
                    size=size,
                    **kwargs,
                )
                break 
            except Exception as e:
                if "429" in str(e):
                    time.sleep(6)
                else:
                    raise
                
        image_base64 = response.data[0].b64_json
        image_bytes = base64.b64decode(image_base64)
        path = self._save_bytes(image_bytes, filename)

        return ImageResult(
            path=path,
            provider="openai",
            model=self.model,
            prompt=prompt,
        )

    def edit(
        self,
        image_path,
        prompt,
        filename="image.png",
        size="1024x1024",
        **kwargs,
    ):
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Template image not found: {image_path}")

        last_error = None
        for _ in range(3):
            try:
                with open(image_path, "rb") as image_file:
                    response = self.client.images.edit(
                        model=self.model,
                        image=image_file,
                        prompt=prompt,
                        size=size,
                        **kwargs,
                    )
                break
            except Exception as e:
                last_error = e
                if "429" in str(e):
                    time.sleep(6)
                    continue
                raise
        else:
            raise last_error

        image_base64 = response.data[0].b64_json
        image_bytes = base64.b64decode(image_base64)
        path = self._save_bytes(image_bytes, filename)

        return ImageResult(
            path=path,
            provider="openai",
            model=self.model,
            prompt=prompt,
        )


# -----------------------------
# Gemini / Imagen image client
# -----------------------------
class GeminiImageClient(BaseImageClient):
    required_secret = "API_KEY_GEMINI"

    def __init__(
        self,
        model="imagen-4.0-generate-001",
        output_dir="data/memes",
        verbose=False,
    ):
        super().__init__(model=model, output_dir=output_dir, verbose=verbose)
        api_key = get_secret(self.required_secret)
        if not api_key:
            raise ValueError("Missing API_KEY_GEMINI")
        self.client = genai.Client(api_key=api_key)

    def generate(
        self,
        prompt,
        filename="image.png",
        **kwargs,
    ):
        last_error = None
        for _ in range(3):
            try:
                response = self.client.models.generate_images(
                    model=self.model,
                    prompt=prompt,
                    **kwargs,
                )
                break
            except Exception as e:
                last_error = e
                if "429" in str(e):
                    time.sleep(6)
                    continue
                raise
        else:
            raise last_error

        generated_image = response.generated_images[0]
        image_bytes = generated_image.image.image_bytes
        path = self._save_bytes(image_bytes, filename)
        return ImageResult(
            path=path,
            provider="google",
            model=self.model,
            prompt=prompt,
        )
    

# -----------------------------
# Replicate image client
# -----------------------------
class ReplicateImageClient(BaseImageClient):
    required_secret = "API_KEY_REPLICATE"

    def __init__(
        self,
        model="black-forest-labs/flux-dev",
        output_dir="data/memes",
        verbose=False,
    ):
        super().__init__(model=model, output_dir=output_dir, verbose=verbose)
        api_key = get_secret(self.required_secret)
        if not api_key:
            raise ValueError("Missing API_KEY_REPLICATE")
        self.client = replicate.Client(api_token=api_key)

    def generate(
        self,
        prompt,
        filename="image.png",
        **kwargs,
    ):
        for _ in range(3):
            try:
                output = self.client.run(
                    self.model,
                    input={
                        "prompt": prompt,
                        **kwargs,
                    },
                )
                break
            except Exception as e:
                if "429" in str(e):
                    time.sleep(6)
                else:
                    raise

        image_url = output[0] if isinstance(output, list) else output
        response = requests.get(image_url, timeout=60)
        response.raise_for_status()
        image_bytes = response.content
        path = self._save_bytes(image_bytes, filename)

        return ImageResult(
            path=path,
            provider="replicate",
            model=self.model,
            prompt=prompt,
        )


# -----------------------------
# Fallback image client
# -----------------------------
class FallbackImageClient:
    def __init__(self, clients, verbose=False):
        self.clients = clients
        self.verbose = verbose
        self.last_working_client = None

    def generate(self, prompt, filename="image.png", **kwargs):
        errors = []
        ordered_clients = self.clients
        if self.last_working_client is not None:
            ordered_clients = [self.last_working_client] + [
                c for c in self.clients if c is not self.last_working_client
            ]

        for client in ordered_clients:
            try:
                result = client.generate(
                    prompt=prompt,
                    filename=filename,
                    **kwargs,
                )
                self.last_working_client = client
                return result
            except Exception as e:
                errors.append((client.model, str(e)))
        raise RuntimeError(f"All image clients failed: {errors}")
    

    def edit(self, image_path, prompt, filename="image.png", **kwargs):
        errors = []
        ordered_clients = self.clients
        if self.last_working_client is not None:
            ordered_clients = [self.last_working_client] + [
                c for c in self.clients if c is not self.last_working_client
            ]

        for client in ordered_clients:
            try:
                result = client.edit(
                    image_path=image_path,
                    prompt=prompt,
                    filename=filename,
                    **kwargs,
                )
                self.last_working_client = client
                return result
            except NotImplementedError as e:
                errors.append((client.model, str(e)))
                continue
            except Exception as e:
                errors.append((client.model, str(e)))
                continue
        raise RuntimeError(f"All image edit clients failed: {errors}")