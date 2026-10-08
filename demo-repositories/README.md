# ECDAT ImpactX demonstration repositories

These fixtures make the expected outcome explicit before a judge-facing scan. They are intentionally small, deterministic, and safe: the code is evidence for static analysis and is not executed.

| Repository | Purpose | Expected result |
|---|---|---|
| `positive-control` | Known executable crypto API calls | AES, RSA, ECDSA, SHA-256 and HMAC findings |
| `negative-control` | Comments, strings and algorithm-named configuration symbols | No findings |
| `mixed-risk` | Classical weakness, quantum exposure and modern symmetric crypto | MD5/SHA-1, RSA/ECDSA and AES findings across risk bands |

Each directory contains `expected-findings.json`. Treat these labels as demonstration ground truth; do not generalize their precision or recall to unrelated repositories.
