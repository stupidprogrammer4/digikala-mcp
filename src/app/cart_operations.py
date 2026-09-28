"""Inspect and reconcile journaled operations without replaying remote mutations."""

from datetime import datetime, timezone
from uuid import UUID

from src.app.cart import CartService, exceeds
from src.app.replacement import CartReplacer
from src.infra.http import GatewayError
from src.models.schemas.account import CartReplacement, CartReplacementResult
from src.models.schemas.cart import CartLimits, CartOperation, CartPlan
from src.models.schemas.cart_status import CartOperationStatus


def operation_status(record: dict) -> CartOperationStatus:
    replacement = "fingerprint" in record
    plan = None if replacement else CartPlan.model_validate(record["plan"])
    raw = record.get("result")
    result = (
        None
        if raw is None
        else (
            CartReplacementResult.model_validate(raw)
            if replacement
            else CartOperation.model_validate(raw)
        )
    )
    return CartOperationStatus(
        operation_id=record["plan"]["plan_id"],
        kind="replacement" if replacement else "change",
        state=record["state"],
        plan=plan,
        result=result,
        expired=bool(
            plan and record["state"] == "prepared" and plan.expires_at <= datetime.now(timezone.utc)
        ),
        reason="execution_in_progress_or_interrupted" if record["state"] == "executing" else None,
    )


async def inspect_operation(
    service: CartService, operation_id: str, *, reconcile: bool = False
) -> CartOperationStatus:
    async with service.gateway_factory() as gateway:
        async with service.journal() as journal:
            record = await journal.get(operation_id)
        if record["connection_id"] != gateway.connection_id:
            raise GatewayError("plan_not_found", "Operation not found for this account")
        # Executing ownership cannot be stolen: another worker may still send its mutation.
        if not reconcile or record["state"] != "uncertain":
            return operation_status(record)
        if "fingerprint" not in record:
            result = await service._reconcile(gateway, record)
            await service._finish(record, result)
        else:
            # Older replacement records have no expected selection, so cannot be inferred.
            if "request" not in record:
                status = operation_status(record)
                status.reason = "legacy_operation_requires_manual_review"
                return status
            request = CartReplacement.model_validate(record["request"])
            try:
                after = await gateway.read()
            except GatewayError:
                status = operation_status(record)
                status.reason = "read_back_failed"
                return status
            limits = CartLimits.model_validate(record["limits"]) if record.get("limits") else None
            matched = CartReplacer.matches(request, after)
            breached = exceeds(after, limits)
            replacement_result = CartReplacementResult(
                request_id=UUID(hex=operation_id),
                state="applied" if matched else "uncertain",
                cart=after,
                reason=("limits_exceeded_after_write" if breached else None)
                if matched
                else "cart_does_not_match_expected_selection",
            )
            record["result"] = replacement_result.model_dump(mode="json", round_trip=True)
            async with service.journal() as journal:
                await journal.transition(record, "uncertain", replacement_result.state)
        async with service.journal() as journal:
            return operation_status(await journal.get(operation_id))
