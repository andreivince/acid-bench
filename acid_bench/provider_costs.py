from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .provider_contracts import provider_contract

DEEPSEEK_PRICE_PROFILES: dict[str, dict[str, Any]] = {
    "deepseek-v4-flash": {
        "input_cache_hit_per_1m": 0.0028,
        "input_cache_miss_per_1m": 0.14,
        "output_per_1m": 0.28,
        "source": "https://api-docs.deepseek.com/quick_start/pricing/",
        "source_checked_at": "2026-05-27",
    },
    "deepseek-v4-pro": {
        "input_cache_hit_per_1m": 0.003625,
        "input_cache_miss_per_1m": 0.435,
        "output_per_1m": 0.87,
        "source": "https://api-docs.deepseek.com/quick_start/pricing/",
        "source_checked_at": "2026-05-29",
    }
}

USAGE_RECORD_KEYS = ("agent_usage_records", "user_usage_records")


def numeric(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def jsonable_response_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return jsonable_response_value(value.model_dump())
    if isinstance(value, dict):
        return {str(k): jsonable_response_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable_response_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "__dict__"):
        return {
            str(k): jsonable_response_value(v)
            for k, v in vars(value).items()
            if not k.startswith("_")
        }
    return str(value)


def response_usage(res: Any) -> dict[str, Any] | None:
    usage = getattr(res, "usage", None)
    if usage is None and isinstance(res, dict):
        usage = res.get("usage")
    usage = jsonable_response_value(usage)
    return usage if isinstance(usage, dict) else None


def _hidden_params(res: Any) -> dict[str, Any]:
    hidden = getattr(res, "_hidden_params", None)
    if hidden is None and isinstance(res, dict):
        hidden = res.get("_hidden_params")
    return hidden if isinstance(hidden, dict) else {}


def response_cost(res: Any) -> float:
    hidden = _hidden_params(res)
    value = hidden.get("response_cost")
    if value not in (None, 0, 0.0):
        parsed = numeric(value)
        if parsed is not None:
            return parsed
    usage = response_usage(res)
    if isinstance(usage, dict):
        parsed = numeric(usage.get("cost"))
        if parsed is not None:
            return parsed
    return 0.0


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _response_attr(res: Any, name: str) -> Any:
    if isinstance(res, dict):
        return res.get(name)
    return getattr(res, name, None)


def response_usage_record(
    res: Any,
    *,
    role: str,
    provider: str,
    model: str,
) -> dict[str, Any]:
    try:
        provider_cost_strategy = provider_contract(provider).cost_strategy
    except ValueError:
        provider_cost_strategy = "unverified_provider_usage"
    hidden = _hidden_params(res)
    response_id = _first_string(
        _response_attr(res, "id"),
        _response_attr(res, "response_id"),
        hidden.get("id"),
        hidden.get("response_id"),
    )
    generation_id = _first_string(
        _response_attr(res, "generation_id"),
        hidden.get("generation_id"),
        hidden.get("openrouter_generation_id"),
        response_id if provider.lower() == "openrouter" else None,
    )
    provider_response_id = _first_string(
        _response_attr(res, "provider_response_id"),
        _response_attr(res, "upstream_id"),
        hidden.get("provider_response_id"),
        hidden.get("upstream_id"),
    )
    return {
        "role": role,
        "provider": provider,
        "provider_cost_strategy": provider_cost_strategy,
        "model": model,
        "response_id": response_id,
        "generation_id": generation_id,
        "provider_response_id": provider_response_id,
        "response_model": _response_attr(res, "model"),
        "usage": response_usage(res),
        "hidden_response_cost": jsonable_response_value(hidden.get("response_cost")),
    }


def usage_records_from_tau_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    info = row.get("info")
    if not isinstance(info, dict):
        return []
    records: list[dict[str, Any]] = []
    for key in USAGE_RECORD_KEYS:
        value = info.get(key)
        if isinstance(value, list):
            default_role = "agent" if key == "agent_usage_records" else "user_simulator"
            for record in value:
                if not isinstance(record, dict):
                    continue
                normalized = dict(record)
                normalized.setdefault("role", default_role)
                records.append(normalized)
    return records


def usage_record_cost(record: dict[str, Any]) -> float | None:
    usage = record.get("usage")
    if isinstance(usage, dict):
        cost = numeric(usage.get("cost"))
        if cost is not None:
            return cost
    hidden_response_cost = numeric(record.get("hidden_response_cost"))
    if hidden_response_cost is not None and hidden_response_cost > 0.0:
        return hidden_response_cost
    return None


@dataclass(frozen=True)
class CostEvidence:
    provider_total_usd: float | None
    provenance: str
    cost_label: str
    fields: dict[str, Any]


@dataclass(frozen=True)
class UsageCompleteness:
    records: list[dict[str, Any]]
    fields: dict[str, Any]
    complete: bool


def _info(row: dict[str, Any]) -> dict[str, Any]:
    info = row.get("info")
    return info if isinstance(info, dict) else {}


def _row_represents_agent_attempt(row: dict[str, Any]) -> bool:
    return any(key in row for key in ("traj", "reward", "trial", "total_cost", "task_id"))


def _row_has_user_simulator_evidence(row: dict[str, Any]) -> bool:
    info = _info(row)
    raw_user_records = info.get("user_usage_records")
    traj = row.get("traj")
    has_traj_users = (
        isinstance(traj, list)
        and any(
            isinstance(message, dict) and message.get("role") == "user"
            for message in traj
        )
    )
    user_cost = info.get("user_cost")
    has_nonzero_user_cost = isinstance(user_cost, (int, float)) and user_cost > 0
    has_nonempty_user_records = isinstance(raw_user_records, list) and len(raw_user_records) > 0
    if isinstance(raw_user_records, list) and not raw_user_records and user_cost == 0:
        return False
    if has_traj_users or has_nonzero_user_cost or has_nonempty_user_records:
        return True
    if "user_cost" in info or "user_usage_records" in info:
        return True
    return False


def usage_completeness_from_tau_rows(rows: list[dict[str, Any]]) -> UsageCompleteness:
    records: list[dict[str, Any]] = []
    expected_agent_rows = 0
    expected_user_rows = 0
    missing_agent_rows = 0
    missing_user_rows = 0
    priced_records = 0
    unpriced_records = 0

    for row in rows:
        info = _info(row)
        raw_agent_records = info.get("agent_usage_records")
        raw_user_records = info.get("user_usage_records")
        agent_records = [
            record
            for record in (raw_agent_records if isinstance(raw_agent_records, list) else [])
            if isinstance(record, dict)
        ]
        user_records = [
            record
            for record in (raw_user_records if isinstance(raw_user_records, list) else [])
            if isinstance(record, dict)
        ]
        if _row_represents_agent_attempt(row):
            expected_agent_rows += 1
            if not agent_records:
                missing_agent_rows += 1
        if _row_has_user_simulator_evidence(row):
            expected_user_rows += 1
            if not user_records:
                missing_user_rows += 1

        for record in usage_records_from_tau_row(row):
            records.append(record)
            if usage_record_cost(record) is None:
                unpriced_records += 1
            else:
                priced_records += 1

    expected_records = expected_agent_rows + expected_user_rows
    missing_records = missing_agent_rows + missing_user_rows
    complete = expected_records > 0 and missing_records == 0
    return UsageCompleteness(
        records=records,
        complete=complete,
        fields={
            "usage_expected_response_records": expected_records,
            "usage_present_response_records": len(records),
            "usage_priced_response_records": priced_records,
            "usage_unpriced_response_records": unpriced_records,
            "usage_provider_cost_priced_response_records": priced_records,
            "usage_provider_cost_unpriced_response_records": unpriced_records,
            "usage_missing_response_records": missing_records,
            "usage_expected_agent_rows": expected_agent_rows,
            "usage_missing_agent_rows": missing_agent_rows,
            "usage_expected_user_rows": expected_user_rows,
            "usage_missing_user_rows": missing_user_rows,
            "usage_completeness_proven": complete,
        },
    )


def _record_ids(records: list[dict[str, Any]]) -> dict[str, Any]:
    response_ids = [record.get("response_id") for record in records if record.get("response_id")]
    generation_ids = []
    for record in records:
        generation_id = record.get("generation_id")
        if not generation_id and str(record.get("provider") or "").lower() == "openrouter":
            generation_id = record.get("response_id")
        if generation_id:
            generation_ids.append(generation_id)
    provider_response_ids = [
        record.get("provider_response_id")
        for record in records
        if record.get("provider_response_id")
    ]
    return {
        "provider_response_records": len(records),
        "response_ids_captured": len(response_ids),
        "generation_ids_captured": len(generation_ids),
        "provider_response_ids_captured": len(provider_response_ids),
    }


def provider_returned_cost_evidence(
    records: list[dict[str, Any]],
    completeness_fields: dict[str, Any] | None = None,
) -> CostEvidence | None:
    if not records:
        return None
    role_totals: dict[str, float] = {}
    total = 0.0
    priced_records = 0
    unpriced_records = 0
    for record in records:
        provider = str(record.get("provider") or "").lower()
        provider_contract(provider)
        cost = usage_record_cost(record)
        if cost is None:
            unpriced_records += 1
            continue
        total += cost
        priced_records += 1
        role = str(record.get("role") or "unknown")
        role_totals[role] = role_totals.get(role, 0.0) + cost
    if priced_records == 0 or unpriced_records:
        return None
    provider_ids = sorted({str(record.get("provider") or "").lower() for record in records})
    if provider_ids == ["openrouter"]:
        cost_source_type = "openrouter_provider_response_usage"
    elif provider_ids == ["deepseek"]:
        cost_source_type = "deepseek_provider_response_usage"
    elif len(provider_ids) > 1:
        cost_source_type = "mixed_provider_response_usage"
    else:
        cost_source_type = "provider_response_usage"
    return CostEvidence(
        provider_total_usd=total,
        provenance="provider_response_usage_cost",
        cost_label=f"${total:.4f} provider response usage cost ({priced_records} response records)",
        fields={
            **_record_ids(records),
            **(
                completeness_fields
                or {
                    "usage_expected_response_records": len(records),
                    "usage_present_response_records": len(records),
                    "usage_missing_response_records": 0,
                }
            ),
            "usage_priced_response_records": priced_records,
            "usage_unpriced_response_records": unpriced_records,
            "cost_evidence_tier": "provider_response_usage",
            "cost_source_type": cost_source_type,
            "publication_grade_cost": False,
            "cost_publication_note": (
                "Complete provider-returned response cost is captured locally, but it is not "
                "publication-grade until reconciled against provider invoice, dashboard, or usage export."
            ),
            "agent_provider_total_usd": role_totals.get("agent", 0.0),
            "user_provider_total_usd": role_totals.get("user_simulator", 0.0),
            "provider_usage_role_totals_usd": role_totals,
            "confidence": (
                "provider-returned response cost captured for every persisted response; "
                "reconcile against provider invoice or usage export before final publication"
            ),
        },
    )


def deepseek_token_cost_evidence(
    records: list[dict[str, Any]],
    completeness_fields: dict[str, Any] | None = None,
) -> CostEvidence | None:
    deepseek_records = [
        record
        for record in records
        if str(record.get("provider") or "").lower() == "deepseek"
    ]
    if not deepseek_records or len(deepseek_records) != len(records):
        return None

    total_hit = 0
    total_miss = 0
    total_output = 0
    role_totals: dict[str, float] = {}
    priced_records = 0
    model_key = None

    for record in deepseek_records:
        provider_contract("deepseek")
        model = str(record.get("model") or "").lower()
        profile_key = next((key for key in DEEPSEEK_PRICE_PROFILES if key in model), None)
        usage = record.get("usage")
        if profile_key is None or not isinstance(usage, dict):
            return None
        hit = numeric(usage.get("prompt_cache_hit_tokens"))
        miss = numeric(usage.get("prompt_cache_miss_tokens"))
        output = numeric(usage.get("completion_tokens"))
        if hit is None or miss is None or output is None:
            return None
        profile = DEEPSEEK_PRICE_PROFILES[profile_key]
        record_total = (
            hit * profile["input_cache_hit_per_1m"]
            + miss * profile["input_cache_miss_per_1m"]
            + output * profile["output_per_1m"]
        ) / 1_000_000
        role = str(record.get("role") or "unknown")
        role_totals[role] = role_totals.get(role, 0.0) + record_total
        model_key = profile_key
        total_hit += int(hit)
        total_miss += int(miss)
        total_output += int(output)
        priced_records += 1

    if model_key is None or priced_records == 0:
        return None
    profile = DEEPSEEK_PRICE_PROFILES[model_key]
    total = sum(role_totals.values())
    return CostEvidence(
        provider_total_usd=total,
        provenance="deepseek_token_usage_estimate",
        cost_label=(
            f"${total:.4f} DeepSeek token-priced cost "
            f"({priced_records} response records; hit={total_hit}, miss={total_miss}, output={total_output})"
        ),
        fields={
            **_record_ids(records),
            "deepseek_pricing_source": profile["source"],
            "deepseek_pricing_source_checked_at": profile["source_checked_at"],
            "deepseek_cache_hit_input_tokens": total_hit,
            "deepseek_cache_miss_input_tokens": total_miss,
            "deepseek_output_tokens": total_output,
            **(
                completeness_fields
                or {
                    "usage_expected_response_records": len(records),
                    "usage_present_response_records": len(records),
                    "usage_missing_response_records": 0,
                }
            ),
            "usage_priced_response_records": priced_records,
            "usage_unpriced_response_records": 0,
            "cost_evidence_tier": "token_usage_estimate",
            "cost_source_type": "deepseek_token_pricing",
            "publication_grade_cost": False,
            "cost_publication_note": (
                "DeepSeek cost is estimated from persisted token usage and documented prices; "
                "it is not publication-grade until reconciled against provider invoice, dashboard, or usage export."
            ),
            "agent_provider_total_usd": role_totals.get("agent", 0.0),
            "user_provider_total_usd": role_totals.get("user_simulator", 0.0),
            "provider_usage_role_totals_usd": role_totals,
            "confidence": (
                "complete persisted DeepSeek cache-hit, cache-miss, and output token fields "
                "priced with documented rates; reconcile with provider invoice for final publication spend"
            ),
        },
    )


def cost_evidence_from_usage_records(records: list[dict[str, Any]]) -> CostEvidence | None:
    provider_cost = provider_returned_cost_evidence(records)
    if provider_cost is not None:
        return provider_cost
    return deepseek_token_cost_evidence(records)


def cost_evidence_from_tau_rows(rows: list[dict[str, Any]]) -> tuple[CostEvidence | None, dict[str, Any]]:
    completeness = usage_completeness_from_tau_rows(rows)
    if not completeness.complete:
        return None, completeness.fields
    provider_cost = provider_returned_cost_evidence(completeness.records, completeness.fields)
    if provider_cost is not None:
        return provider_cost, completeness.fields
    return deepseek_token_cost_evidence(completeness.records, completeness.fields), completeness.fields
