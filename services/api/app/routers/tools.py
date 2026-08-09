from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.dependencies import get_http_client, get_session, get_settings
from app.models.schemas import ToolDescription, ToolInvocation, ToolInvocationResponse
from app.security.service_auth import require_service_token
from app.services import google_oauth
from app.tools import registry
from app.tools.base import ToolContext, ToolError

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/v1/tools",
    tags=["tools"],
    dependencies=[Depends(require_service_token)],
)


@router.get("", response_model=list[ToolDescription])
async def list_tools() -> list[ToolDescription]:
    return [
        ToolDescription(
            name=spec.name,
            description=spec.description,
            parameters=spec.parameters_schema(),
            confirmation_required=spec.confirmation_required,
        )
        for spec in registry.SPECS
    ]


@router.post("/invoke", response_model=ToolInvocationResponse)
async def invoke_tool(
    invocation: ToolInvocation,
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_http_client),
    session: AsyncSession = Depends(get_session),
) -> ToolInvocationResponse:
    """Run a tool and always answer 200.

    Consequential tools return a pending confirmation payload instead of executing.
    """
    context = ToolContext(
        user_id=invocation.user_id,
        settings=settings,
        http=http,
        session=session,
    )

    try:
        result = await registry.invoke(
            context,
            invocation.tool,
            invocation.arguments,
            idempotency_key=invocation.idempotency_key,
        )
    except ToolError as error:
        return ToolInvocationResponse(
            tool=invocation.tool,
            ok=False,
            summary=str(error),
            error=str(error),
        )
    except google_oauth.GoogleAuthError as error:
        return ToolInvocationResponse(
            tool=invocation.tool,
            ok=False,
            summary=str(error),
            error=str(error),
        )
    except httpx.HTTPError:
        logger.exception("Upstream call failed for tool %s", invocation.tool)
        message = "That service is not responding right now."
        return ToolInvocationResponse(
            tool=invocation.tool, ok=False, summary=message, error=message
        )
    except Exception:
        logger.exception("Tool %s failed", invocation.tool)
        message = "Something went wrong running that."
        return ToolInvocationResponse(
            tool=invocation.tool, ok=False, summary=message, error=message
        )

    return ToolInvocationResponse(
        tool=invocation.tool,
        ok=True,
        summary=result.summary,
        data=result.data,
    )
