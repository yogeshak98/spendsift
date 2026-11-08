from __future__ import annotations

from fastapi import APIRouter, FastAPI, Response

from backend.app.models.api.settings import (
    AccountCloseRequest,
    AccountCloseResponse,
    AccountRenameRequest,
    AccountRenameResponse,
    AccountsCreateRequest,
    AccountsCreateResponse,
    CardProperties,
    CardPropertiesBatchRequest,
    CardPropertiesListResponse,
    CardPropertiesUpdateRequest,
    TemplateListResponse,
)
from backend.app.core.errors.errors import (
    ACCOUNT_NOT_OPEN,
    TEMPLATE_FINGERPRINT_MISMATCH,
    TEMPLATE_NOT_FOUND,
)
from backend.app.models.storage.template import Template
from backend.app.service.ledger.account_meta import UNCHANGED, CardPropertiesUpdate


def register_settings_routes(app: FastAPI) -> None:
    router = APIRouter()

    @router.post("/api/accounts", response_model=AccountsCreateResponse)
    def create_accounts(payload: AccountsCreateRequest) -> dict:
        return app.state.account_admin_service.create(payload.accounts)

    @router.post("/api/accounts/rename", response_model=AccountRenameResponse)
    def rename_account(payload: AccountRenameRequest) -> dict:
        try:
            return app.state.account_admin_service.rename(
                payload.account, payload.new_name
            )
        except LookupError as exc:
            raise ACCOUNT_NOT_OPEN.exc({"account": payload.account}) from exc

    @router.post("/api/accounts/close", response_model=AccountCloseResponse)
    def close_account(payload: AccountCloseRequest) -> dict:
        try:
            return app.state.account_admin_service.close(payload.account)
        except LookupError as exc:
            raise ACCOUNT_NOT_OPEN.exc({"account": payload.account}) from exc

    @router.get(
        "/api/accounts/card-properties", response_model=CardPropertiesListResponse
    )
    def list_card_properties() -> dict:
        return {"cards": app.state.account_meta_service.card_properties()}

    @router.put("/api/accounts/card-properties", response_model=CardProperties)
    def put_card_properties(payload: CardPropertiesUpdateRequest) -> dict:
        # A body without `expiry` keeps the stored one.
        expiry = payload.expiry if "expiry" in payload.model_fields_set else UNCHANGED
        try:
            return app.state.account_meta_service.set_card_properties(
                payload.account,
                payload.statement_day,
                payload.due_day,
                payload.last4,
                expiry,
            )
        except LookupError as exc:
            raise ACCOUNT_NOT_OPEN.exc({"account": payload.account}) from exc

    @router.put(
        "/api/accounts/card-properties/batch",
        response_model=CardPropertiesListResponse,
    )
    def put_card_properties_batch(payload: CardPropertiesBatchRequest) -> dict:
        updates = [
            CardPropertiesUpdate(
                card.account, card.statement_day, card.due_day, card.last4, card.expiry
            )
            for card in payload.cards
        ]
        try:
            cards = app.state.account_meta_service.set_card_properties_many(updates)
        except LookupError as exc:
            params = {"account": str(exc.args[0]) if exc.args else ""}
            raise ACCOUNT_NOT_OPEN.exc(params, data=params) from exc
        return {"cards": cards}

    @router.get("/api/templates", response_model=TemplateListResponse)
    def list_templates() -> dict:
        return {"templates": app.state.template_store.list()}

    @router.get("/api/templates/{fingerprint}", response_model=Template)
    def get_template(fingerprint: str) -> Template:
        template = app.state.template_store.get_by_fingerprint(fingerprint)
        if template is None:
            raise TEMPLATE_NOT_FOUND.exc()
        return template

    @router.put("/api/templates/{fingerprint}", response_model=Template)
    def put_template(fingerprint: str, template: Template) -> Template:
        if template.fingerprint != fingerprint:
            raise TEMPLATE_FINGERPRINT_MISMATCH.exc(
                {
                    "body_fingerprint": template.fingerprint,
                    "path_fingerprint": fingerprint,
                }
            )
        app.state.template_store.save(template)
        return template

    @router.delete("/api/templates/{fingerprint}", status_code=204)
    def delete_template(fingerprint: str) -> Response:
        if not app.state.template_store.delete(fingerprint):
            raise TEMPLATE_NOT_FOUND.exc()
        return Response(status_code=204)

    app.include_router(router)
