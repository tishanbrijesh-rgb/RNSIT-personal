"""
Phase 3 holdout split for ECDAT corpus.
Splits each language manifest into train (70%) and holdout (30%) sets.
Stratifies by algorithm_family to preserve distribution across families.

Usage:
    python holdout_split.py

Outputs:
    scanner/corpus/holdout/train_{lang}.json
    scanner/corpus/holdout/holdout_{lang}.json
    scanner/corpus/holdout/train_all.json
    scanner/corpus/holdout/holdout_all.json
"""
import json
import os
import random
from collections import defaultdict

MANIFESTS_DIR = os.path.join(os.path.dirname(__file__), "manifests")
HOLDOUT_DIR = os.path.join(os.path.dirname(__file__), "holdout")
os.makedirs(HOLDOUT_DIR, exist_ok=True)

TRAIN_FRACTION = 0.70
HOLDOUT_FRACTION = 0.30
SEED = 26164  # ImpactX fixed seed for reproducibility


def stratified_split(entries, train_frac, rng):
    """Split entries stratified by algorithm_family."""
    by_family = defaultdict(list)
    for e in entries:
        by_family[e["algorithm_family"]].append(e)

    train, holdout = [], []
    for fam_entries in by_family.values():
        shuffled = fam_entries[:]
        rng.shuffle(shuffled)
        n_train = int(len(shuffled) * train_frac)
        # Ensure at least 1 entry per family in each split if possible
        if n_train == 0 and len(shuffled) >= 2:
            n_train = 1
        if n_train == len(shuffled) and len(shuffled) >= 2:
            n_train = len(shuffled) - 1
        train.extend(shuffled[:n_train])
        holdout.extend(shuffled[n_train:])

    return train, holdout


def main():
    rng = random.Random(SEED)

    languages = ["python", "java", "javascript", "c", "go", "rust"]
    all_train = []
    all_holdout = []

    print(f"Holdout split: {int(TRAIN_FRACTION*100)}% train / {int(HOLDOUT_FRACTION*100)}% holdout")
    print(f"Random seed: {SEED}\n")

    for lang in languages:
        manifest_path = os.path.join(MANIFESTS_DIR, f"{lang}.json")
        with open(manifest_path) as f:
            data = json.load(f)

        entries = data["entries"]
        train, holdout = stratified_split(entries, TRAIN_FRACTION, rng)

        # Sort each split by corpus_id for determinism
        train.sort(key=lambda e: e["corpus_id"])
        holdout.sort(key=lambda e: e["corpus_id"])

        # Write per-language splits
        with open(os.path.join(HOLDOUT_DIR, f"train_{lang}.json"), "w") as f:
            json.dump({"language": lang, "split": "train",
                       "count": len(train), "entries": train}, f, indent=2)
        with open(os.path.join(HOLDOUT_DIR, f"holdout_{lang}.json"), "w") as f:
            json.dump({"language": lang, "split": "holdout",
                       "count": len(holdout), "entries": holdout}, f, indent=2)

        all_train.extend(train)
        all_holdout.extend(holdout)

        train_pos = sum(1 for e in train if e["positive"])
        train_neg = sum(1 for e in train if not e["positive"])
        hold_pos = sum(1 for e in holdout if e["positive"])
        hold_neg = sum(1 for e in holdout if not e["positive"])
        print(f"  {lang:12s}: {len(train):3d} train ({train_pos:2d}pos/{train_neg:2d}neg) | "
              f"{len(holdout):3d} holdout ({hold_pos:2d}pos/{hold_neg:2d}neg)")

    # Write combined splits
    all_train.sort(key=lambda e: e["corpus_id"])
    all_holdout.sort(key=lambda e: e["corpus_id"])

    with open(os.path.join(HOLDOUT_DIR, "train_all.json"), "w") as f:
        json.dump({"split": "train", "train_fraction": TRAIN_FRACTION,
                   "total_count": len(all_train), "entries": all_train}, f, indent=2)
    with open(os.path.join(HOLDOUT_DIR, "holdout_all.json"), "w") as f:
        json.dump({"split": "holdout", "holdout_fraction": HOLDOUT_FRACTION,
                   "total_count": len(all_holdout), "entries": all_holdout}, f, indent=2)

    print(f"\n  {'TOTAL':10s}: {len(all_train):3d} train ({sum(1 for e in all_train if e['positive']):2d}pos/{sum(1 for e in all_train if not e['positive']):2d}neg) | "
          f"{len(all_holdout):3d} holdout ({sum(1 for e in all_holdout if e['positive']):2d}pos/{sum(1 for e in all_holdout if not e['positive']):2d}neg)")
    print(f"\n  Written to: {HOLDOUT_DIR}")


if __name__ == "__main__":
    main()
