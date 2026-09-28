"""Database failures with stable, public-safe codes; no HTTP or MCP dependencies."""


class DatabaseError(Exception):
    code = "database_error"
    message = "Database operation failed"
    retryable = False

    def __init__(self) -> None:
        super().__init__(self.message)


class DatabaseNotConfigured(DatabaseError):
    code = "database_not_configured"
    message = "Set DATABASE_URL for cart persistence"


class DatabaseConfigurationError(DatabaseError):
    code = "database_configuration_error"
    message = "DATABASE_URL must configure a supported PostgreSQL connection"


class DatabaseUnavailable(DatabaseError):
    code = "database_unavailable"
    message = "Cart database unavailable or schema not initialized"
    retryable = True


class DatabaseClosed(DatabaseError):
    code = "database_closed"
    message = "Database resource has been closed"


class DatabaseIntegrityError(DatabaseError):
    code = "database_integrity_error"
    message = "Database record violates an integrity constraint"

    def __init__(self, constraint: str | None = None, sqlstate: str | None = None) -> None:
        super().__init__()
        self.constraint = constraint
        self.sqlstate = sqlstate


class OperationConflict(DatabaseError):
    code = "request_conflict"
    message = "Operation ID already exists"


class UnresolvedOperation(DatabaseError):
    code = "cart_outcome_unknown"
    message = "Resolve the previous operation first"


class OperationNotFound(DatabaseError):
    code = "plan_not_found"
    message = "Prepare a cart change first"
