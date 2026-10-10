"""Protected service plumbing, subordinate to the existing Runtime/Control owners.

The service is deployed under a dedicated Windows identity. Its repository,
configuration, TLS keys, App PEM and journal are service-owned, never workspace
inputs. Mutual TLS authenticates approved owner processes, not runner usernames.
No service deployment or ref ruleset is enabled by importing this module.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import sqlite3
import ssl
import sys
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPOSITORY = "trung-via/AIOS-renew"
REMOTE = "https://github.com/" + REPOSITORY + ".git"
RUNTIME_APP = 5257829
CONTROL_APP = 5257909
MAX_REQUEST = 96 * 1024 * 1024
CLIENT_CONFIG = Path("C:/ProgramData/AIOS/owner-client.json")
SHA = re.compile(r"[0-9a-f]{40}")
RUN = re.compile(r"RUN-[A-Za-z0-9_-]+-\d{3,}")


class BrokerError(RuntimeError):
    pass


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def decode(content):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise BrokerError("duplicate field")
            result[key] = value
        return result
    return json.loads(content, object_pairs_hook=unique)


def metadata(content):
    return {"sha256": digest(content), "size": len(content)}


def _protected_file(path, *, private=False, directory=False, authority_root=None):
    """Reject runner-owned setup or writable trust roots using Windows ACLs.

    Approved deployment uses virtual service identities (S-1-5-80-*). SYSTEM
    and Administrators retain trusted Human emergency recovery authority.
    This is ordinary OS isolation, not an anti-Administrator/tamper claim.
    """
    if os.name != "nt":
        raise BrokerError("Windows protected service setup required")
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    def sid_text(pointer):
        text = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(pointer, ctypes.byref(text)):
            raise BrokerError("protected ACL SID unavailable")
        try:
            return text.value
        finally:
            kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    def trusted(sid):
        return sid in {"S-1-5-18", "S-1-5-32-544"} or sid.startswith("S-1-5-80-")
    root = Path(authority_root or "C:/ProgramData/AIOS").resolve(strict=True)
    target = Path(path).resolve(strict=True)
    if not target.is_relative_to(root) or (not target.is_dir() if directory else not target.is_file()):
        raise BrokerError("protected setup path invalid")
    selected = target
    while True:
        owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        code = advapi.GetNamedSecurityInfoW(str(selected), 1, 5, ctypes.byref(owner), None,
                                           ctypes.byref(dacl), None, ctypes.byref(descriptor))
        if code or not owner.value or not dacl.value:
            raise BrokerError("protected ACL unavailable")
        try:
            owner_sid = sid_text(owner)
            if not trusted(owner_sid):
                raise BrokerError("runner-owned service setup rejected")
            count = ctypes.c_ushort.from_address(dacl.value + 4).value
            for index in range(count):
                ace = ctypes.c_void_p()
                if not advapi.GetAce(dacl, index, ctypes.byref(ace)):
                    raise BrokerError("protected ACL unreadable")
                kind = ctypes.c_ubyte.from_address(ace.value).value
                flags = ctypes.c_ubyte.from_address(ace.value + 1).value
                if flags & 8 or kind == 1:  # inherit-only, or deny
                    continue
                if kind != 0:
                    raise BrokerError("unsupported protected ACL")
                mask = ctypes.c_uint32.from_address(ace.value + 4).value
                principal = sid_text(ace.value + 8)
                write = mask & (0x40000000 | 0x10000000 | 0x10000 | 0x40000 | 0x80000 | 0x2 | 0x4 | 0x10 | 0x40 | 0x100)
                read = mask & (0x80000000 | 0x10000000 | 0x1)
                if write and not trusted(principal):
                    raise BrokerError("untrusted service setup write permission")
                if selected == target and private and read and principal not in {owner_sid, "S-1-5-18", "S-1-5-32-544"}:
                    raise BrokerError("private owner credential readable outside owner identity")
        finally:
            kernel.LocalFree(descriptor)
        if selected == root:
            break
        selected = selected.parent
    return target


def _caller_sid():
    """Windows token identity, never a request's asserted username or SID."""
    if os.name != "nt":
        raise BrokerError("Windows owner caller required")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                         wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    token, size = wintypes.HANDLE(), wintypes.DWORD()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise BrokerError("Windows caller token unavailable")
    try:
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, len(buffer), ctypes.byref(size)):
            raise BrokerError("Windows caller SID unavailable")
        pointer = ctypes.c_void_p.from_buffer(buffer).value
        text = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(pointer, ctypes.byref(text)):
            raise BrokerError("Windows caller SID unavailable")
        try:
            return text.value
        finally:
            kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        kernel.CloseHandle(token)


