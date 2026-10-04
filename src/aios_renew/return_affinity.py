"""Bounded selector contract. Affinity conveys no semantic or lifecycle authority."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import re

HANDLE_PREFIX = "page-origin-v1:"
HANDLE = re.compile(r"page-origin-v1:[0-9a-f]{64}")
MAX_GENERATION = 2147483647
ORIGIN_AFFINE = "ORIGIN_AFFINE"
LEGACY_REPOSITORY_DEFAULT_ROUTE = "LEGACY_REPOSITORY_DEFAULT_ROUTE"


class AffinityError(ValueError):
    """Content-free contract failure."""


@dataclass(frozen=True)
class LegacyAffinity:
    kind: str = LEGACY_REPOSITORY_DEFAULT_ROUTE


@dataclass(frozen=True)
class OriginAffinity:
    route_handle: str
    generation: int
    kind: str = ORIGIN_AFFINE


ReturnAffinity = LegacyAffinity | OriginAffinity
LEGACY = LegacyAffinity()


def parse_affinity(value, *, historical_missing=False) -> ReturnAffinity:
    if historical_missing:
        return LEGACY
    if isinstance(value, (LegacyAffinity, OriginAffinity)):
        value = asdict(value)
    if not isinstance(value, Mapping):
        raise AffinityError("return_affinity must be a selector mapping")
    if value == {"kind": LEGACY_REPOSITORY_DEFAULT_ROUTE}:
        return LEGACY
    if (set(value) == {"kind", "route_handle", "generation"}
            and value["kind"] == ORIGIN_AFFINE
            and type(value["route_handle"]) is str
            and HANDLE.fullmatch(value["route_handle"])
            and type(value["generation"]) is int
            and 1 <= value["generation"] <= MAX_GENERATION):
        return OriginAffinity(value["route_handle"], value["generation"])
    raise AffinityError("invalid return_affinity selector")


def document_affinity(document) -> ReturnAffinity:
    if not isinstance(document, Mapping):
        raise AffinityError("affinity carrier must be a mapping")
    return parse_affinity(document.get("return_affinity"),
                          historical_missing="return_affinity" not in document)


def require_same_affinity(task, run) -> None:
    if parse_affinity(task.return_affinity) != parse_affinity(run.return_affinity):
        raise AffinityError("RUN return_affinity conflicts with TASK")


def require_authored_affinity(document, existing=None) -> ReturnAffinity:
    """Published-code cutover: new authoring is explicit; transfer is separate."""
    if "return_affinity" not in document:
        raise AffinityError("new TASK/revision requires explicit return_affinity")
    affinity = document_affinity(document)
    if existing is not None and affinity != existing.return_affinity:
        raise AffinityError("TASK revision cannot transfer return_affinity")
    return affinity
