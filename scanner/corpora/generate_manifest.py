import json
import os

BASE = "/c/Users/Tishan Kumar B/Desktop/ImpactX/ECDAT-ImpactX/scanner/corpora"

cases = []
pos_count = 0
neg_count = 0

# Helper to get family from algorithm
def get_family(algorithm):
    families = {
        "AES": "symmetric", "ChaCha20": "symmetric",
        "RSA": "asymmetric", "ECDSA": "asymmetric", "Ed25519": "asymmetric",
        "ECDH": "key_exchange",
        "SHA-256": "hash", "SHA-512": "hash", "SHA-1": "hash", "MD5": "hash", "BLAKE2": "hash",
        "HMAC": "mac",
        "TLS": "protocol",
        "PBKDF2": "kdf",
    }
    return families.get(algorithm, "unknown")

lang_map = {
    ".py": "python", ".java": "java", ".js": "javascript",
    ".go": "go", ".c": "c", ".cs": "csharp"
}

# Scan all source files
for root, dirs, files in os.walk(BASE):
    dirs.sort()
    for fname in sorted(files):
        if fname.endswith(".expected.json") or fname in ("manifest.json", "README.md"):
            continue
        ext = os.path.splitext(fname)[1]
        if ext not in lang_map:
            continue
        full_path = os.path.join(root, fname)
        rel = os.path.relpath(full_path, BASE)
        lang = lang_map[ext]
        parts = rel.split(os.sep)
        file_type = parts[1]  # positive, negative, aliases, wrappers
        is_positive = file_type in ("positive", "aliases", "wrappers")
        
        if is_positive:
            pos_count += 1
        else:
            neg_count += 1
        
        base = os.path.splitext(fname)[0].replace("-", "_")
        case_id = f"{lang[:2]}-{base}"
        
        cases.append({
            "id": case_id,
            "language": lang,
            "type": "positive" if is_positive else "negative",
            "family": "unknown",
            "path": rel.replace(os.sep, "/")
        })

total = pos_count + neg_count

manifest = {
    "schema_version": 1,
    "corpus_name": "phase4-frozen",
    "created": "2026-09-12",
    "languages": ["python", "java", "javascript", "go", "c", "csharp"],
    "total_cases": total,
    "positive_cases": pos_count,
    "negative_cases": neg_count,
    "cases": cases
}

out_path = os.path.join(BASE, "manifest.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)

print(f"Written {total} cases ({pos_count} positive, {neg_count} negative)")
