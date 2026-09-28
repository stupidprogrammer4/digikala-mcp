"""Translate driver failures once, without exposing SQL or credentials."""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src.infra.db.exceptions import (
    DatabaseIntegrityError,
    DatabaseUnavailable,
    OperationConflict,
    UnresolvedOperation,
)


@contextmanager
def database_errors() -> Generator[None, None, None]:
    """Never expose driver messages, SQL statements, parameters or connection credentials."""
    try:
        yield
    except IntegrityError as exc:
        diagnostic = getattr(exc.orig, "diag", None)
        raise DatabaseIntegrityError(
            constraint=getattr(diagnostic, "constraint_name", None),
            sqlstate=getattr(exc.orig, "sqlstate", None),
        ) from None
    except SQLAlchemyError:
        raise DatabaseUnavailable() from None


@contextmanager
def cart_conflicts() -> Generator[None, None, None]:
    """Translate only recognized cart constraints, including commit-time failures."""
    try:
        yield
    except DatabaseIntegrityError as exc:
        if exc.sqlstate == "23505":
            if exc.constraint == "cart_one_unresolved_operation":
                raise UnresolvedOperation() from None
            if exc.constraint == "cart_operations_pkey":
                raise OperationConflict() from None
        raise
