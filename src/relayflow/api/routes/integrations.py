from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from relayflow.core.credentials import (
    CredentialConfigurationError,
    encrypt_auth_config,
)
from relayflow.core.security import require_admin_api_key
from relayflow.db.session import get_db
from relayflow.models import Integration
from relayflow.models.run import Run
from relayflow.schemas.integration import (
    BearerAuthConfig,
    IntegrationCreate,
    IntegrationRead,
    IntegrationUpdate,
)
from relayflow.schemas.run import RunRead
from relayflow.tasks import execute_integration_run

router = APIRouter(
    prefix="/integrations",
    tags=["Integrations"],
    dependencies=[Depends(require_admin_api_key)],
)


def _encrypt_auth_config_input(
    auth_config: BearerAuthConfig | None,
) -> str | None:
    if auth_config is None:
        return None

    payload = {
        "type": auth_config.type,
        "token": auth_config.token.get_secret_value(),
    }

    try:
        return encrypt_auth_config(payload)
    except CredentialConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credential encryption is not configured.",
        ) from exc


@router.post(
    "",
    response_model=IntegrationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_integration(
    payload: IntegrationCreate,
    db: Session = Depends(get_db),
) -> Integration:
    integration = Integration(
        name=payload.name,
        description=payload.description,
        endpoint_url=str(payload.endpoint_url),
        http_method=payload.http_method,
        request_headers=payload.request_headers,
        request_body=payload.request_body,
        is_active=payload.is_active,
        auth_config_encrypted=_encrypt_auth_config_input(payload.auth_config),
    )

    db.add(integration)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An integration with this name already exists.",
        ) from exc

    db.refresh(integration)
    return integration


@router.get(
    "",
    response_model=list[IntegrationRead],
)
def list_integrations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[Integration]:
    statement = (
        select(Integration).order_by(Integration.created_at.desc()).offset(offset).limit(limit)
    )

    return list(db.scalars(statement).all())


@router.get(
    "/{integration_id}",
    response_model=IntegrationRead,
)
def get_integration(
    integration_id: UUID,
    db: Session = Depends(get_db),
) -> Integration:
    integration = db.get(Integration, integration_id)

    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found.",
        )

    return integration


@router.patch(
    "/{integration_id}",
    response_model=IntegrationRead,
)
def update_integration(
    integration_id: UUID,
    payload: IntegrationUpdate,
    db: Session = Depends(get_db),
) -> Integration:
    integration = db.get(Integration, integration_id)

    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found.",
        )

    changes = payload.model_dump(
        exclude_unset=True,
        exclude={"auth_config"},
    )

    if "auth_config" in payload.model_fields_set:
        changes["auth_config_encrypted"] = _encrypt_auth_config_input(payload.auth_config)

    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided.",
        )

    if "endpoint_url" in changes:
        changes["endpoint_url"] = str(changes["endpoint_url"])

    for field_name, value in changes.items():
        setattr(integration, field_name, value)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The update conflicts with an existing database constraint.",
        ) from exc

    db.refresh(integration)
    return integration


@router.delete(
    "/{integration_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_integration(
    integration_id: UUID,
    db: Session = Depends(get_db),
) -> Response:
    integration = db.get(Integration, integration_id)

    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found.",
        )

    db.delete(integration)
    db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{integration_id}/runs",
    response_model=RunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_integration_run(
    integration_id: UUID,
    db: Session = Depends(get_db),
) -> Run:
    integration = db.get(Integration, integration_id)

    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found.",
        )

    if not integration.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot run an inactive integration.",
        )

    run_id = uuid4()
    task_id = str(uuid4())

    run = Run(
        id=run_id,
        integration_id=integration.id,
        celery_task_id=task_id,
        status="queued",
    )

    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        execute_integration_run.apply_async(
            args=[str(run_id)],
            task_id=task_id,
        )
    except Exception as exc:
        run.status = "failed"
        run.error_message = "Unable to enqueue the execution task."
        run.completed_at = datetime.now(UTC)
        db.commit()

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The run was created but could not be queued.",
        ) from exc

    return run


@router.get(
    "/{integration_id}/runs",
    response_model=list[RunRead],
)
def list_integration_runs(
    integration_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[Run]:
    integration = db.get(Integration, integration_id)

    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found.",
        )

    statement = (
        select(Run)
        .where(Run.integration_id == integration_id)
        .order_by(Run.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    return list(db.scalars(statement).all())


@router.get(
    "/{integration_id}/runs/{run_id}",
    response_model=RunRead,
)
def get_integration_run(
    integration_id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
) -> Run:
    statement = select(Run).where(
        Run.id == run_id,
        Run.integration_id == integration_id,
    )
    run = db.scalar(statement)

    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found for this integration.",
        )

    return run
