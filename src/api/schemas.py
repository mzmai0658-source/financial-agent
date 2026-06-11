from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


MessageRole = str


class StatusItem(BaseModel):
    ok: bool
    detail: str


class HealthResponse(BaseModel):
    service: StatusItem
    database: StatusItem
    knowledge_base: StatusItem
    llm: StatusItem
    examples: StatusItem


class ExampleItem(BaseModel):
    id: str
    type: str
    question: str


class ExamplesResponse(BaseModel):
    examples: List[ExampleItem]


class SessionCreateRequest(BaseModel):
    pass


class ChatQueryRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, description="Conversation session id")
    question: str = Field(..., min_length=1, description="User question")


class ReferenceItem(BaseModel):
    paper_path: str = ""
    source_title: str = ""
    text: str = ""
    score: Optional[float] = None
    paper_image: Optional[str] = None


class AssistantMetadata(BaseModel):
    sql: str = "-"
    chart_format: str = "无"
    chart_data: Optional[Dict[str, Any]] = None
    chart_data_list: List[Dict[str, Any]] = Field(default_factory=list)
    images: List[str] = Field(default_factory=list)
    references: List[ReferenceItem] = Field(default_factory=list)
    validation: Dict[str, Any] = Field(default_factory=dict)
    execution_plan: List[Dict[str, Any]] = Field(default_factory=list)
    needs_clarification: bool = False
    clarify_options: List[str] = Field(default_factory=list)
    context: Dict[str, Any] = Field(default_factory=dict)


class SessionMessage(BaseModel):
    role: MessageRole
    content: str
    ts: str
    metadata: Optional[AssistantMetadata] = None


class SessionResponse(BaseModel):
    session_id: str
    messages: List[SessionMessage]
    latest_context: Dict[str, Any]
    created_at: str
    updated_at: str


class ChatQueryResponse(BaseModel):
    session_id: str
    answer: Dict[str, Any]
    sql: str
    chart_format: str
    chart_data: Optional[Dict[str, Any]] = None
    chart_data_list: List[Dict[str, Any]] = Field(default_factory=list)
    execution_plan: List[Dict[str, Any]]
    validation: Dict[str, Any]
    context: Dict[str, Any]
    needs_clarification: bool
    clarify_options: List[str] = Field(default_factory=list)
    messages: List[SessionMessage]
