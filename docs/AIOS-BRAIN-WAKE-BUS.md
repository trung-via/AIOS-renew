# AIOS Brain Wake Bus

This branch and pull request exist only as an operational wake surface for the
GitHub -> ChatGPT Work event-trigger conformance probe.

It is not lifecycle truth, roadmap truth, review truth, or publication truth.
Canonical AIOS state remains on main and refs/heads/aios/**.

The pull request must remain unmerged during the conformance probe.

Wake comments use the marker:

```
[AIOS BRAIN WAKE]
version: 1
...
```

A woken Brain must perform fresh Brain Sync and verify canonical state before any
semantic action.
