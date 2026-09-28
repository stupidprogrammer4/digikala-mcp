from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from mcp_types import ToolAnnotations

from src.app.accounts import AccountService
from src.app.cart_operations import inspect_operation
from src.infra.db.exceptions import UnresolvedOperation
from src.infra.http import GatewayError
from src.models.schemas.account import (
    AccountConnection,
    AccountDisconnected,
    AccountLogin,
    CartReplacement,
    CartReplacementResult,
)
from src.models.schemas.cart import CartChange, CartOperation, CartPlan, CartSnapshot, PlanId
from src.models.schemas.cart_status import CartOperationStatus
from src.tools.dependencies import from_dishka
from src.tools.errors import handle_database_errors


def register_account_tools(server: FastMCP) -> None:
    @server.tool(annotations=ToolAnnotations(read_only_hint=False, open_world_hint=True))
    @handle_database_errors
    async def login_account(
        credentials: AccountLogin, service: AccountService = Depends(from_dishka(AccountService))
    ) -> AccountConnection:
        """Trusted backend only: password login, returning an ephemeral opaque tool-session token.

        Do not expose credentials or the returned token to an LLM or a user-visible log.
        Token is scoped to this MCP process; upstream cookies remain private in memory.
        OTP-required accounts return challenge_required. No OTP bypass or automatic retry.
        """
        try:
            result = await service.login(credentials)
            return result
        except GatewayError as exc:
            raise ValueError(exc.error.code) from None

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, open_world_hint=False))
    @handle_database_errors
    async def logout_account(
        session_token: str, service: AccountService = Depends(from_dishka(AccountService))
    ) -> AccountDisconnected:
        """Discard this host tool session without affecting other accounts."""
        service.logout(session_token)
        return AccountDisconnected()

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
    @handle_database_errors
    async def read_account_cart(
        session_token: str, service: AccountService = Depends(from_dishka(AccountService))
    ) -> CartSnapshot:
        """Read only the account bound to this token; never use the desktop keyring fallback."""
        try:
            session = service.require(session_token)
            async with service.gateways(session) as gateway:
                result = await gateway.read()
                return result
        except GatewayError as exc:
            raise ValueError(exc.error.code) from None

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True
        )
    )
    @handle_database_errors
    async def replace_account_cart(
        session_token: str,
        replacement: CartReplacement,
        service: AccountService = Depends(from_dishka(AccountService)),
    ) -> CartReplacementResult:
        """Replace the connected user's entire cart with selected exact offers, one unit each.

        Trusted host must have explicit authorization for clearing this account's cart and
        adding these items. Refreshes all offers before any removal. Reuse request_id for
        retries; uncertain/partial outcomes never blindly resend writes. No checkout/payment.
        """
        try:
            session = service.require(session_token)
            async with service.gateways(session) as gateway:
                result = await service.replacer.replace(gateway, replacement)
                return result
        except UnresolvedOperation as exc:
            # The unique constraint rejected the claim before any remote mutation.
            return CartReplacementResult(
                request_id=replacement.request_id, state="rejected", reason=exc.code
            )
        except GatewayError as exc:
            return CartReplacementResult(
                request_id=replacement.request_id, state="rejected", reason=exc.error.code
            )

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, open_world_hint=True
        )
    )
    @handle_database_errors
    async def prepare_account_cart_change(
        session_token: str,
        change: CartChange,
        service: AccountService = Depends(from_dishka(AccountService)),
    ) -> CartPlan:
        """Trusted backend: prepare a five-minute token-account cart plan; no remote mutation.

        add needs product_id + offer_id; update needs cart_item_id + final quantity;
        remove needs cart_item_id. Host must present and approve the exact plan before execution.
        Amount/unit caps apply. Token stays private to the host; never falls back to keyring.
        """
        try:
            return await service.cart(session_token).prepare(change)
        except GatewayError as exc:
            raise ValueError(exc.error.code) from None

    write = ToolAnnotations(
        read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True
    )

    async def execute(service: AccountService, token: str, plan_id: str, action):
        try:
            return await service.cart(token).execute(plan_id, action)
        except GatewayError as exc:
            raise ValueError(exc.error.code) from None

    @server.tool(annotations=write)
    @handle_database_errors
    async def add_to_account_cart(
        session_token: str,
        plan_id: PlanId,
        service: AccountService = Depends(from_dishka(AccountService)),
    ) -> CartOperation:
        """Trusted backend: POST one unit with an approved add plan belonging to this account.

        Requires explicit host approval. Revalidates price, stock and caps; never resends a plan.
        """
        return await execute(service, session_token, plan_id, "add")

    @server.tool(annotations=write)
    @handle_database_errors
    async def update_account_cart_item(
        session_token: str,
        plan_id: PlanId,
        service: AccountService = Depends(from_dishka(AccountService)),
    ) -> CartOperation:
        """Trusted backend: PATCH final quantity with this account's approved update plan.

        Requires explicit host approval. Increases obey both caps; retries never resend mutations.
        """
        return await execute(service, session_token, plan_id, "update")

    @server.tool(annotations=write)
    @handle_database_errors
    async def remove_from_account_cart(
        session_token: str,
        plan_id: PlanId,
        service: AccountService = Depends(from_dishka(AccountService)),
    ) -> CartOperation:
        """Trusted backend: DELETE the exact item using this account's approved remove plan.

        Requires explicit host approval. No checkout or payment. Retries never resend mutations.
        """
        return await execute(service, session_token, plan_id, "remove")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    @handle_database_errors
    async def get_account_cart_operation(
        session_token: str,
        operation_id: PlanId,
        service: AccountService = Depends(from_dishka(AccountService)),
    ) -> CartOperationStatus:
        """Trusted backend: inspect a plan/replacement belonging to this token's account.

        For replacement operations use request_id as 32 lowercase hex digits without hyphens.
        """
        try:
            return await inspect_operation(service.cart(session_token), operation_id)
        except GatewayError as exc:
            raise ValueError(exc.error.code) from None

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, open_world_hint=True
        )
    )
    @handle_database_errors
    async def reconcile_account_cart_operation(
        session_token: str,
        operation_id: PlanId,
        service: AccountService = Depends(from_dishka(AccountService)),
    ) -> CartOperationStatus:
        """Trusted backend: read back an uncertain account operation and update its journal.

        Never mutates the remote cart or steals an executing operation. An executing record
        needs operator investigation; a matching current cart confirms state, not causal proof.
        """
        try:
            return await inspect_operation(
                service.cart(session_token), operation_id, reconcile=True
            )
        except GatewayError as exc:
            raise ValueError(exc.error.code) from None
