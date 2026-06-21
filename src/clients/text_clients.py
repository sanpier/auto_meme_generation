import json
import os
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from google import genai
from groq import Groq
from openai import OpenAI


# -----------------------------
# Abstract Base Client
# -----------------------------
class BaseLLMClient(ABC):

    def __init__(self, model):
        self.model = model

    def build_messages(self, prompt, system=None):
        messages = []
        if system:
            messages.append({
                "role": "system",
                "content": system,
            })
        messages.append({
            "role": "user",
            "content": prompt,
        })
        return messages

    def generate_json(self, prompt, system=None, schema_hint=None, **kwargs):
        json_system = """
            You are a structured JSON generator.
            Return only valid JSON.
            Do not include markdown.
            Do not include explanations.
        """

        if system:
            json_system = system + "\n\n" + json_system

        if schema_hint:
            prompt = f"""
                Return JSON matching this structure:
                {schema_hint}
                Task:
                {prompt}
            """

        text = self.generate(prompt=prompt, system=json_system, **kwargs)
        cleaned = text.strip()
        cleaned = cleaned.replace("```json", "").replace("```", "").strip()
        return json.loads(cleaned)

    @abstractmethod
    def generate(self, prompt, system=None, **kwargs):
        pass


# -----------------------------
# Groq Client
# -----------------------------
class GroqClient(BaseLLMClient):

    required_secret = "API_KEY_GROQ"

    def __init__(self, model="llama-3.3-70b-versatile"):
        api_key = os.getenv(self.required_secret)
        if not api_key:
            raise ValueError("Missing API key")
        self.client = Groq(api_key=api_key)
        super().__init__(model=model)


    def generate(self, prompt, system=None, **kwargs):
        messages = self.build_messages(prompt=prompt, system=system)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **kwargs,
        )
        return response.choices[0].message.content.strip()

    def generate_json(self, prompt, system=None, schema_hint=None, **kwargs):
        kwargs.setdefault("response_format", {"type": "json_object"})
        return super().generate_json(
            prompt=prompt,
            system=system,
            schema_hint=schema_hint,
            **kwargs,
        )
    

# -----------------------------
# OpenRouter Client
# -----------------------------
class OpenRouterClient(BaseLLMClient):

    required_secret = "API_KEY_OPENROUTER"
    
    def __init__(self, model="openrouter/free"):
        api_key = os.getenv(self.required_secret)
        if not api_key:
            raise ValueError("Missing API key")        
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            default_headers={
                "HTTP-Referer": "http://localhost",
                "X-OpenRouter-Title": "AI Scholar",
            },
        )     
        super().__init__(model=model)

    def generate(self, prompt, system=None, **kwargs):
        messages = self.build_messages(prompt=prompt, system=system)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **kwargs,
        )
        return response.choices[0].message.content.strip()

    def generate_json(self, prompt, system=None, schema_hint=None, **kwargs):
        kwargs.setdefault("response_format", {"type": "json_object"})
        return super().generate_json(
            prompt=prompt,
            system=system,
            schema_hint=schema_hint,
            **kwargs,
        )
    

# -----------------------------
# Gemini Client
# -----------------------------
class GeminiClient(BaseLLMClient):

    required_secret = "API_KEY_GEMINI"

    def __init__(self, model="gemini-2.5-flash"):
        api_key = os.getenv(self.required_secret)
        if not api_key:
            raise ValueError("Missing API key")
        self.client = genai.Client(api_key=api_key)        
        super().__init__(model=model)

    def generate(self, prompt, system=None, **kwargs):
        full_prompt = prompt
        if system:
            full_prompt = f"{system}\n\n{prompt}"
            
        temperature = kwargs.pop("temperature", None)
        max_tokens = kwargs.pop("max_tokens", None)
        config = kwargs.pop("config", {})
        if temperature is not None:
            config["temperature"] = temperature
        if max_tokens is not None:
            config["max_output_tokens"] = max_tokens
            
        response = self.client.models.generate_content(
            model=self.model,
            contents=full_prompt,
            config=config,
            **kwargs,
        )
        return response.text.strip()

    def generate_json(self, prompt, system=None, schema_hint=None, **kwargs):
        config = kwargs.pop("config", {})
        config["response_mime_type"] = "application/json"
        kwargs["config"] = config
        return super().generate_json(
            prompt=prompt,
            system=system,
            schema_hint=schema_hint,
            **kwargs,
        )


