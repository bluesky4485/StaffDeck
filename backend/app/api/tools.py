from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.db import get_session
from app.db.models import Tool, utc_now
from app.security.tenant import ensure_tenant
from app.tools import ToolExecutor
from app.tools.tool_schema import ToolCall, ToolCreateRequest, ToolRead, ToolResult, ToolTestRequest, ToolUpdateRequest

router = APIRouter(prefix="/api/enterprise/tools", tags=["enterprise:tools"])


def tool_read(row: Tool) -> ToolRead:
    return ToolRead(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        display_name=row.display_name,
        description=row.description,
        method=row.method,
        url=row.url,
        headers=row.headers_json or {},
        auth=row.auth_json or {},
        input_schema=row.input_schema or {},
        output_schema=row.output_schema or {},
        allowed_skills=row.allowed_skills_json or [],
        enabled=row.enabled,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


@router.get("", response_model=list[ToolRead])
def list_tools(tenant_id: str = Query(...), db: Session = Depends(get_session)) -> list[ToolRead]:
    ensure_tenant(db, tenant_id)
    rows = db.exec(select(Tool).where(Tool.tenant_id == tenant_id)).all()
    return [tool_read(row) for row in rows]


@router.post("", response_model=ToolRead)
def create_tool(request: ToolCreateRequest, db: Session = Depends(get_session)) -> ToolRead:
    ensure_tenant(db, request.tenant_id)
    existing = db.exec(
        select(Tool).where(Tool.tenant_id == request.tenant_id, Tool.name == request.name)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Tool name already exists for this tenant")
    row = Tool(
        tenant_id=request.tenant_id,
        name=request.name,
        display_name=request.display_name,
        description=request.description,
        method=request.method,
        url=request.url,
        headers_json=request.headers,
        auth_json=request.auth,
        input_schema=request.input_schema,
        output_schema=request.output_schema,
        allowed_skills_json=request.allowed_skills,
        enabled=request.enabled,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return tool_read(row)


@router.get("/{tool_id}", response_model=ToolRead)
def get_tool(tool_id: str, tenant_id: str = Query(...), db: Session = Depends(get_session)) -> ToolRead:
    row = _get_tool(db, tenant_id, tool_id)
    return tool_read(row)


@router.put("/{tool_id}", response_model=ToolRead)
def update_tool(tool_id: str, request: ToolUpdateRequest, db: Session = Depends(get_session)) -> ToolRead:
    row = _get_tool(db, request.tenant_id, tool_id)
    row.name = request.name
    row.display_name = request.display_name
    row.description = request.description
    row.method = request.method
    row.url = request.url
    row.headers_json = request.headers
    row.auth_json = request.auth
    row.input_schema = request.input_schema
    row.output_schema = request.output_schema
    row.allowed_skills_json = request.allowed_skills
    row.enabled = request.enabled
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return tool_read(row)


@router.delete("/{tool_id}")
def delete_tool(
    tool_id: str,
    tenant_id: str = Query(...),
    db: Session = Depends(get_session),
) -> dict[str, str]:
    row = _get_tool(db, tenant_id, tool_id)
    db.delete(row)
    db.commit()
    return {"status": "deleted"}


@router.post("/{tool_id}/test", response_model=ToolResult)
def test_tool(tool_id: str, request: ToolTestRequest, db: Session = Depends(get_session)) -> ToolResult:
    row = _get_tool(db, request.tenant_id, tool_id)
    return ToolExecutor(db).execute(request.tenant_id, ToolCall(name=row.name, arguments=request.arguments))


def _get_tool(db: Session, tenant_id: str, tool_id: str) -> Tool:
    ensure_tenant(db, tenant_id)
    row = db.get(Tool, tool_id)
    if not row or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Tool not found")
    return row
