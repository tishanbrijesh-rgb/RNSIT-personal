# ECDAT Scanner Reality, Competitive Context, and ImpactX Deployment Readiness

**Assessment date:** September 21, 2026  
**Scope:** ImpactX demonstration readiness, not production certification

## Executive verdict

ECDAT is **conditionally ready for an ImpactX demonstration deployment**. It is not production-ready, and it should not be presented as a complete enterprise cryptographic-discovery platform.

For ImpactX, the product is credible when positioned as:

> A repository-focused cryptographic discovery and migration-prioritization prototype that combines static evidence, dependency and certificate inspection, CBOM output, explainable risk scoring, and a Mosca-style planning scenario.

It should not claim complete enterprise coverage, guaranteed detection, real-time infrastructure discovery, or universally calibrated confidence probabilities.

## What ECDAT currently searches

ECDAT performs static repository scanning.

### Source code

- Python (`.py`): AST analysis plus rule analysis
- Java (`.java`)
- JavaScript (`.js`)
- TypeScript (`.ts`)
- C (`.c`)
- C++ (`.cpp`)
- Go (`.go`)
- C# (`.cs`)
- Rust (`.rs`)

Except for Python AST analysis, these languages are primarily inspected through auditable pattern rules.

### Dependency manifests and lockfiles

- `requirements.txt`
- `pom.xml`
- `package-lock.json`
- `Gemfile.lock`
- `go.sum`
- `Cargo.lock`

### Certificate files

- `.pem`
- `.crt`
- `.cer`

### Algorithm and protocol indicators

- AES
- RSA
- ECDSA, ECDH and general ECC indicators
- DH and DSA
- SHA-1, SHA-256 and SHA-512
- MD5 and BLAKE2
- Ed25519
- HMAC
- PBKDF2, scrypt and HKDF
- TLS
- ChaCha20
- ML-KEM and ML-DSA

## Current scan boundaries

ECDAT does not currently provide meaningful inspection of:

- Executables, DLLs, shared objects or compiled bytecode
- Docker and other container images
- Live TLS or SSH network endpoints
- Runtime-generated cryptographic operations
- Cloud KMS products, HSMs or secret managers
- Browser and mobile application packages
- Live network traffic
- Operating-system certificate stores or Java keystores
- SSH public/private key inventories
- Encrypted archives and databases
- Kubernetes secrets, Terraform state and similar infrastructure artifacts
- Transitive runtime dependencies absent from supported lockfiles

The normal source profile excludes `.git`, `node_modules`, `dist`, `build`, virtual environments and common cache directories.

Files larger than **8 MB** are marked as oversized and are not scanned by default. The configurable upper bound is 128 MB. Therefore, oversized files are currently coverage failures rather than successfully inspected files.

## IBM Quantum Safe Explorer comparison

IBM Quantum Safe Explorer is primarily an application cryptography discovery product. According to IBM's documentation, it:

- Scans supported source code and some object code
- Models supported cryptographic APIs rather than relying only on algorithm words
- Supports Java, C/C++, C#, Python, Go, Dart, JavaScript and TypeScript
- Recognizes libraries such as OpenSSL, JCA, Bouncy Castle, PyCryptodome, Python `cryptography`, Node `crypto` and .NET Cryptography
- Identifies algorithms, libraries, key sizes, source locations and dependencies
- Generates a CBOM
- Reports quantum-safety and selected FIPS 140-3 information
- Reports files excluded because of unsupported syntax or parsing failures

IBM's library-aware semantic analysis is more precise than broad textual matching.

