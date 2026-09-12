P3 correction-lineage design note.

Canonical P2 base: e37817bce8335e6a158e9aedcae6f90867751c02.
P2 is sufficiently complete after extracting Correction Preflight, Unified State, and Unified Human Surface.

P3 goal: preserve unresolved sibling findings across narrow correction lineage without rediscovery, renaming, ranking, batching, or Runtime selection authority.

Published sequence:
- P3A / TASK-097: exact prospective REMEDIATION predecessor identity, published at 9cb95eb6553131800ff2cb89728147614de2b79d.
- P3B / TASK-098: deterministic outstanding-finding frontier plus publication PASS-and-empty-frontier gate, published at 4df6a12779e254cfe07b5f54de0d89955c321054.

P3C refinement after P3B implementation audit:
- P3C1: expose exact outstanding-finding identities through Unified State and Unified Human Surface without auto-selecting a finding or changing correction execution semantics.
- P3C2: separately establish cumulative correction continuation so a Human/Brain-selected sibling finding can be corrected on top of the current corrected candidate while preserving its original P3A semantic predecessor identity.

The split is required because current REMEDIATION execution still binds Run.base_sha to REMEDIATION.reviewed_sha and uses a historical workspace when the control checkout has advanced. Therefore a later sibling finding originating from an older reviewed candidate can otherwise execute from that older candidate rather than accumulate on the current correction tip. P3C1 must not pretend observation alone solves this execution-continuity problem.

Historical artifacts remain immutable. Runtime must never auto-rank or auto-select outstanding findings. Brain/Human selection remains explicit. Publication remains gated by semantic PASS plus an empty P3B frontier. P3 is not complete until the observation surface and cumulative sibling-correction continuation are both semantically reviewed and published.
