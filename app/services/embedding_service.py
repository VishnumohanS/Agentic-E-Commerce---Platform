import hashlib
import math
import logging
from typing import List, Dict

from app.config import settings

logger = logging.getLogger(__name__)

class EmbeddingService:
    def __init__(self):
        self._client = None
        self._credential_error: str | None = None
        if settings.ai_provider() == "bedrock":
            try:
                import boto3
                session = boto3.Session(region_name=settings.AWS_REGION or None)
                if session.get_credentials() is None:
                    self._credential_error = "AWS credentials were not found."
                else:
                    self._client = session.client(
                        "bedrock-runtime", region_name=settings.AWS_REGION or None
                    )
            except Exception as exc:
                logger.error("AWS Bedrock embedding client initialization failed: %s", exc)
                self._credential_error = str(exc)

        self._cache: Dict[str, List[float]] = {}

    def embed_text(self, text: str) -> List[float]:
        if not self._client:
            return []
        
        text_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if text_hash in self._cache:
            return self._cache[text_hash]
        
        try:
            import json
            result = self._client.invoke_model(
                modelId=settings.BEDROCK_EMBEDDING_MODEL_ID,
                body=json.dumps({"inputText": text}),
                contentType="application/json",
                accept="application/json",
            )
            embedding = json.loads(result["body"].read())["embedding"]
            self._cache[text_hash] = embedding
            return embedding
        except Exception as e:
            logger.error(f"Error embedding text: {e}")
            return []

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        if not self._client:
            return [[] for _ in texts]
            
        embeddings = []
        texts_to_embed = []
        indices_to_embed = []
        
        for i, text in enumerate(texts):
            text_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
            if text_hash in self._cache:
                embeddings.append(self._cache[text_hash])
            else:
                embeddings.append([]) # placeholder
                texts_to_embed.append(text)
                indices_to_embed.append(i)
                
        if texts_to_embed:
            try:
                for i, text in enumerate(texts_to_embed):
                    orig_idx = indices_to_embed[i]
                    embeddings[orig_idx] = self.embed_text(text)
            except Exception as e:
                logger.error(f"Error in batch embedding: {e}")
                
        return embeddings

    def cosine_similarity(self, a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot_product = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot_product / (norm_a * norm_b)

    def is_available(self) -> bool:
        return bool(self._client)

embedding_service = EmbeddingService()
