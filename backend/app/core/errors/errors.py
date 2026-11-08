"""Framework-free error framework: coded exceptions + a catalog.

CodedError is a plain Exception (no FastAPI import). The app layer
(backend/app/server/middleware/errors.py) renders it into a JSON envelope.
"""

from __future__ import annotations

from backend.app.core.errors import error_codes


class CodedError(Exception):
    """A domain error carrying a stable code and rendered display strings."""

    def __init__(
        self,
        *,
        code: str,
        status: int,
        description: str,
        resolution: str,
        resolved_by_refresh: bool,
        data: dict | None = None,
    ) -> None:
        self.code = code
        self.status = status
        self.description = description
        self.resolution = resolution
        self.resolved_by_refresh = resolved_by_refresh
        # Optional machine-readable payload for the errors whose resolution
        # needs one -- a write conflict must hand back the server's current
        # document so the form can show the difference instead of discarding
        # what the user typed. Rendered under `data` only when present, so
        # every existing error's envelope is unchanged.
        self.data = data
        super().__init__(f"{code}: {description}")


class ErrorModel:
    """A reusable error definition: code + display metadata + bound HTTP status."""

    def __init__(self, meta: dict, *, code: str, status: int) -> None:
        self._meta = meta
        self.code = code
        self.status = status

    @classmethod
    def initialize(cls, meta: dict, *, code: str, status: int) -> "ErrorModel":
        return cls(meta, code=code, status=status)

    def exc(
        self, entity_specific_params: dict | None = None, *, data: dict | None = None
    ) -> CodedError:
        params = entity_specific_params or {}
        return CodedError(
            code=self.code,
            status=self.status,
            description=self._meta["description"].format(**params),
            resolution=self._meta["resolution"].format(**params),
            resolved_by_refresh=self._meta["resolvedByRefresh"],
            data=data,
        )


# --- Catalog -----------------------------------------------------------------