Reference: [IBM Quantum Safe Explorer overview](https://www.ibm.com/docs/en/quantum-safe/quantum-safe-explorer/2.x?topic=quantum-safe-explorer-overview)

## Keyfactor comparison

Keyfactor's cryptographic discovery platform is broader and more infrastructure-oriented. Its advertised discovery surface includes:

- Source-code repositories
- Server and endpoint file systems
- TLS certificates and endpoints
- SSH keys and tokens
- Cryptographic protocols and libraries
- Cloud environments and workloads
- Binary executables
- Real-time network traffic
- Ownership, dependency and usage context
- Continuous monitoring and compliance reporting

Keyfactor also provides certificate lifecycle management, PKI through EJBCA, and signing services through SignServer. It covers discovery, operational management and remediation rather than repository analysis alone.

Reference: [Keyfactor cryptographic discovery and inventory](https://www.keyfactor.com/products/cryptographic-discovery-inventory/)

ECDAT is currently closer to a repository-focused IBM Explorer prototype than to Keyfactor's enterprise-wide discovery and management platform.

## What the ECDAT metrics actually mean

### Coverage

ECDAT calculates coverage as:

```text
successfully processed supported files / total supported files
```

This is a real operational measurement, but it is not detection completeness. If a repository contains 4,000 files and only 2,300 are supported, 100% coverage means that those 2,300 supported files were processed successfully. It does not mean every repository file was understood or every cryptographic operation was found.

The dashboard should consistently label this as **supported-file processing coverage**.

### Precision and recall

The current frozen evaluation corpus reports:

- Micro precision: **85.71%**
- Micro recall: **96%**
- Micro F1: **90.57%**
- True positives: 48
- False positives: 8
- False negatives: 2

These are legitimate results for that labelled corpus. They do not prove the same performance on arbitrary repositories, enterprise systems or `C:\Python314`.

Displaying precision or recall for an individual scan is defensible only when that exact repository has complete, independently labelled ground truth.

### Confidence

Current confidence scores are evidence-strength heuristics:

| Evidence kind | Base strength |
|---|---:|
| Observed operation | 0.90 |
| Configured protocol | 0.85 |
| Declared capability | 0.65 |
| Artifact metadata | 0.50 |
| Unknown | 0.30 |

Multiple independent evidence kinds can add a bonus, while conflicts reduce the score.

The current 600-sample calibration data is synthetic and generated from assumed accuracy rates. Consequently, a displayed value of 90% must not be described as meaning that 90 of 100 comparable findings will be correct.

Until calibration uses real analyst-labelled outcomes, the UI and presentation should call this an **evidence confidence score** or **heuristic confidence**, not a calibrated probability.

## Mosca assessment

The correct name is **Mosca's XYZ theorem**:

- `X`: required protection lifetime or shelf life
- `Y`: time required to complete migration
- `Z`: estimated time until a cryptographically relevant quantum computer
- Risk condition: `X + Y >= Z`

ECDAT implements this formula correctly and conservatively treats equality as a risk condition. This is consistent with ETSI's repeatable quantum-safe migration framework.

Reference: [ETSI TR 104 016](https://www.etsi.org/deliver/etsi_tr/104000_104099/104016/01.01.01_60/tr_104016v010101p.pdf)

However, a correct equation does not make default inputs factual. ECDAT currently uses policy defaults such as:

- Data lifetime: 10 years
- Migration time: 3 years
- Threat horizon: 15 years
- Business criticality: Medium
- Data sensitivity: Medium
- Exposure: Internal

A Mosca result based on these defaults is a **modelled planning scenario**, not a prediction of when a quantum computer will arrive.

The demonstration should show which inputs are user-provided and which are policy defaults. It should also explain that organizations must provide their own protection lifetime, migration estimate and selected threat-horizon scenario.

## Improving precision

Recommended priority order:

1. Replace broad patterns with language-specific semantic detection.
2. Extend AST or compiler-level analysis to Java, JavaScript/TypeScript, Go, C# and C/C++.
3. Resolve imports and identify actual cryptographic API calls.
4. Distinguish observed operations, configurations, dependencies, capabilities and textual mentions.
5. Require call-site context before classifying generic terms such as `RSA`, `AES` or `TLS` as confirmed use.
6. Add negative rules for comments, documentation, tests, examples, constants and algorithm-name mappings.
7. Correlate declared dependencies with reachable application code.
8. Deduplicate by operation, file and line rather than only algorithm and path.
9. Build a larger external corpus with independently labelled positive and negative examples.
10. Publish metrics by language, collector, algorithm and evidence kind rather than relying on a single global score.

## Making confidence genuine

1. Allow analysts to mark findings as confirmed, rejected or uncertain.
2. Store the original score, evidence and scoring-model version with every verdict.
3. Collect substantial independently labelled data for each evidence kind.
4. Keep training, rule-development, calibration and final test datasets separate.
5. Calibrate scores using methods such as isotonic regression or Platt scaling.
6. Measure Brier score, expected calibration error and reliability curves on an untouched holdout set.
7. Calibrate AST, rules, dependencies, certificates and correlated evidence separately.
8. Never assign dependency presence the same certainty as an observed cryptographic operation.
9. Publish sample counts and confidence intervals alongside calibration results.

## ImpactX deployment readiness

### Ready for an ImpactX demonstration

The following capabilities are sufficiently credible for a controlled ImpactX demonstration:

- Local repository submission
- Safe bounded static scanning
- Multi-language rule discovery
- Python AST evidence
- Dependency and certificate discovery
- Evidence-chain presentation
- Scan history and inspection
- Supported-file coverage and failure reporting
- Risk prioritization with explained inputs
- CBOM export
- Mosca-style migration scenarios
- Responsive frontend and authenticated dashboard

### Conditions before the demonstration

The deployment should be considered ImpactX-ready only if all of these conditions are met:

- Frontend and backend health checks pass on the demonstration machine.
- A complete scan of the prepared demonstration repository succeeds before presentation day.
- At least one positive, one negative and one mixed/conflicting example repository is available.
- The team has a pre-generated successful scan as a fallback if a live scan is slow.
- Unsupported and oversized files are visibly reported rather than silently ignored.
- No dashboard label implies that supported-file coverage equals detection completeness.
- Confidence is described as heuristic evidence confidence.
- Precision/recall are presented as evaluation-corpus metrics, not metrics for every scanned repository.
- Mosca defaults are clearly marked as policy assumptions.
- The presentation states that the product is a prototype and not a certified vulnerability scanner.
- Authentication credentials, database state and scan paths are prepared before judging begins.
- The application is deployed only on a controlled machine or trusted network for the demonstration.

### Not ready for production

Production deployment would require, at minimum:

- Real confidence calibration using analyst-labelled findings
- Independent external accuracy validation
- Broader language-semantic analysis
- Binary, runtime, endpoint, network and cloud discovery
- Enterprise identity integration and stronger authorization
- Secret management and key rotation
- Database backup and migration procedures
- Observability, alerting and capacity testing
- Tenant isolation if offered as a service
- Security review and penetration testing
- Signed releases and software-supply-chain controls
- Documented retention, privacy and incident-response policies

## Recommended ImpactX positioning

### Defensible statement

> ECDAT discovers cryptographic evidence in supported source code, dependencies and certificates; correlates that evidence into an inspectable inventory; and helps teams prioritize post-quantum migration using transparent risk context and Mosca-style planning scenarios.

### Claims to avoid

- “Finds all cryptography”
- “100% accurate”
- “90% confidence means a 90% probability of correctness”
- “Scans every file”
- “Predicts when quantum computers will break encryption”
- “Equivalent to IBM or Keyfactor”
- “Production-ready enterprise security platform”

## Final assessment

ECDAT contains real scanning logic, real evidence records, a genuine evaluation framework, and a correctly implemented Mosca planning rule. Its supported-file coverage is real within the stated scope, and its frozen-corpus accuracy results are meaningful for that corpus.

Its present confidence percentages are heuristic, and its enterprise discovery surface is much narrower than IBM Quantum Safe Explorer or Keyfactor AgileSec. With accurate positioning, prepared demonstration data, visible limitations and successful health checks, ECDAT is ready for an ImpactX prototype deployment.