# -----------------------------
# OpenAI Client
# -----------------------------
class OpenAIClient(BaseLLMClient):

    required_secret = "API_KEY_OPENAI"
    
    def __init__(self, model="gpt-4.1-mini"):
        api_key = os.getenv(self.required_secret)
        if not api_key:
            raise ValueError("Missing API key")
        self.client = OpenAI(api_key=api_key)        
        super().__init__(model=model)

    def generate(self, prompt, system=None, **kwargs):
        messages = self.build_messages(
            prompt=prompt,
            system=system,
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **kwargs,
        )
        return response.choices[0].message.content.strip()

    def generate_json(self, prompt, system=None, schema_hint=None, **kwargs):
        kwargs.setdefault("response_format", {"type": "json_object"})
        return super().generate_json(
            prompt=prompt,
            system=system,
            schema_hint=schema_hint,
            **kwargs,
        )
    
    def embed(self, texts, model="text-embedding-3-small", batch_size=64, **kwargs):
        if isinstance(texts, str):
            texts = [texts]

        vectors = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            response = self.client.embeddings.create(
                model=model,
                input=batch,
                **kwargs,
            )
            vectors.extend([item.embedding for item in response.data])
        return vectors


# -----------------------------
# Fallback LLM Client
# -----------------------------
class FallbackLLM:
    
    def __init__(self, clients, timeout_seconds=30, debug=False, prefer_last_working=True):
        self.clients = clients
        self.timeout_seconds = timeout_seconds
        self.debug = debug
        self.prefer_last_working = prefer_last_working
        self.last_working_client = None
        self.last_used_model = None
        self.primary_failed = None

    def generate_with_timeout(self, client, prompt, system=None, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                client.generate,
                prompt,
                system,
                **kwargs,
            )
            return future.result(timeout=self.timeout_seconds)

    def generate(self, prompt, system=None, fallback_value=None, **kwargs):
        errors = []
        self.primary_failed = None
        self.last_used_model = None
        if self.prefer_last_working and self.last_working_client is not None:
            ordered_clients = [self.last_working_client] + [
                c for c in self.clients if c is not self.last_working_client
            ]
        else:
            ordered_clients = self.clients

        for client in ordered_clients:
            try:
                result = self.generate_with_timeout(
                    client=client,
                    prompt=prompt,
                    system=system,
                    **kwargs,
                )
                self.last_working_client = client
                self.last_used_model = client.model
                return result
            except TimeoutError:
                if client == ordered_clients[0]:
                    self.primary_failed = client.model
                errors.append((client.model, f"timeout > {self.timeout_seconds}s"))
                if self.debug:
                    print(f"Timeout: {client.model}")
            except Exception as e:
                if client == ordered_clients[0]:
                    self.primary_failed = client.model
                errors.append((client.model, str(e)))
                if self.debug:
                    print(f"Failed: {client.model} | {e}")

        if fallback_value is not None:
            return fallback_value
        if self.debug:
            print(f"All LLM clients failed: {errors}")
        raise RuntimeError(f"All LLM clients failed: {errors}")

    def generate_json_with_timeout(
        self,
        client,
        prompt,
        system=None,
        schema_hint=None,
        **kwargs,
    ):
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                client.generate_json,
                prompt,
                system,
                schema_hint,
                **kwargs,
            )
            return future.result(timeout=self.timeout_seconds)
        
    def generate_json(
        self,
        prompt,
        system=None,
        schema_hint=None,
        fallback_value=None,
        **kwargs,
    ):
        errors = []
        self.primary_failed = None
        self.last_used_model = None

        if (
            self.prefer_last_working
            and self.last_working_client is not None
        ):
            ordered_clients = [self.last_working_client] + [
                c for c in self.clients
                if c is not self.last_working_client
            ]
        else:
            ordered_clients = self.clients

        for client in ordered_clients:
            try:
                result = self.generate_json_with_timeout(
                    client=client,
                    prompt=prompt,
                    system=system,
                    schema_hint=schema_hint,
                    **kwargs,
                )
                self.last_working_client = client
                self.last_used_model = client.model
                return result

            except TimeoutError:
                if client == ordered_clients[0]:
                    self.primary_failed = client.model
                errors.append(
                    (client.model, f"timeout > {self.timeout_seconds}s")
                )

            except Exception as e:
                if client == ordered_clients[0]:
                    self.primary_failed = client.model
                errors.append(
                    (client.model, str(e))
                )

        if fallback_value is not None:
            return fallback_value
        raise RuntimeError(f"All JSON LLM clients failed: {errors}")