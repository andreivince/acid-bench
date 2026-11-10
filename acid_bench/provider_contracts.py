from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class ProviderContract:
    provider: str
    display_name: str
    provider_docs_domains: tuple[str, ...]
    runtime_docs: tuple[str, ...]
    usage_docs: tuple[str, ...]
    pricing_docs: tuple[str, ...]
    cost_strategy: str
    model_owner_required: bool
    notes: str


PROVIDER_CONTRACTS: dict[str, ProviderContract] = {
    "deepseek": ProviderContract(
        provider="deepseek",
        display_name="DeepSeek",
        provider_docs_domains=("api-docs.deepseek.com",),
        runtime_docs=(
            "https://api-docs.deepseek.com/guides/thinking_mode",
            "https://api-docs.deepseek.com/guides/tool_calls",
        ),
        usage_docs=(
            "https://api-docs.deepseek.com/quick_start/pricing/",
            "https://api-docs.deepseek.com/guides/kv_cache",
        ),
        pricing_docs=("https://api-docs.deepseek.com/quick_start/pricing/",),
        cost_strategy="deepseek_token_usage_estimate",
        model_owner_required=False,
        notes=(
            "DeepSeek is a direct provider. Runtime options, pricing, cache-hit/cache-miss "
            "usage fields, and strict-tool requirements must come from DeepSeek docs."
        ),
    ),
    "openrouter": ProviderContract(
        provider="openrouter",
        display_name="OpenRouter",
        provider_docs_domains=("openrouter.ai",),
        runtime_docs=(
            "https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request",
        ),
        usage_docs=(
            "https://openrouter.ai/docs/cookbook/administration/usage-accounting",
            "https://openrouter.ai/docs/api/api-reference/generations/get-generation",
        ),
        pricing_docs=("https://openrouter.ai/api/v1/models",),
        cost_strategy="openrouter_provider_usage_cost",
        model_owner_required=True,
        notes=(
            "OpenRouter is the provider/router and billing surface. The model id can point "
            "to Gemini, Claude, Qwen, DeepSeek, or another upstream model owner, but ACID "
            "provider docs and cost evidence must still be OpenRouter docs/evidence."
        ),
    ),
}


def provider_contract(provider: str) -> ProviderContract:
    key = provider.lower()
    try:
        return PROVIDER_CONTRACTS[key]
    except KeyError as exc:
        known = ", ".join(sorted(PROVIDER_CONTRACTS))
        raise ValueError(f"unknown provider {provider!r}; known providers: {known}") from exc


def url_domain(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc.lower()


def _domains_for(urls: Any) -> set[str]:
    if not isinstance(urls, list):
        return set()
    domains: set[str] = set()
    for url in urls:
        if isinstance(url, str) and url:
            domain = url_domain(url)
            if domain:
                domains.add(domain)
    return domains


def _source_domains(model: dict[str, Any]) -> set[str]:
    domains = set()
    source = model.get("source")
    if isinstance(source, str) and source:
        domains.add(url_domain(source))
    domains.update(_domains_for(model.get("source_evidence")))
    return domains


def infer_model_owner(model_id: str) -> str | None:
    if "/" not in model_id:
        return None
    owner = model_id.split("/", 1)[0].strip().lower()
    return owner or None


def provider_contract_metadata(model: dict[str, Any]) -> dict[str, Any]:
    provider = str(model.get("provider") or "").lower()
    try:
        contract = provider_contract(provider)
    except ValueError:
        return {
            "provider": provider or "unknown",
            "provider_display_name": provider or "unknown",
            "provider_cost_strategy": model.get("provider_cost_strategy", "unverified"),
            "provider_runtime_docs": [],
            "provider_usage_docs": [],
            "provider_pricing_docs": [],
            "model_owner": model.get("model_owner") or infer_model_owner(str(model.get("name") or "")),
            "model_owner_docs": model.get("model_owner_docs", []),
        }
    model_id = str(model.get("name") or "")
    return {
        "provider": contract.provider,
        "provider_display_name": contract.display_name,
        "provider_cost_strategy": contract.cost_strategy,
        "provider_runtime_docs": list(contract.runtime_docs),
        "provider_usage_docs": list(contract.usage_docs),
        "provider_pricing_docs": list(contract.pricing_docs),
        "model_owner": model.get("model_owner") or infer_model_owner(model_id),
        "model_owner_docs": model.get("model_owner_docs", []),
    }


def validate_provider_contract(model: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    provider = model.get("provider")
    if not isinstance(provider, str) or not provider:
        return ["provider is required"]

    try:
        contract = provider_contract(provider)
    except ValueError as exc:
        return [str(exc)]

    source_domains = _source_domains(model)
    provider_domain_set = set(contract.provider_docs_domains)
    provider_doc_domains = source_domains & provider_domain_set
    non_provider_doc_domains = source_domains - provider_domain_set

    if not provider_doc_domains:
        errors.append(
            f"{contract.provider} config must cite provider docs from "
            f"{', '.join(contract.provider_docs_domains)}"
        )

    if contract.provider == "deepseek":
        api_base = (
            model.get("provider_options", {})
            if isinstance(model.get("provider_options"), dict)
            else {}
        ).get("api_base")
        if model.get("strict_tools") is True and api_base != "https://api.deepseek.com/beta":
            errors.append("DeepSeek strict-tools configs must use https://api.deepseek.com/beta")
        if str(model.get("name") or "").startswith("google/"):
            errors.append("DeepSeek provider config cannot use a Google/OpenRouter-style model id")

    if contract.provider == "openrouter":
        model_owner = model.get("model_owner") or infer_model_owner(str(model.get("name") or ""))
        if not model_owner:
            errors.append("OpenRouter configs must record the upstream model owner")
        if not str(model.get("name") or "").startswith(f"{model_owner}/"):
            errors.append("OpenRouter model ids should preserve the upstream owner namespace")
        owner_docs = model.get("model_owner_docs")
        if owner_docs is not None and not isinstance(owner_docs, list):
            errors.append("model_owner_docs must be a list when present")
        if non_provider_doc_domains and not owner_docs:
            errors.append(
                "Non-OpenRouter docs belong in model_owner_docs, not provider source_evidence"
            )

    declared_strategy = model.get("provider_cost_strategy")
    if declared_strategy is not None and declared_strategy != contract.cost_strategy:
        errors.append(
            f"provider_cost_strategy for {contract.provider} must be {contract.cost_strategy}"
        )

    return errors


def validate_provider_contracts(models: list[dict[str, Any]]) -> None:
    problems = []
    for index, model in enumerate(models):
        label = model.get("name", f"model[{index}]")
        for error in validate_provider_contract(model):
            problems.append(f"{label}: {error}")
    if problems:
        raise ValueError("provider contract validation failed:\n" + "\n".join(problems))