# Exact operation namespaces. Creation and update are separately described in
# the Stage 2 contract; deletion is allowed only for the shared reservation.
OPERATIONS = {
    "TERMINAL_RESULT": ("RUNTIME", ("review", "artifacts")),
    "TERMINAL_FAILURE": ("RUNTIME", ("failure", "failure-artifacts")),
    "RECOVERY_RESULT": ("RUNTIME", ("review", "artifacts", "publication-recovery", "publication-source")),
    "ADMISSION_FAILURE": ("RUNTIME", ("admission-failure", "admission-failure-delivery")),
    "AUTHOR_TASK": ("CONTROL", ("dispatch",)),
    "SUBMIT_REVIEW": ("CONTROL", ("review-decision",)),
    "AUTHOR_REMEDIATION": ("CONTROL", ("remediation", "correction-dispatch")),
    "AUTHOR_REPAIR": ("CONTROL", ("repair", "repair-supersession", "repair-dispatch")),
    "PUBLISH_REVIEWED": ("RUNTIME", ("integration",)),
    "RESERVATION_CONTROL": ("CONTROL", ()),
    "RESERVATION_RUNTIME": ("RUNTIME", ()),
}


def validate_mutation(role, operation, updates):
    policy = OPERATIONS.get(operation)
    if policy is None or role != policy[0] or not isinstance(updates, list) or not 1 <= len(updates) <= 8:
        raise BrokerError("unauthorized writer operation")
    seen = set()
    run_ids = set()
    for item in updates:
        if not isinstance(item, dict) or set(item) != {"ref", "old", "new"}:
            raise BrokerError("invalid CAS fields")
        ref, old, new = item["ref"], item["old"], item["new"]
        if (not isinstance(ref, str) or ref in seen or
                any(v is not None and (type(v) is not str or not SHA.fullmatch(v)) for v in (old, new)) or
                old == new):
            raise BrokerError("invalid CAS identity")
        seen.add(ref)
        reservation = ref == "refs/heads/aios/publication-reservation/main"
        if operation.startswith("RESERVATION_"):
            if not reservation or len(updates) != 1 or (old is None) == (new is None):
                raise BrokerError("reservation requires exact acquisition/release CAS")
        elif ref == "refs/heads/main":
            if operation not in {"AUTHOR_TASK", "PUBLISH_REVIEWED"} or old is None or new is None:
                raise BrokerError("unauthorized main mutation")
        else:
            match = re.fullmatch(r"refs/heads/aios/([a-z-]+)/([A-Za-z0-9_./-]{1,192})", ref)
            if (not match or match[1] not in policy[1] or ".." in match[2] or "//" in match[2]
                    or old is not None or new is None):
                raise BrokerError("unauthorized namespace or immutable-ref update")
            if operation in {"TERMINAL_RESULT", "TERMINAL_FAILURE", "SUBMIT_REVIEW", "RECOVERY_RESULT"}:
                if match[1] == "publication-source":
                    if not re.fullmatch(r"[0-9a-f]{64}", match[2]):
                        raise BrokerError("invalid publication source identity")
                elif not RUN.fullmatch(match[2]):
                    raise BrokerError("invalid RUN ref")
                else:
                    run_ids.add(match[2])
    if len(run_ids) > 1:
        raise BrokerError("mixed RUN mutation")
    return updates