UNEXPECTED_ERROR = ErrorModel.initialize(error_codes.E0000, code="E0000", status=500)
UPLOAD_MISSING_FILENAME = ErrorModel.initialize(
    error_codes.E1001, code="E1001", status=422
)
HEADER_ROW_NOT_DETECTED = ErrorModel.initialize(
    error_codes.E1002, code="E1002", status=422
)
UNRESOLVED_COLUMNS = ErrorModel.initialize(error_codes.E1003, code="E1003", status=422)
UPLOAD_NOT_FOUND = ErrorModel.initialize(error_codes.E1004, code="E1004", status=404)
TEMPLATE_NOT_FOUND = ErrorModel.initialize(error_codes.E1005, code="E1005", status=404)
UNKNOWN_BATCH = ErrorModel.initialize(error_codes.E1006, code="E1006", status=404)
UNKNOWN_CANDIDATE = ErrorModel.initialize(error_codes.E1007, code="E1007", status=404)
SPLITS_DO_NOT_SUM = ErrorModel.initialize(error_codes.E1008, code="E1008", status=422)
SPLIT_MISSING_FIELDS = ErrorModel.initialize(
    error_codes.E1009, code="E1009", status=422
)
UNRESOLVED_ACCOUNTS = ErrorModel.initialize(error_codes.E1010, code="E1010", status=422)
SCHEME_MASTER_MISSING = ErrorModel.initialize(
    error_codes.E1011, code="E1011", status=422
)
UNRESOLVED_SCHEMES = ErrorModel.initialize(error_codes.E1012, code="E1012", status=422)
UNCOVERED_REDEMPTION = ErrorModel.initialize(
    error_codes.E1013, code="E1013", status=422
)
NAV_STORE_CORRUPT = ErrorModel.initialize(error_codes.E1014, code="E1014", status=422)
EMPTY_DUMP_WHILE_HOLDING = ErrorModel.initialize(
    error_codes.E1015, code="E1015", status=422
)
INVESTMENT_VALUATION_INCONSISTENT = ErrorModel.initialize(
    error_codes.E1016, code="E1016", status=422
)
INVALID_ACCOUNT_NAME = ErrorModel.initialize(
    error_codes.E1017, code="E1017", status=422
)
ACCOUNT_NAME_COLLISION = ErrorModel.initialize(
    error_codes.E1018, code="E1018", status=409
)
ACCOUNT_NOT_SETTLED = ErrorModel.initialize(error_codes.E1019, code="E1019", status=409)
LEDGER_MAP_INCOMPLETE = ErrorModel.initialize(
    error_codes.E1020, code="E1020", status=422
)
LEDGER_MAP_ILLEGAL_TARGET = ErrorModel.initialize(
    error_codes.E1021, code="E1021", status=422
)
LEDGER_UNBOOKABLE_SELL = ErrorModel.initialize(
    error_codes.E1022, code="E1022", status=422
)
LEDGER_IMBALANCE = ErrorModel.initialize(error_codes.E1023, code="E1023", status=422)
CHARGE_VIOLATION = ErrorModel.initialize(error_codes.E1024, code="E1024", status=422)
LEDGER_EDIT_ILLEGAL_ACCOUNT = ErrorModel.initialize(
    error_codes.E1025, code="E1025", status=422
)
MFAPI_UNAVAILABLE = ErrorModel.initialize(error_codes.E1030, code="E1030", status=502)
MFAPI_BAD_RESPONSE = ErrorModel.initialize(error_codes.E1031, code="E1031", status=502)
TRANSACTION_NOT_FOUND = ErrorModel.initialize(
    error_codes.E1032, code="E1032", status=404
)
EXPECTED_HASH_REQUIRED = ErrorModel.initialize(
    error_codes.E1033, code="E1033", status=422
)
POSTING_NOT_FOUND = ErrorModel.initialize(error_codes.E1034, code="E1034", status=404)
HASH_MISMATCH = ErrorModel.initialize(error_codes.E1035, code="E1035", status=409)
IMPORT_CLASSIFY_INVALID_FORCE_KIND = ErrorModel.initialize(
    error_codes.E1036, code="E1036", status=422
)
REVIEW_EDIT_INVALID = ErrorModel.initialize(error_codes.E1037, code="E1037", status=422)
CATEGORIZE_IMPORT_INVALID = ErrorModel.initialize(
    error_codes.E1038, code="E1038", status=422
)
ACCOUNT_NOT_OPEN = ErrorModel.initialize(error_codes.E1039, code="E1039", status=404)
TEMPLATE_FINGERPRINT_MISMATCH = ErrorModel.initialize(
    error_codes.E1040, code="E1040", status=422
)
PNL_FYS_INVALID = ErrorModel.initialize(error_codes.E1041, code="E1041", status=422)
EQUITY_QUOTES_EMPTY_WHILE_HOLDING = ErrorModel.initialize(
    error_codes.E1042, code="E1042", status=422
)
UNRECONCILED_STOCK_ORDER = ErrorModel.initialize(
    error_codes.E1043, code="E1043", status=422
)
LOT_NOT_BOOKABLE = ErrorModel.initialize(error_codes.E1044, code="E1044", status=422)
ORDER_RESTATED = ErrorModel.initialize(error_codes.E1045, code="E1045", status=422)
UNSTAMPED_STOCK_HISTORY = ErrorModel.initialize(
    error_codes.E1046, code="E1046", status=422
)
ENTRY_NOT_LOCATABLE = ErrorModel.initialize(error_codes.E1047, code="E1047", status=409)
CLASSIFY_TARGET_NOT_EXPENSE = ErrorModel.initialize(
    error_codes.E1048, code="E1048", status=422
)
CLASSIFY_TARGET_IS_BACKLOG = ErrorModel.initialize(
    error_codes.E1049, code="E1049", status=422
)
PROMPT_FILE_MISSING = ErrorModel.initialize(error_codes.E1050, code="E1050", status=422)
DECLARED_INCOME_VALUE_INVALID = ErrorModel.initialize(
    error_codes.E1051, code="E1051", status=422
)
TEMPLATE_SIGN_COLUMN_REQUIRED = ErrorModel.initialize(
    error_codes.E1052, code="E1052", status=422
)
TEMPLATE_DATE_FORMAT_REQUIRED = ErrorModel.initialize(
    error_codes.E1053, code="E1053", status=422
)
TAX_RULES_FILE_INVALID = ErrorModel.initialize(
    error_codes.E1054, code="E1054", status=422
)
DECLARED_DEDUCTION_VALUE_INVALID = ErrorModel.initialize(
    error_codes.E1055, code="E1055", status=422
)
TAX_INPUTS_VALUE_INVALID = ErrorModel.initialize(
    error_codes.E1056, code="E1056", status=422
)
TAX_INPUTS_UNKNOWN_KEY = ErrorModel.initialize(
    error_codes.E1057, code="E1057", status=422
)
TAX_INPUTS_REVISION_MISMATCH = ErrorModel.initialize(
    error_codes.E1058, code="E1058", status=409
)
TAX_INPUTS_FILE_UNPARSEABLE = ErrorModel.initialize(
    error_codes.E1059, code="E1059", status=422
)
PREPAID_TAX_COMPONENT_UNMAPPED = ErrorModel.initialize(
    error_codes.E1060, code="E1060", status=500
)
TAX_FY_META_INVALID = ErrorModel.initialize(error_codes.E1061, code="E1061", status=422)
AGENT_UPLOAD_CHANGED = ErrorModel.initialize(
    error_codes.E1074, code="E1074", status=409
)
AGENT_UNKNOWN_RUN = ErrorModel.initialize(error_codes.E1075, code="E1075", status=404)
AGENT_ATTEMPTS_EXHAUSTED = ErrorModel.initialize(
    error_codes.E1076, code="E1076", status=422
)
AGENT_UNKNOWN_ACCOUNT = ErrorModel.initialize(
    error_codes.E1077, code="E1077", status=422
)
AGENT_KIND_UNRESOLVED = ErrorModel.initialize(
    error_codes.E1078, code="E1078", status=422
)
AGENT_KIND_MISMATCH = ErrorModel.initialize(error_codes.E1079, code="E1079", status=422)
AGENT_INVESTMENT_UNSUPPORTED = ErrorModel.initialize(
    error_codes.E1080, code="E1080", status=422
)
AGENT_VALIDATION_FAILED = ErrorModel.initialize(
    error_codes.E1081, code="E1081", status=422
)
AGENT_NO_PASSING_PROOF = ErrorModel.initialize(
    error_codes.E1062, code="E1062", status=409
)
AGENT_NOT_MATCHED = ErrorModel.initialize(error_codes.E1063, code="E1063", status=409)
AGENT_TEMPLATE_EXISTS = ErrorModel.initialize(
    error_codes.E1064, code="E1064", status=409
)
AGENT_DEDUP_CHANGED = ErrorModel.initialize(error_codes.E1065, code="E1065", status=409)
AGENT_NO_RESULT = ErrorModel.initialize(error_codes.E1066, code="E1066", status=404)
AGENT_UPGRADE_MISMATCH = ErrorModel.initialize(
    error_codes.E1067, code="E1067", status=422
)
PDF_PASSWORD_WRONG = ErrorModel.initialize(error_codes.E1068, code="E1068", status=422)
PDF_UNREADABLE = ErrorModel.initialize(error_codes.E1069, code="E1069", status=422)
PDF_STATEMENT_MISMATCH = ErrorModel.initialize(
    error_codes.E1070, code="E1070", status=422
)
PDF_ACCOUNT_HAS_OTHER_CARD = ErrorModel.initialize(
    error_codes.E1071, code="E1071", status=409
)
INVALID_TAG = ErrorModel.initialize(error_codes.E1072, code="E1072", status=422)
RULES_NOT_SAVED = ErrorModel.initialize(error_codes.E1073, code="E1073", status=500)
ACCOUNT_PROPERTY_INVALID = ErrorModel.initialize(
    error_codes.E1082, code="E1082", status=422
)
ACCOUNT_PROPERTY_NOT_APPLICABLE = ErrorModel.initialize(
    error_codes.E1083, code="E1083", status=422
)
ACCOUNT_CLOSED = ErrorModel.initialize(error_codes.E1084, code="E1084", status=409)
EMI_SPLIT_STALE = ErrorModel.initialize(error_codes.E1085, code="E1085", status=409)
EMI_SPLIT_INVALID = ErrorModel.initialize(error_codes.E1086, code="E1086", status=422)
PERIOD_RANGE_INVALID = ErrorModel.initialize(
    error_codes.E1087, code="E1087", status=422
)
OPENING_EXISTS = ErrorModel.initialize(error_codes.E1088, code="E1088", status=409)
OPENING_DATE_INVALID = ErrorModel.initialize(
    error_codes.E1089, code="E1089", status=422
)
OPENING_NOT_APPLICABLE = ErrorModel.initialize(
    error_codes.E1090, code="E1090", status=422
)
OPENING_STALE = ErrorModel.initialize(error_codes.E1091, code="E1091", status=409)
OPENING_LOT_INVALID = ErrorModel.initialize(error_codes.E1092, code="E1092", status=422)
OPENING_AMOUNT_INVALID = ErrorModel.initialize(
    error_codes.E1093, code="E1093", status=422
)
ACCOUNT_RENAME_BREAKS_TEMPLATE = ErrorModel.initialize(
    error_codes.E1094, code="E1094", status=409
)
ACCOUNT_RENAME_PENDING_MERGE = ErrorModel.initialize(
    error_codes.E1095, code="E1095", status=409
)
BENCHMARK_UNKNOWN = ErrorModel.initialize(error_codes.E1096, code="E1096", status=422)
BENCHMARKS_FILE_INVALID = ErrorModel.initialize(
    error_codes.E1097, code="E1097", status=422
)
ROWS_SELECTOR_INVALID = ErrorModel.initialize(
    error_codes.E1098, code="E1098", status=422
)
