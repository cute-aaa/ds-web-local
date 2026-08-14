"""pydantic 模型。"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ServiceConfig(BaseModel):
    name: str
    transport: str = "stdio"          # stdio | sse | streamable-http
    command: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    url: Optional[str] = None
    env: Dict[str, str] = Field(default_factory=dict)
    tools: List[str] = Field(default_factory=list)
    auto_start: bool = False
    timeout: int = 120
    description: str = ""


class ToolCall(BaseModel):
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class BridgeCallRequest(BaseModel):
    tool: str = ""
    arguments: Dict[str, Any] = Field(default_factory=dict)
    calls: List[ToolCall] = Field(default_factory=list)


class ToolInfo(BaseModel):
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    source: str = "builtin"           # builtin | mcp | skill


class SkillDefinition(BaseModel):
    name: str
    description: str = ""
    prompt: str = ""
    tools: List[Dict[str, Any]] = Field(default_factory=list)
    output_template: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
