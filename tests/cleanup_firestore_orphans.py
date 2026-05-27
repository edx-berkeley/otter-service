"""Sweep stale `otter-ci-*` Firestore collections.

Background: tests/run_docker_grade_check.py wraps its work in try/finally so
the per-run `otter-ci-{run_id}-{grades,logs,submissions,tornado-logs}`
collections get cleaned up at end of run. But GitHub Actions kills jobs
with SIGKILL on `timeout-minutes` expiry, which skips the finally block —
orphaning the collections. This script sweeps anything that's been left
behind.

Safety:
- Only collections matching the otter-ci-{...} pattern are considered.
  Production collections (otter-prod-*, otter-staging-*) are excluded.
- Skips collections whose newest doc is younger than --min-age-hours
  (default 6h), so an in-flight test isn't truncated mid-run.

Run from a GH Actions workflow on a cron, with GCP_SA_KEY in env.
"""
import argparse
import re
import sys
from datetime import datetime, timedelta, timezone

from google.cloud import firestore


COLLECTION_RE = re.compile(
    r"^otter-ci(-test)?-\d+-(grades|logs|submissions|tornado-logs)$"
)


def newest_update_time(coll):
    """Return the most recent doc update_time in `coll`, or None if empty."""
    newest = None
    for doc in coll.stream():
        ts = doc.update_time
        if newest is None or (ts and ts > newest):
            newest = ts
    return newest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="data8x-scratch")
    parser.add_argument(
        "--min-age-hours", type=float, default=6.0,
        help="Skip collections whose newest doc is younger than this. "
             "Guards against truncating an in-flight test run.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List matching collections without deleting.",
    )
    args = parser.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.min_age_hours)
    db = firestore.Client(project=args.project)

    n_swept = 0
    n_skipped_fresh = 0
    docs_deleted = 0
    for coll in db.collections():
        if not COLLECTION_RE.match(coll.id):
            continue
        newest = newest_update_time(coll)
        if newest is None:
            # Empty collection (zombie reference) — nothing to do.
            continue
        if newest > cutoff:
            print(f"  SKIP {coll.id}: newest doc {newest} younger than {args.min_age_hours}h cutoff")
            n_skipped_fresh += 1
            continue

        docs = list(coll.stream())
        if args.dry_run:
            print(f"  DRY {coll.id}: would delete {len(docs)} doc(s)")
        else:
            for d in docs:
                d.reference.delete()
            print(f"  DEL {coll.id}: deleted {len(docs)} doc(s)")
            docs_deleted += len(docs)
        n_swept += 1

    print()
    print(f"Swept: {n_swept} collection(s)")
    print(f"Skipped (too fresh): {n_skipped_fresh}")
    print(f"Docs deleted: {docs_deleted}" if not args.dry_run else "Dry run — nothing deleted")


if __name__ == "__main__":
    main()
