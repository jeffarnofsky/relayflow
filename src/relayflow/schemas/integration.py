from typing import Any, Literal
from uuid import UUID

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]

PROTECTED_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "host",
    "content-length",
}


def validate_request_headers(
    value: dict[str, str],
) -> dict[str, str]:
    for key, header_value in value.items():
        if key.lower() in PROTECTED_HEADERS:
            raise ValueError(f"Header '{key}' must not be stored as a plain-text header.")

        if not key.strip():
            raise ValueError("Header names cannot be empty.")

        if any(char in key or char in header_value for char in "\r\n"):
            raise ValueError("Headers cannot contain newline characters.")

    return value


class BearerAuthConfig(BaseModel):
    type: Literal["bearer"] = "bearer"
    token: SecretStr

    @field_validator("token")
    @classmethod
    def token_must_not_be_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Bearer token must not be blank.")
        return value


class IntegrationCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )
    auth_config: BearerAuthConfig | None = None

    name: str = Field(min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    endpoint_url: AnyHttpUrl
    http_method: HttpMethod = "GET"

    request_headers: dict[str, str] = Field(default_factory=dict)
    request_body: dict[str, Any] | list[Any] | None = None

    is_active: bool = True

    @field_validator("request_headers")
    @classmethod
    def check_request_headers(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        return validate_request_headers(value)


class IntegrationUpdate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )
    auth_config: BearerAuthConfig | None = None

    name: str | None = Field(default=None, min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    endpoint_url: AnyHttpUrl | None = None
    http_method: HttpMethod | None = None

    request_headers: dict[str, str] | None = None
    request_body: dict[str, Any] | list[Any] | None = None

    is_active: bool | None = None

    @field_validator("request_headers")
    @classmethod
    def check_request_headers(
        cls,
        value: dict[str, str] | None,
    ) -> dict[str, str] | None:
        if value is None:
            return value

        return validate_request_headers(value)

    @model_validator(mode="after")
    def check_non_nullable_fields(self) -> "IntegrationUpdate":
        non_nullable = {
            "name",
            "endpoint_url",
            "http_method",
            "request_headers",
            "is_active",
        }

        for field_name in non_nullable:
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null.")

        return self


class IntegrationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    endpoint_url: str
    http_method: str
    request_headers: dict[str, str]
    request_body: dict[str, Any] | list[Any] | None
    is_active: bool
    created_at: Any
    updated_at: Any
