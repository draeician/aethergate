import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, List
from sqlmodel import Field, SQLModel, Relationship
from sqlalchemy import Column, JSON

# --- Enums ---
class Capability(str, Enum):
    TEXT = "TEXT"
    IMAGE = "IMAGE"
    AUDIO_STT = "AUDIO_STT"
    AUDIO_TTS = "AUDIO_TTS"
    EMBEDDING = "EMBEDDING"

class BillingUnit(str, Enum):
    TOKEN = "TOKEN"
    IMAGE = "IMAGE"
    MINUTE = "MINUTE"
    REQUEST = "REQUEST"

# --- Tables ---

class User(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    username: str = Field(index=True, unique=True)
    email: Optional[str] = None
    balance: float = Field(default=0.00)
    is_active: bool = Field(default=True)
    organization: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Relationships
    api_keys: List["APIKey"] = Relationship(back_populates="user")
    logs: List["RequestLog"] = Relationship(back_populates="user")

class APIKey(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    key_hash: str = Field(index=True, unique=True)
    key_prefix: str = Field(description="First 8 chars for display")
    user_id: uuid.UUID = Field(foreign_key="user.id")
    name: str = Field(default="default")
    is_active: bool = Field(default=True)
    log_content: bool = Field(default=True)
    rate_limit_model: Optional[str] = Field(default=None, description="Rate limit, e.g. '60/m', '1000/h', '5000/d'")
    allowed_capabilities: List[str] = Field(default=[], sa_column=Column(JSON))
    
    # Relationships
    user: User = Relationship(back_populates="api_keys")


class LLMEndpoint(SQLModel, table=True):
    """A provider endpoint (e.g. OpenAI, local Ollama, vLLM)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, description="Human-readable name, e.g. 'OpenAI', 'Local vLLM'")
    base_url: str = Field(description="Provider API URL, e.g. 'https://api.openai.com/v1'")
    api_key: Optional[str] = Field(default=None, description="Provider authentication key")
    rpm_limit: Optional[int] = Field(default=None, description="Global requests-per-minute for this endpoint")
    day_limit: Optional[int] = Field(default=None, description="Global requests-per-day for this endpoint")
    is_active: bool = Field(default=True)

    # Relationships
    models: List["LLMModel"] = Relationship(back_populates="endpoint")


class LLMModel(SQLModel, table=True):
    id: str = Field(primary_key=True, description="The public model ID, e.g., 'gpt-4o'")
    litellm_name: str = Field(description="The internal LiteLLM name")
    capability: Capability
    billing_unit: BillingUnit
    price_in: float = Field(default=0.0)
    price_out: float = Field(default=0.0)
    is_active: bool = Field(default=True)
    fallback_model_id: Optional[str] = Field(default=None, foreign_key="llmmodel.id")

    # Per-model rate limit overrides (take precedence over endpoint limits)
    rpm_limit: Optional[int] = Field(default=None, description="Model-specific RPM override")
    day_limit: Optional[int] = Field(default=None, description="Model-specific daily limit override")

    # FK to endpoint
    endpoint_id: Optional[int] = Field(default=None, foreign_key="llmendpoint.id")

    # Relationships
    endpoint: Optional[LLMEndpoint] = Relationship(back_populates="models")


class RequestLog(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id")
    api_key_id: uuid.UUID = Field(foreign_key="apikey.id")
    model_used: str
    input_units: float = Field(default=0.0)
    output_units: float = Field(default=0.0)
    total_cost: float = Field(default=0.0)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    # Privacy: These are null if log_content is False
    prompt_text: Optional[str] = Field(default=None) 
    completion_text: Optional[str] = Field(default=None)
    
    # Relationships
    user: User = Relationship(back_populates="logs")
