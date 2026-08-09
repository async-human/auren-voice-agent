from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends

from app.models.schemas import CapabilityCatalogResponse, CapabilityGroup, ToolDescription
from app.models.tables import User
from app.security.auth import require_user
from app.tools import registry

router = APIRouter(prefix="/v1/capabilities", tags=["capabilities"])


@router.get("", response_model=CapabilityCatalogResponse)
async def capability_catalog(_user: User = Depends(require_user)) -> CapabilityCatalogResponse:
    grouped: dict[str, list[ToolDescription]] = defaultdict(list)
    for spec in registry.SPECS:
        meta = registry.CAPABILITIES[spec.name]
        grouped[meta.domain].append(
            ToolDescription(
                name=spec.name,
                description=spec.description,
                parameters=spec.parameters_schema(),
                confirmation_required=spec.confirmation_required,
                domain=meta.domain,
                operation=meta.operation,
                risk=meta.risk,
                reversible=meta.reversible,
                parallel_safe=meta.parallel_safe,
                requires_connection=meta.requires_connection,
                produces=list(meta.produces),
                version=meta.version,
            )
        )
    return CapabilityCatalogResponse(
        version="1.0",
        capabilities=[
            CapabilityGroup(domain=domain, tools=tools)
            for domain, tools in sorted(grouped.items())
        ],
    )