class AppWriter:
    """Only the service instantiates this; credentials never cross the RPC boundary."""
    def __init__(self, *, app_id, installation_id, pem, repository_id, git="git", openssl="openssl", allow_workflows=False):
        if (app_id not in {RUNTIME_APP, CONTROL_APP} or type(repository_id) is not int or repository_id <= 0
                or type(installation_id) is not int or installation_id <= 0 or type(allow_workflows) is not bool):
            raise BrokerError("invalid installation configuration")
        self.app_id, self.installation_id, self.pem = app_id, installation_id, Path(pem)
        self.repository_id, self.git, self.openssl = repository_id, git, openssl
        self.allow_workflows = allow_workflows

    def _token(self, *, workflows=False):
        if not self.pem.is_file():
            raise BrokerError("writer credential unavailable")
        _protected_file(self.pem, private=True)
        if workflows and not self.allow_workflows:
            raise BrokerError("writer workflow permission unavailable")
        permissions = {"contents": "write", **({"workflows": "write"} if workflows else {})}
        def b64(value):
            return base64.urlsafe_b64encode(value).rstrip(b"=")
        now = int(time.time())
        payload = b".".join((b64(encoded({"alg": "RS256", "typ": "JWT"})),
                             b64(encoded({"iat": now - 60, "exp": now + 540, "iss": str(self.app_id)}))))
        signed = subprocess.run([self.openssl, "dgst", "-sha256", "-sign", str(self.pem)],
                                input=payload, capture_output=True, check=False)
        if signed.returncode:
            raise BrokerError("writer signing unavailable")
        jwt = (payload + b"." + b64(signed.stdout)).decode("ascii")
        request = urllib.request.Request(
            f"https://api.github.com/app/installations/{int(self.installation_id)}/access_tokens",
            data=encoded({"repository_ids": [self.repository_id], "permissions": permissions}),
            headers={"Authorization": "Bearer " + jwt, "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
        # Never follow redirects carrying a bearer credential.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
                data = decode(response.read(65537))
            repositories = data.get("repositories", [])
            actual_permissions = data.get("permissions", {})
            if ({k: v for k, v in actual_permissions.items() if k != "metadata"} != permissions
                    or actual_permissions.get("metadata", "read") != "read"
                    or len(repositories) != 1 or repositories[0].get("full_name") != REPOSITORY
                    or repositories[0].get("id") != self.repository_id or not data.get("token")):
                raise BrokerError("installation permission/repository mismatch")
            return data["token"]
        except Exception:
            raise BrokerError("writer credential or permission unavailable") from None

    def push(self, repo, updates, *, workflows=False):
        token = self._token(workflows=workflows)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        auth = base64.b64encode(("x-access-token:" + token).encode()).decode()
        config = {"credential.helper": "", "core.hooksPath": "NUL" if os.name == "nt" else "/dev/null",
                  "http.followRedirects": "false", "http.https://github.com/.extraheader": "AUTHORIZATION: basic " + auth}
        env["GIT_CONFIG_COUNT"] = str(len(config))
        for index, (key, value) in enumerate(config.items()):
            env[f"GIT_CONFIG_KEY_{index}"], env[f"GIT_CONFIG_VALUE_{index}"] = key, value
        command = [self.git, "-C", str(repo), "push", "--atomic", "--no-tags",
                   *(f"--force-with-lease={u['ref']}:{u['old'] or ''}" for u in updates), REMOTE,
                   *(f"{u['new'] or ''}:{u['ref']}" for u in updates)]
        result = subprocess.run(command, env=env, capture_output=True, check=False, timeout=90)
        # Do not return Git stderr, URLs, credentials or server-controlled text.
        if result.returncode:
            raise BrokerError("writer CAS rejected")


class BrokerService:
    """One protected credential/issuance journal, never an admission/lifecycle router.

    Caller role is selected from a verified TLS peer by the HTTP handler. A JSON
    role, username, workflow label, fixture, ref or App identity grants nothing.
    """
    def __init__(self, *, repo, journal, key, peers, writers, raw_root, git="git"):
        if len(key) < 32 or not peers or any(v not in {"RUNTIME", "CONTROL", "READER"} for v in peers.values()):
            raise BrokerError("protected service configuration unavailable")
        self.repo, self.raw_root, self.git = Path(repo), Path(raw_root).resolve(), git
        self.journal = Path(journal)
        self.key, self.peers, self.writers = key, dict(peers), dict(writers)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(journal, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS operations (nonce TEXT PRIMARY KEY, request BLOB, state TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS sources (identity TEXT PRIMARY KEY, record BLOB)")
        self.db.commit()

    def _git(self, *args, content=None):
        result = subprocess.run([self.git, "-C", str(self.repo), *args], input=content,
                                capture_output=True, check=False, timeout=90)
        if result.returncode:
            raise BrokerError("protected object unavailable")
        return result.stdout

    def _raw(self, paths):
        result = []
        for item in paths:
            if set(item) != {"evidence_id", "path"}:
                raise BrokerError("invalid protected raw locator")
            try:
                path = Path(item["path"]).resolve(strict=True)
            except OSError:
                raise BrokerError("protected raw unavailable") from None
            if not path.is_relative_to(self.raw_root) or not path.is_file():
                raise BrokerError("protected raw unavailable")
            hasher, size = hashlib.sha256(), 0
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    hasher.update(chunk)
                    size += len(chunk)
            result.append({"evidence_id": item["evidence_id"], "sha256": hasher.hexdigest(), "size": size})
        return result

    def _source_record(self, identity):
        row = self.db.execute("SELECT record FROM sources WHERE identity=?", (identity,)).fetchone()
        if not row:
            return None
        record = decode(row[0])
        unsigned = {k: v for k, v in record.items() if k != "authentication"}
        expected = hmac.new(self.key, encoded(unsigned), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, record.get("authentication", "")):
            raise BrokerError("source journal authentication mismatch")
        if (record.get("version") != 1 or record.get("repository") != REPOSITORY
                or identity != record.get("kind", "") + ":" + record["facts"]["run_id"]):
            raise BrokerError("source journal identity mismatch")
        return record

    def _remote_ref(self, ref):
        rows = self._git("ls-remote", "--refs", REMOTE, ref).decode().splitlines()
        if not rows:
            return None
        if len(rows) != 1 or rows[0].split()[1] != ref:
            raise BrokerError("ambiguous protected remote ref")
        value = rows[0].split()[0]
        if not SHA.fullmatch(value):
            raise BrokerError("invalid protected remote ref")
        return value

    def _mutation_objects(self, role, operation, updates, request):
        # Object binding supplements the existing owners' lifecycle checks; it
        # cannot select a TASK, admit a RUN or manufacture a semantic verdict.
        workflows = False
        for item in updates:
            if self._remote_ref(item["ref"]) != item["old"]:
                raise BrokerError("stale writer CAS")
        reservation_ref = "refs/heads/aios/publication-reservation/main"
        if operation.startswith("RESERVATION_"):
            item = updates[0]
            token = item["new"] or item["old"]
            content = decode(self._git("show", token + ":reservation.json"))
            identity = content["identity"]
            if (content.get("version") != 1 or set(identity) != {"kind", "subject", "source_sha", "main_sha", "decision_sha", "artifacts_sha"}
                    or identity["kind"] not in {"MAIN_MUTATION", "REVIEW_TO_PUBLICATION"}
                    or role == "RUNTIME" and identity["kind"] != "REVIEW_TO_PUBLICATION"
                    or any(type(identity[k]) is not str or not SHA.fullmatch(identity[k]) for k in ("source_sha", "main_sha"))
                    or type(content.get("created_at")) is not int or type(content.get("expires_at")) is not int
                    or not content["created_at"] <= int(time.time()) < content["expires_at"]):
                raise BrokerError("reservation operation identity invalid or expired")
            if item["new"] and self._remote_ref("refs/heads/main") != identity["main_sha"]:
                raise BrokerError("reservation main binding stale")
            return False
        for item in updates:
            if item["ref"] == "refs/heads/main":
                pin = request.get("reservation")
                if type(pin) is not dict or set(pin) != {"token_sha", "identity"}:
                    raise BrokerError("protected main writer reservation required")
                token = pin["token_sha"]
                if type(token) is not str or not SHA.fullmatch(token) or self._remote_ref(reservation_ref) != token:
                    raise BrokerError("protected main reservation CAS mismatch")
                content = decode(self._git("show", token + ":reservation.json"))
                identity = content["identity"]
                expected_kind = "MAIN_MUTATION" if operation == "AUTHOR_TASK" else "REVIEW_TO_PUBLICATION"
                if (content.get("version") != 1 or identity != pin["identity"] or identity["kind"] != expected_kind
                        or identity["source_sha"] != item["new"] or identity["main_sha"] != item["old"]
                        or not content["created_at"] <= int(time.time()) < content["expires_at"]):
                    raise BrokerError("protected main operation/reservation mismatch")
                self._git("merge-base", "--is-ancestor", item["old"], item["new"])
                paths = self._git("diff", "--name-only", item["old"], item["new"]).decode().splitlines()
                workflows |= any(path.startswith(".github/workflows/") for path in paths)
            if operation in {"TERMINAL_RESULT", "TERMINAL_FAILURE", "RECOVERY_RESULT"}:
                family, run_id = item["ref"].removeprefix("refs/heads/aios/").split("/", 1)
                if family in {"artifacts", "failure-artifacts"}:
                    allowed = {".ai/transport/run.json", ".ai/transport/result.json", ".ai/transport/failure.json",
                               ".ai/transport/execution-profile.json", ".ai/transport/observation.json",
                               ".ai/transport/repair.json", ".ai/transport/pre-verification-candidate.json",
                               ".ai/transport/publication-recovery.json"}
                    paths = set(self._git("ls-tree", "-r", "--name-only", item["new"]).decode().splitlines())
                    if not paths.issubset(allowed):
                        raise BrokerError("protected raw/unexpected artifact export rejected")
                    run = decode(self._git("show", item["new"] + ":.ai/transport/run.json"))
                    underlying = run.get("execution", {}).get("run") if run.get("kind") == "REMEDIATION" else run
                    if underlying.get("run_id") != run_id:
                        raise BrokerError("writer RUN/artifact mismatch")
                    terminal_name = "failure" if family == "failure-artifacts" else "result"
                    terminal = decode(self._git("show", item["new"] + f":.ai/transport/{terminal_name}.json"))
                    candidate = terminal.get("failed_head_sha") if terminal_name == "failure" else terminal["result"]["head_sha"]
                    candidate_ref = "refs/heads/aios/" + ("failure" if terminal_name == "failure" else "review") + "/" + run_id
                    peer = next((u["new"] for u in updates if u["ref"] == candidate_ref), None)
                    if peer is None:
                        peer = self._remote_ref(candidate_ref)
                    if peer is not None and peer != candidate:
                        raise BrokerError("writer candidate/artifact mismatch")
                    if terminal_name == "result" and peer is None:
                        raise BrokerError("writer candidate missing")
                    paths = self._git("diff", "--name-only", underlying["base_sha"], candidate).decode().splitlines()
                    workflows |= any(path.startswith(".github/workflows/") for path in paths)
            if operation == "SUBMIT_REVIEW":
                run_id = item["ref"].rsplit("/", 1)[1]
                parent = self._git("rev-list", "--parents", "-n", "1", item["new"]).decode().split()
                if len(parent) != 2 or self._remote_ref("refs/heads/aios/review/" + run_id) != parent[1]:
                    raise BrokerError("writer review/candidate mismatch")
        return workflows

    def _import(self, request):
        if request.get("pack"):
            pack = base64.b64decode(request["pack"], validate=True)
            if len(pack) > 64 * 1024 * 1024:
                raise BrokerError("object import bound exceeded")
            self._git("index-pack", "--stdin", content=pack)

    def _source_objects(self, kind, facts, request):
        """Independently bind exact immutable objects, never Git writer identity."""
        from .runtime_provenance_issuer import public_result, public_run
        from .task import parse_task
        def blob(commit, path):
            size = int(self._git("cat-file", "-s", commit + ":" + path))
            if size > 1048576:
                raise BrokerError("source object bound exceeded")
            return self._git("show", commit + ":" + path)
        artifact, candidate = facts["artifact_sha"], facts["candidate_sha"]
        allowed = {".ai/transport/run.json", ".ai/transport/result.json", ".ai/transport/execution-profile.json",
                   ".ai/transport/observation.json"}
        if not set(self._git("ls-tree", "-r", "--name-only", artifact).decode().splitlines()).issubset(allowed):
            raise BrokerError("unsupported terminal source tree")
        task_path = ".ai/tasks/" + facts["task_id"] + ".yaml"
        task = parse_task(blob(facts["base_sha"], task_path).decode())
        if (task.task_id != facts["task_id"] or task.revision != facts["task_revision"]
                or self._git("rev-parse", facts["base_sha"] + ":" + task_path).decode().strip() != facts["task_blob_sha"]
                or self._git("rev-parse", facts["task_authoring_commit_sha"] + ":" + task_path).decode().strip() != facts["task_blob_sha"]
                or self._git("rev-parse", candidate + ":" + task_path).decode().strip() != facts["task_blob_sha"]
                or self._git("rev-parse", candidate + "^{tree}").decode().strip() != facts["candidate_tree_sha"]):
            raise BrokerError("source TASK/blob/tree mismatch")
        run_bytes = blob(artifact, ".ai/transport/run.json")
        result_bytes = blob(artifact, ".ai/transport/result.json")
        profile_bytes = blob(artifact, ".ai/transport/execution-profile.json")
        result = decode(result_bytes)
        run = decode(run_bytes)
        profile = decode(profile_bytes)
        if (metadata(run_bytes) != facts["run"] or metadata(result_bytes) != facts["result"]
                or metadata(profile_bytes) != facts["profile"]
                or metadata(encoded(result["evidence"])) != facts["evidence"]
                or run.get("run_id") != facts["run_id"] or run.get("base_sha") != facts["base_sha"]
                or run.get("task") != {"id": facts["task_id"], "revision": facts["task_revision"]}
                or run.get("status") != "ACTIVE" or result["result"]["head_sha"] != candidate
                or profile.get("run_id") != facts["run_id"] or profile.get("executor") != run.get("executor")):
            raise BrokerError("source RUN/profile/RESULT mismatch")
        for item in result["evidence"]:
            if item.get("run_id") != facts["run_id"] or item.get("subject_sha") != candidate:
                raise BrokerError("source EVIDENCE mismatch")
        if kind == "RUNTIME_TERMINAL":
            original = base64.b64decode(request["original_result"], validate=True)
            original_run = base64.b64decode(request["original_run"], validate=True)
            if (len(original) > 1048576 or len(original_run) > 1048576
                    or metadata(original) != facts["local_result"] or metadata(original_run) != facts["local_run"]
                    or digest(original_run) != facts["admitted_run_sha256"]
                    or public_result(original) != result_bytes or public_run(original_run) != run_bytes
                    or metadata(encoded(decode(original)["evidence"])) != facts["local_evidence"]
                    or [item["evidence_id"] for item in result["evidence"]] != [item["evidence_id"] for item in facts["raw"]]
                    or [item["evidence_id"] for item in facts["raw"]] != [item["evidence_id"] for item in request["paths"]]):
                raise BrokerError("source original terminal/raw mismatch")
            for item, locator in zip(decode(original)["evidence"], request["paths"], strict=True):
                original_path = (Path(decode(original_run)["workspace"]) / item["raw"]["path"]).resolve()
                if original_path != Path(locator["path"]).resolve():
                    raise BrokerError("source original raw locator swapped")
            # The existing canonical validators remain canonical authority.
            from .artifacts import validate_result, validate_evidence, validate_result_package
            from .run import Run
            admitted = Run.from_task(run_id=run["run_id"], task=task,
                                     executor=run["executor"], base_sha=run["base_sha"], workspace=run["workspace"])
            validate_result_package(task=task, run=admitted, result=validate_result(result["result"]),
                                    evidence=tuple(validate_evidence(item) for item in result["evidence"]))
        else:
            from .review import parse_review
            decision = facts["decision_sha"]
            paths = self._git("diff-tree", "--no-commit-id", "--name-only", "-r", candidate, decision).decode().splitlines()
            if (self._git("rev-list", "--parents", "-n", "1", decision).decode().split() != [decision, candidate]
                    or len(paths) != 1 or not re.fullmatch(r"\.ai/reviews/[A-Za-z0-9_-]+\.yaml", paths[0])):
                raise BrokerError("source decision parent/delta mismatch")
            review_bytes = blob(decision, paths[0])
            review = parse_review(review_bytes.decode())
            if metadata(review_bytes) != facts["review"] or review.reviewed_sha != candidate or review.verdict != facts["recorded_verdict"]:
                raise BrokerError("source review decision mismatch")

    def handle(self, peer_digest, request):
        role = self.peers.get(peer_digest)
        if role is None or not isinstance(request, dict):
            raise BrokerError("unauthenticated IPC")
        with self.lock:
            action = request.get("action")
            if action == "read":
                return {"record": self._source_record(request["identity"])}
            if action == "compare_raw":
                if role != "READER":
                    raise BrokerError("independent raw reader permission required")
                record = self._source_record(request["identity"])
                if not record:
                    raise BrokerError("source unavailable")
                if record["kind"] != "RUNTIME_TERMINAL":
                    raise BrokerError("raw comparison requires Runtime terminal")
                return {"matches": self._raw(request["paths"]) == record["facts"]["raw"]}
            if action == "issue":
                return self._issue(role, request)
            if action != "mutate":
                raise BrokerError("unsupported protected operation")
            updates = validate_mutation(role, request.get("operation"), request.get("updates"))
            writer = self.writers.get(role)
            expected_app = RUNTIME_APP if role == "RUNTIME" else CONTROL_APP
            if writer is None or writer.app_id != expected_app:
                raise BrokerError("wrong or missing writer")
            nonce = request.get("nonce")
            if type(nonce) is not str or not re.fullmatch(r"[0-9a-f]{64}", nonce):
                raise BrokerError("invalid operation nonce")
            payload = encoded({k: v for k, v in request.items() if k != "pack"})
            try:
                self.db.execute("INSERT INTO operations VALUES (?, ?, 'RESERVED')", (nonce, payload))
                self.db.commit()  # Burn before I/O; crashes never silently re-authorize writes.
            except sqlite3.IntegrityError:
                raise BrokerError("operation replay rejected") from None
            try:
                self._import(request)
                for update in updates:
                    if update["new"]:
                        if self._git("cat-file", "-t", update["new"]).strip() != b"commit":
                            raise BrokerError("writer target is not a commit")
                workflows = self._mutation_objects(role, request["operation"], updates, request)
                writer.push(self.repo, updates, workflows=workflows)
            except Exception:
                self.db.execute("UPDATE operations SET state='FAILED' WHERE nonce=?", (nonce,))
                self.db.commit()
                raise BrokerError("protected mutation failed; re-observe exact refs before a new owner attempt") from None
            self.db.execute("UPDATE operations SET state='WRITTEN' WHERE nonce=?", (nonce,))
            self.db.commit()
            return {"written": updates}

    def _issue(self, role, request):
        kind, facts = request.get("kind"), request.get("facts")
        if (kind not in {"RUNTIME_TERMINAL", "REVIEW_INGRESS"} or
                role != ("RUNTIME" if kind == "RUNTIME_TERMINAL" else "CONTROL")):
            raise BrokerError("source issuer role mismatch")
        from .runtime_provenance_issuer import validate_source_facts
        validate_source_facts(kind, facts)
        self._import(request)
        self._source_objects(kind, facts, request)
        identity = kind + ":" + facts["run_id"]
        if kind == "RUNTIME_TERMINAL":
            if self._raw(request.get("paths", [])) != facts["raw"]:
                raise BrokerError("original raw digest/size mismatch")
        else:
            source = self._source_record("RUNTIME_TERMINAL:" + facts["run_id"])
            if not source:
                raise BrokerError("independent Runtime source unavailable")
            source_facts = source["facts"]
            for key in (key for key in facts if key in source_facts):
                if facts[key] != source_facts[key]:
                    raise BrokerError("review terminal source mismatch")
        record = {"version": 1, "repository": REPOSITORY, "kind": kind, "facts": facts}
        record["authentication"] = hmac.new(self.key, encoded(record), hashlib.sha256).hexdigest()
        content = encoded(record)
        row = self.db.execute("SELECT record FROM sources WHERE identity=?", (identity,)).fetchone()
        if row and row[0] != content:
            raise BrokerError("immutable source conflict")
        if not row:
            self.db.execute("INSERT INTO sources VALUES (?, ?)", (identity, content))
            self.db.commit()
        return {"record": record}


class BrokerClient:
    """TLS client created only from protected machine setup, never TASK/env input."""
    def __init__(self, config):
        if config["host"] != "localhost" or type(config["port"]) is not int:
            raise BrokerError("broker must be machine-local")
        self.config = config
        self.context = ssl.create_default_context(cafile=config["ca"])
        self.context.minimum_version = ssl.TLSVersion.TLSv1_3
        self.context.load_cert_chain(config["certificate"], config["key"])

    def request(self, request):
        data = encoded(request)
        if len(data) > MAX_REQUEST:
            raise BrokerError("request bound exceeded")
        connection = http.client.HTTPSConnection("localhost", self.config["port"], context=self.context, timeout=100)
        try:
            connection.connect()
            cert = connection.sock.getpeercert(binary_form=True)
            if not cert or not hmac.compare_digest(digest(cert), self.config["service_certificate_sha256"]):
                raise BrokerError("broker service identity mismatch")
            connection.request("POST", "/owner-operation", data, {"Content-Type": "application/json"})
            response = connection.getresponse()
            content = response.read(1048577)
            if response.status != 200 or len(content) > 1048576:
                raise BrokerError("protected owner operation rejected")
            return decode(content)
        finally:
            connection.close()


def protected_client():
    if os.name != "nt" or not CLIENT_CONFIG.is_file():
        raise BrokerError("protected broker setup unavailable")
    # Deployment ACLs, trusted service code and caller certificate private keys
    # are an explicit operational gate. No workspace/env override is accepted.
    _protected_file(CLIENT_CONFIG)
    sid = _caller_sid()
    if not sid.startswith("S-1-5-80-"):
        raise BrokerError("untrusted runner identity is not an approved owner caller")
    manifest = decode(CLIENT_CONFIG.read_bytes())
    config = manifest.get("owners", {}).get(sid)
    if not isinstance(config, dict):
        raise BrokerError("approved owner credential unavailable")
    for field in ("ca", "certificate", "key"):
        _protected_file(config[field], private=field == "key")
    if not re.fullmatch(r"[0-9a-f]{64}", config["service_certificate_sha256"]):
        raise BrokerError("protected service certificate pin invalid")
    return BrokerClient(config)


def serve(service, *, port, certificate, key, ca):
    """Host in the dedicated service; no launcher, installation or activation."""
    for path in (certificate, key, ca):
        _protected_file(path, private=path == key)
    _protected_file(service.repo, directory=True)
    _protected_file(service.journal)
    _protected_file(service.raw_root, directory=True, authority_root=service.raw_root.parent.parent)
    binaries = {service.git}
    for writer in service.writers.values():
        binaries.update((writer.git, writer.openssl))
    for executable in binaries:
        path = Path(executable)
        if not path.is_absolute():
            raise BrokerError("protected service executable must be pinned absolutely")
        _protected_file(path, authority_root=path.parent)
    if any(not re.fullmatch(r"[0-9a-f]{64}", peer) for peer in service.peers):
        raise BrokerError("protected peer certificate pin invalid")
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *args):
            pass  # No protected request or credential logging.
        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.path != "/owner-operation" or not 0 < length <= MAX_REQUEST:
                    raise BrokerError("invalid IPC framing")
                peer = self.connection.getpeercert(binary_form=True)
                response = service.handle(digest(peer), decode(self.rfile.read(length)))
                body, status = encoded(response), 200
            except Exception:
                body, status = b'{"error":"protected operation rejected"}', 403
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_cert_chain(certificate, key)
    context.load_verify_locations(cafile=ca)
    server = HTTPServer(("127.0.0.1", port), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


def owner_push(repo, operation, updates, *, reservation=None):
    """Use protected writer when configured; legacy local/test transport is unchanged."""
    if not CLIENT_CONFIG.is_file():
        return None
    client = protected_client()  # Configured but missing credentials fails closed.
    targets = {v for u in updates for v in (u["old"], u["new"]) if v}
    if reservation:
        targets.add(reservation["token_sha"])
    targets = sorted(targets)
    pack = b""
    if targets:
        result = subprocess.run(["git", "-C", str(repo), "pack-objects", "--stdout", "--revs"],
                                input=("\n".join(targets) + "\n").encode(), capture_output=True, check=False)
        if result.returncode or len(result.stdout) > 64 * 1024 * 1024:
            raise BrokerError("protected writer object import unavailable")
        pack = result.stdout
    return client.request({"action": "mutate", "operation": operation, "updates": updates,
                           "nonce": secrets.token_hex(32), "pack": base64.b64encode(pack).decode(),
                           "reservation": reservation})


def broker_git_push(repo, args):
    """Route existing owner Git pushes, retaining their exact ref and lease CAS.

    A configured service fails closed: there is no fallback to runner credentials.
    Code identity is an integration seal, not an authentication credential.
    """
    if not args or args[0] != "push" or not CLIENT_CONFIG.is_file():
        return None
    owners = {
        "review_transport": {"transport_post_pass": "TERMINAL_RESULT", "transport_failure": "TERMINAL_FAILURE",
                             "transport_admission_failure": "ADMISSION_FAILURE"},
        "authoring_ingress": {"_execute_author_task": "AUTHOR_TASK", "_execute_submit_review": "SUBMIT_REVIEW",
                              "_execute_author_remediation": "AUTHOR_REMEDIATION", "_execute_author_repair": "AUTHOR_REPAIR"},
        "publication": {"publish_review_decision": "PUBLISH_REVIEWED"},
        "operator": {"recover_publication_source": "RECOVERY_RESULT"},
    }
    operation, reservation_operation, reservation_pin = None, False, None
    frame = sys._getframe(1)
    for unused in range(16):
        if frame is None:
            break
        for module, functions in owners.items():
            actual = sys.modules.get("aios_renew." + module)
            if actual and frame.f_globals is vars(actual):
                for name, selected in functions.items():
                    function = getattr(actual, name, None)
                    if function and frame.f_code is function.__code__ and operation is None:
                        operation = selected
        if frame.f_code.co_name in {"reserve_publication", "release_publication_reservation"}:
            reservation_operation = True
        value = frame.f_locals.get("reservation")
        if value is not None and hasattr(value, "token_sha") and hasattr(value, "identity"):
            reservation_pin = {"token_sha": value.token_sha, "identity": value.identity}
        frame = frame.f_back
    try:
        if operation is None:
            raise BrokerError("unsupported owner write path")
        if reservation_operation:
            operation = "RESERVATION_CONTROL" if operation.startswith("AUTHOR_") or operation == "SUBMIT_REVIEW" else "RESERVATION_RUNTIME"
        leases = {}
        for arg in args:
            if arg.startswith("--force-with-lease="):
                ref, old = arg.removeprefix("--force-with-lease=").split(":", 1)
                leases[ref] = old or None
        updates = []
        for arg in args:
            if not arg.startswith("-") and ":refs/heads/" in arg:
                new, ref = arg.split(":", 1)
                updates.append({"ref": ref, "old": leases.get(ref), "new": new or None})
        if not updates:
            raise BrokerError("missing exact writer refs")
        if any(u["ref"] in {"refs/heads/main", "refs/heads/aios/publication-reservation/main"}
               and u["ref"] not in leases for u in updates):
            raise BrokerError("missing protected writer lease")
        response = owner_push(repo, operation, updates, reservation=reservation_pin)
        if response is None or response.get("written") != updates:
            raise BrokerError("protected writer response mismatch")
        return 0, "", ""
    except Exception:
        return 1, "", "protected writer unavailable or rejected exact owner CAS"
