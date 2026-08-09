from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

import httpx
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings


class ToolError(Exception):
    """A failure the assistant should explain to the user rather than retry."""


@dataclass
class ToolContext:
    user_id: str
    settings: Settings
    http: httpx.AsyncClient
    session: AsyncSession


@dataclass
class ToolResult:
    """Tool output.

    `summary` is a short sentence the voice model can speak directly; `data`
    carries the structured payload for future UI surfaces.
    """

    summary: str
    data: dict[str, Any] = field(default_factory=dict)


ArgsT = TypeVar("ArgsT", bound=BaseModel)
Handler = Callable[[ToolContext, Any], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ActionProposal:
    """Authoritative, read-only preparation for a consequential action.

    Preparers resolve resource identifiers, validate current state, and bind a
    human-readable preview before an approval exists. The handler still
    revalidates the bound snapshot immediately before the write.
    """

    arguments: dict[str, Any]
    preview: str
    audit_preview: str | None = None
    idempotency_key: str | None = None


Preparer = Callable[[ToolContext, Any], Awaitable[ActionProposal]]

RiskLevel = Literal["read", "write", "consequential", "destructive"]


@dataclass(frozen=True)
class CapabilityMeta:
    domain: str
    operation: str
    risk: RiskLevel
    reversible: bool
    parallel_safe: bool
    requires_connection: str | None = None
    produces: tuple[str, ...] = ()
    version: str = "1.0"


@dataclass(frozen=True)
class ToolSpec(Generic[ArgsT]):
    name: str
    description: str
    args_model: type[ArgsT]
    handler: Handler
    # Reserved for higher-risk integrations (calendar, email, smart home) that
    # must be confirmed by the user before the gateway executes them.
    confirmation_required: bool = False
    prepare: Preparer | None = None

    def parameters_schema(self) -> dict[str, Any]:
        schema = self.args_model.model_json_schema()
        # Some consequential tools accept an internal approval snapshot during
        # post-confirmation execution. Never advertise arbitrary extra input to
        # the model even though the validated snapshot is accepted internally.
        schema["additionalProperties"] = False
        return schema
