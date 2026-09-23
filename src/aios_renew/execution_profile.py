"""Repository-owned execution-profile foundation for managed native executors."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode, Node

MANAGED_EXECUTORS = frozenset({"codex", "antigravity"})
MODEL_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}$")
POLICY_FORMAT = "AIOS_EXECUTOR_PROFILES_POLICY"
POLICY_VERSION = 1
EXECUTION_PROFILE_FORMAT = "AIOS_EXECUTION_PROFILE"
EXECUTION_PROFILE_VERSION = 1

POLICY_ALLOWED_FIELDS = frozenset({"format", "version", "executors"})
EXECUTOR_SPEC_ALLOWED_FIELDS = frozenset({
    "default_model",
    "default_reasoning_effort",
    "supported_reasoning_efforts",
})
EXECUTION_PROFILE_ALLOWED_FIELDS = frozenset({
    "format",
    "version",
    "run_id",
    "executor",
    "model",
    "reasoning_effort",
    "model_source",
    "effort_source",
})
ALLOWED_SOURCE_ATTRIBUTIONS = frozenset({"REPOSITORY_DEFAULT", "EXPLICIT"})
CANONICAL_SOURCE_ATTRIBUTIONS = ALLOWED_SOURCE_ATTRIBUTIONS


class ExecutionProfileError(RuntimeError):
    """Base error for execution-profile policy, resolution, and persistence failures."""


class ExecutionProfileValidationError(ExecutionProfileError):
    """Raised when profile policy, model identifier, or profile data is malformed."""


class ExecutionProfileConflictError(ExecutionProfileError):
    """Raised when an existing persisted execution profile conflicts with an attempted write."""


class UniqueKeySafeLoader(yaml.SafeLoader):
    """YAML safe loader that rejects duplicate mapping keys deterministically."""

    def construct_mapping(self, node: Node, deep: bool = False) -> dict[Any, Any]:
        if isinstance(node, MappingNode):
            self.flatten_mapping(node)
        if not isinstance(node, MappingNode):
            raise ConstructorError(
                None,
                None,
                f"expected a mapping node, but found {node.id}",
                node.start_mark,
            )
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                hash(key)
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found unacceptable key ({exc})",
                    key_node.start_mark,
                ) from exc
            if key in mapping:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key: {key!r}",
                    key_node.start_mark,
                )
            value = self.construct_object(value_node, deep=deep)
            mapping[key] = value
        return mapping


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExecutionProfileValidationError(
                f"duplicate key in execution profile JSON: {key!r}"
            )
        result[key] = value
    return result


def is_profile_managed_executor(executor: str) -> bool:
    """Return True if the executor is profile-managed (Codex or Antigravity)."""
    return executor in MANAGED_EXECUTORS


def is_valid_model_identifier(model: Any) -> bool:
    """Check if model is a non-empty provider-native model identifier within bounds."""
    if not isinstance(model, str) or not model:
        return False
    if len(model) > 128:
        return False
    return bool(MODEL_IDENTIFIER_PATTERN.fullmatch(model))


def validate_model_identifier(model: Any) -> str:
    """Validate opaque provider-native model identifier syntax and bounds."""
    if not is_valid_model_identifier(model):
        raise ExecutionProfileValidationError(
            f"invalid model identifier: {model!r} (must be 1-128 chars matching "
            f"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{{0,127}}$)"
        )
    return model


@dataclass(frozen=True)
class ExecutorProfileSpec:
    """Profile specification for one managed executor in repository policy."""

    default_model: str
    default_reasoning_effort: str
    supported_reasoning_efforts: tuple[str, ...]

    @property
    def default_effort(self) -> str:
        return self.default_reasoning_effort

    def as_dict(self) -> dict[str, Any]:
        return {
            "default_model": self.default_model,
            "default_reasoning_effort": self.default_reasoning_effort,
            "supported_reasoning_efforts": list(self.supported_reasoning_efforts),
        }


@dataclass(frozen=True)
class ExecutionProfilePolicy:
    """Repository-owned execution profile policy document."""

    format: str
    version: int
    executors: Mapping[str, ExecutorProfileSpec]

    def spec_for(self, executor: str) -> ExecutorProfileSpec:
        if executor not in self.executors:
            raise ExecutionProfileValidationError(
                f"unsupported executor in profile policy: {executor!r}"
            )
        return self.executors[executor]

    def default_model(self, executor: str) -> str:
        return self.spec_for(executor).default_model

    def default_reasoning_effort(self, executor: str) -> str:
        return self.spec_for(executor).default_reasoning_effort

    def is_supported_effort(self, executor: str, effort: str) -> bool:
        return effort in self.spec_for(executor).supported_reasoning_efforts

    def validate_profile(
        self, profile: ResolvedExecutionProfile
    ) -> ResolvedExecutionProfile:
        return validate_execution_profile(profile, self)

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "executors": {k: v.as_dict() for k, v in self.executors.items()},
        }


@dataclass(frozen=True)
class ResolvedExecutionProfile:
    """Exact immutable execution profile bound to one admitted native execution."""

    run_id: str
    executor: str
    model: str
    reasoning_effort: str
    model_source: str = "REPOSITORY_DEFAULT"
    effort_source: str = "REPOSITORY_DEFAULT"
    format: str = EXECUTION_PROFILE_FORMAT
    version: int = EXECUTION_PROFILE_VERSION

    def __post_init__(self) -> None:
        if self.format != EXECUTION_PROFILE_FORMAT:
            raise ExecutionProfileValidationError(
                f"execution profile format must be {EXECUTION_PROFILE_FORMAT!r}, got {self.format!r}"
            )
        if self.version != EXECUTION_PROFILE_VERSION or isinstance(self.version, bool):
            raise ExecutionProfileValidationError(
                f"execution profile version must be {EXECUTION_PROFILE_VERSION}, got {self.version!r}"
            )
        if not isinstance(self.run_id, str) or not self.run_id:
            raise ExecutionProfileValidationError("invalid or missing run_id in execution profile")
        if self.executor not in MANAGED_EXECUTORS:
            raise ExecutionProfileValidationError(
                f"unsupported executor in execution profile: {self.executor!r}"
            )
        if not is_valid_model_identifier(self.model):
            raise ExecutionProfileValidationError(
                f"invalid model identifier in execution profile: {self.model!r}"
            )
        if not isinstance(self.reasoning_effort, str) or not self.reasoning_effort:
            raise ExecutionProfileValidationError(
                "invalid or missing reasoning_effort in execution profile"
            )
        if self.model_source not in ALLOWED_SOURCE_ATTRIBUTIONS:
            raise ExecutionProfileValidationError(
                f"invalid or missing model_source in execution profile: {self.model_source!r}"
            )
        if self.effort_source not in ALLOWED_SOURCE_ATTRIBUTIONS:
            raise ExecutionProfileValidationError(
                f"invalid or missing effort_source in execution profile: {self.effort_source!r}"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "run_id": self.run_id,
            "executor": self.executor,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "model_source": self.model_source,
            "effort_source": self.effort_source,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"


def parse_execution_profile_policy(data: Any) -> ExecutionProfilePolicy:
    """Parse and strictly validate execution profile policy configuration."""
    if isinstance(data, (str, bytes)):
        try:
            data = yaml.load(data, Loader=UniqueKeySafeLoader)
        except ExecutionProfileValidationError:
            raise
        except Exception as exc:
            raise ExecutionProfileValidationError(
                f"malformed execution profile policy YAML: {exc}"
            ) from exc

    if not isinstance(data, Mapping):
        raise ExecutionProfileValidationError(
            "execution profile policy document must be a mapping"
        )

    unknown_top = sorted(k for k in data.keys() if k not in POLICY_ALLOWED_FIELDS)
    if unknown_top:
        raise ExecutionProfileValidationError(
            f"execution profile policy contains unknown field(s): {', '.join(repr(k) for k in unknown_top)}"
        )

    doc_format = data.get("format")
    if doc_format != POLICY_FORMAT:
        raise ExecutionProfileValidationError(
            f"execution profile policy format must be {POLICY_FORMAT!r}, got {doc_format!r}"
        )

    version = data.get("version")
    if version != POLICY_VERSION or isinstance(version, bool):
        raise ExecutionProfileValidationError(
            f"execution profile policy version must be {POLICY_VERSION}, got {version!r}"
        )

    executors_raw = data.get("executors")
    if not isinstance(executors_raw, Mapping):
        raise ExecutionProfileValidationError(
            "execution profile policy 'executors' must be a mapping"
        )

    unknown_executors = sorted(k for k in executors_raw.keys() if k not in MANAGED_EXECUTORS)
    if unknown_executors:
        raise ExecutionProfileValidationError(
            f"execution profile policy contains unknown executor(s): {', '.join(repr(k) for k in unknown_executors)}"
        )

    for required_executor in ("codex", "antigravity"):
        if required_executor not in executors_raw:
            raise ExecutionProfileValidationError(
                f"execution profile policy missing required executor {required_executor!r}"
            )

    specs: dict[str, ExecutorProfileSpec] = {}
    for name, spec_data in executors_raw.items():
        if not isinstance(name, str) or not name:
            raise ExecutionProfileValidationError(
                f"invalid executor name in policy: {name!r}"
            )
        if not isinstance(spec_data, Mapping):
            raise ExecutionProfileValidationError(
                f"executor spec for {name!r} must be a mapping"
            )

        unknown_spec_fields = sorted(
            k for k in spec_data.keys() if k not in EXECUTOR_SPEC_ALLOWED_FIELDS
        )
        if unknown_spec_fields:
            raise ExecutionProfileValidationError(
                f"executor {name!r} spec contains unknown field(s): {', '.join(repr(k) for k in unknown_spec_fields)}"
            )

        model = spec_data.get("default_model")
        if not isinstance(model, str) or not is_valid_model_identifier(model):
            raise ExecutionProfileValidationError(
                f"executor {name!r} has invalid default_model: {model!r}"
            )

        effort = spec_data.get("default_reasoning_effort")
        if not isinstance(effort, str) or not effort:
            raise ExecutionProfileValidationError(
                f"executor {name!r} has invalid or missing default_reasoning_effort"
            )

        efforts_raw = spec_data.get("supported_reasoning_efforts")
        if not isinstance(efforts_raw, (list, tuple)) or not efforts_raw:
            raise ExecutionProfileValidationError(
                f"executor {name!r} must define non-empty supported_reasoning_efforts"
            )
        seen_efforts: set[str] = set()
        supported: list[str] = []
        for item in efforts_raw:
            if not isinstance(item, str) or not item:
                raise ExecutionProfileValidationError(
                    f"executor {name!r} has invalid supported reasoning effort: {item!r}"
                )
            if item in seen_efforts:
                raise ExecutionProfileValidationError(
                    f"executor {name!r} supported_reasoning_efforts contains duplicate effort: {item!r}"
                )
            seen_efforts.add(item)
            supported.append(item)

        if effort not in supported:
            raise ExecutionProfileValidationError(
                f"executor {name!r} default effort {effort!r} not in supported efforts {supported!r}"
            )

        specs[name] = ExecutorProfileSpec(
            default_model=model,
            default_reasoning_effort=effort,
            supported_reasoning_efforts=tuple(supported),
        )

    return ExecutionProfilePolicy(
        format=doc_format,
        version=version,
        executors=specs,
    )


def canonical_policy_path(repo: str | Path | None = None) -> Path:
    """Resolve repository-owned execution profile policy path."""
    if repo is not None:
        local_path = Path(repo).resolve() / ".ai" / "executor-profiles.yaml"
        if local_path.is_file():
            return local_path
    pkg_root = Path(__file__).resolve().parents[2]
    canonical = pkg_root / ".ai" / "executor-profiles.yaml"
    if canonical.is_file():
        return canonical
    if repo is not None:
        return Path(repo).resolve() / ".ai" / "executor-profiles.yaml"
    return canonical


def load_execution_profile_policy(
    path_or_repo: str | Path | None = None,
) -> ExecutionProfilePolicy:
    """Load and validate the single repository-owned execution-profile policy."""
    if path_or_repo is None:
        path = canonical_policy_path()
    else:
        candidate = Path(path_or_repo).resolve()
        if candidate.is_dir():
            path = canonical_policy_path(candidate)
        else:
            path = candidate

    if not path.is_file():
        raise ExecutionProfileError(
            f"execution profile policy file not found: {path}"
        )

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        raise ExecutionProfileValidationError(
            f"failed to load execution profile policy {path}: {exc}"
        ) from exc

    return parse_execution_profile_policy(content)


def parse_execution_profile(data: Any) -> ResolvedExecutionProfile:
    """Parse and validate one persisted or transmitted execution profile."""
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data, object_pairs_hook=_reject_duplicate_json_keys)
        except ExecutionProfileValidationError:
            raise
        except Exception as exc:
            raise ExecutionProfileValidationError(
                f"malformed execution profile JSON: {exc}"
            ) from exc

    if not isinstance(data, Mapping):
        raise ExecutionProfileValidationError("execution profile must be a mapping")

    unknown_fields = sorted(
        k for k in data.keys() if k not in EXECUTION_PROFILE_ALLOWED_FIELDS
    )
    if unknown_fields:
        raise ExecutionProfileValidationError(
            f"execution profile contains unknown field(s): {', '.join(repr(k) for k in unknown_fields)}"
        )

    doc_format = data.get("format")
    if doc_format != EXECUTION_PROFILE_FORMAT:
        raise ExecutionProfileValidationError(
            f"execution profile format must be {EXECUTION_PROFILE_FORMAT!r}, got {doc_format!r}"
        )

    version = data.get("version")
    if version != EXECUTION_PROFILE_VERSION or isinstance(version, bool):
        raise ExecutionProfileValidationError(
            f"execution profile version must be {EXECUTION_PROFILE_VERSION}, got {version!r}"
        )

    run_id = data.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ExecutionProfileValidationError("invalid or missing run_id in execution profile")

    executor = data.get("executor")
    if executor not in MANAGED_EXECUTORS:
        raise ExecutionProfileValidationError(
            f"unsupported executor in execution profile: {executor!r}"
        )

    model = data.get("model")
    if not isinstance(model, str) or not is_valid_model_identifier(model):
        raise ExecutionProfileValidationError(
            f"invalid model identifier in execution profile: {model!r}"
        )

    effort = data.get("reasoning_effort")
    if not isinstance(effort, str) or not effort:
        raise ExecutionProfileValidationError(
            "invalid or missing reasoning_effort in execution profile"
        )

    model_source = data.get("model_source")
    if model_source not in ALLOWED_SOURCE_ATTRIBUTIONS:
        raise ExecutionProfileValidationError(
            f"invalid or missing model_source in execution profile: {model_source!r}"
        )

    effort_source = data.get("effort_source")
    if effort_source not in ALLOWED_SOURCE_ATTRIBUTIONS:
        raise ExecutionProfileValidationError(
            f"invalid or missing effort_source in execution profile: {effort_source!r}"
        )

    return ResolvedExecutionProfile(
        run_id=run_id,
        executor=executor,
        model=model,
        reasoning_effort=effort,
        model_source=model_source,
        effort_source=effort_source,
        format=doc_format,
        version=version,
    )


def resolve_execution_profile(
    policy: ExecutionProfilePolicy | None = None,
    *,
    run_id: str,
    executor: str,
    model: str | None = None,
    reasoning_effort: str | None = None,
    repo: str | Path | None = None,
) -> ResolvedExecutionProfile:
    """Resolve and validate one exact execution profile from policy and explicit options."""
    if not is_profile_managed_executor(executor):
        raise ExecutionProfileValidationError(
            f"executor {executor!r} is not profile-managed"
        )

    if not isinstance(run_id, str) or not run_id:
        raise ExecutionProfileValidationError("run_id must be a non-empty string")

    if policy is None:
        policy = load_execution_profile_policy(repo)

    spec = policy.spec_for(executor)

    if model is not None:
        validate_model_identifier(model)
        resolved_model = model
        model_source = "EXPLICIT"
    else:
        resolved_model = spec.default_model
        model_source = "REPOSITORY_DEFAULT"

    if reasoning_effort is not None:
        if not policy.is_supported_effort(executor, reasoning_effort):
            raise ExecutionProfileValidationError(
                f"unsupported reasoning effort {reasoning_effort!r} for executor {executor!r}"
            )
        resolved_effort = reasoning_effort
        effort_source = "EXPLICIT"
    else:
        resolved_effort = spec.default_reasoning_effort
        effort_source = "REPOSITORY_DEFAULT"

    return ResolvedExecutionProfile(
        run_id=run_id,
        executor=executor,
        model=resolved_model,
        reasoning_effort=resolved_effort,
        model_source=model_source,
        effort_source=effort_source,
    )


def validate_execution_profile(
    profile: ResolvedExecutionProfile,
    policy: ExecutionProfilePolicy | None = None,
    *,
    repo: str | Path | None = None,
) -> ResolvedExecutionProfile:
    """Validate an existing resolved execution profile against repository policy.

    Deterministically validates that the profile's effort is supported by the repository-owned
    capability policy for its executor, while preserving its exact bound model and effort values.
    A change to repository defaults alone must not alter an already-bound profile.
    """
    if not isinstance(profile, ResolvedExecutionProfile):
        raise ExecutionProfileValidationError("profile must be a ResolvedExecutionProfile")

    if policy is None:
        policy = load_execution_profile_policy(repo)

    if not policy.is_supported_effort(profile.executor, profile.reasoning_effort):
        raise ExecutionProfileValidationError(
            f"unsupported reasoning effort {profile.reasoning_effort!r} for executor {profile.executor!r}"
        )

    return profile


def default_execution_profile(
    executor: str,
    run_id: str = "DEFAULT",
    repo: str | Path | None = None,
) -> ResolvedExecutionProfile:
    """Resolve the repository-owned default execution profile for one executor."""
    policy = load_execution_profile_policy(repo)
    return resolve_execution_profile(policy, run_id=run_id, executor=executor, repo=repo)


def execution_profile_path(state_root: str | Path, run_id: str) -> Path:
    """Return the standard path for one local RUN execution profile sidecar."""
    return Path(state_root) / "execution-profiles" / f"{run_id}.json"


def persist_execution_profile(
    path: Path,
    profile: ResolvedExecutionProfile,
) -> None:
    """Persist an exact immutable execution profile sidecar durably.

    Deterministic re-persistence of identical content is idempotent.
    Conflicting content for an existing RUN profile fails closed.
    """
    target = Path(path)
    if target.is_file():
        existing = parse_execution_profile(target.read_text(encoding="utf-8"))
        if existing == profile:
            return
        raise ExecutionProfileConflictError(
            f"execution profile for RUN {profile.run_id} conflicts with existing profile: "
            f"existing={existing.as_dict()}, attempted={profile.as_dict()}"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_suffix(f".tmp.{os.getpid()}")
    try:
        tmp_path.write_text(profile.to_json(), encoding="utf-8")
        tmp_path.replace(target)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
