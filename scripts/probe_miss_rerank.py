"""Read-only miss-diagnosis probe for a finished diagnostic retrieval run.

For every evidence that failed to match inside top-10 in the run's traces,
this script locates the carrying chunk OFFLINE (quote ⊆ chunk text ∧
element_ids ⊆ chunk elements, reproduced with the published chunk ids) and
re-ranks it against the live published collection:

* dense exact rank over the whole collection,
* bm25 rank over the whole collection,
* RRF fusion rank under the run's own prefetch limit (unfiltered dense+BM25+RRF
  retrieval core).

Before any query is issued the probe validates its binding to the run:

* dataset sha256 equals the run's recorded ``dataset_sha256``,
* the dataset's ``anchor_corpus`` equals the run's recorded ``corpus_version``,
* the live registry pointer still names the run's recorded ``corpus_version``
  and ``collection``,
* the collection exists and its point count equals BOTH the run's recorded
  count and the pointer's ``chunk_count``.

Any mismatch refuses the run -- the probe must never diagnose against a
different dataset, corpus build or collection than the one that produced the
traces.

It writes ONE file -- ``miss_rerank_probe.json`` inside the given run
directory -- and never touches the dataset, the corpus, the index or the
run's recorded artifacts.  This is diagnosis evidence, not an optimization:
no retrieval configuration is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# Force ROOT to sys.path[0]: the machine's global PYTHONPATH can hold OTHER
# projects' ``src`` packages (e.g. D:\app_dev\ApisMind\backend\src), and a
# mere "if ROOT not in sys.path" insert is skipped when ROOT already sits at
# a later position -- every src.* import then silently shadows to that other
# project and fails with "No module named 'src.application'".
sys.path[:] = [p for p in sys.path if p != str(ROOT)]
sys.path.insert(0, str(ROOT))

from qdrant_client import QdrantClient, models  # noqa: E402

from scripts.reconcile_fact_evidence import ChunkedCorpus  # noqa: E402
from scripts.reanchor_golden_v2 import _normalized  # noqa: E402
from src.retrieval.bge_small_zh import BgeSmallZhEmbedder  # noqa: E402
from src.retrieval.qdrant_index import (  # noqa: E402
    HybridQuery,
    QdrantRetrievalIndex,
)


def _rank_of(chunk_id: str, point_chunk_ids: list[str]) -> int | None:
    try:
        return point_chunk_ids.index(chunk_id) + 1
    except ValueError:
        return None


def _binding_problems(
    config: dict,
    dataset_path: Path,
    current: dict,
    *,
    client: Any = None,
) -> list[str]:
    """Pre-run binding validation, shared by main() and the tests.

    Offline checks (always): dataset sha256 equals the run's recorded
    ``dataset_sha256``; EVERY dataset row carries ``anchor_corpus`` (required)
    and all rows anchor to exactly the run's ``corpus_version``; the registry
    pointer names the run's ``corpus_version`` and ``collection``; the
    pointer's ``chunk_count`` and the run config's ``point_count`` are both
    present (required) and agree with each other.

    Live checks (when a client is supplied): the collection exists and its
    point count equals BOTH the run config and the pointer chunk count.

    Any mismatch is a problem string; the caller refuses.
    """
    problems: list[str] = []
    dataset_sha = hashlib.sha256(Path(dataset_path).read_bytes()).hexdigest()
    if dataset_sha != config.get("dataset_sha256"):
        problems.append(
            f"dataset hash mismatch: file {dataset_sha[:16]}… != run "
            f"{str(config.get('dataset_sha256'))[:16]}…"
        )
    anchors: set[str] = set()
    for line in Path(dataset_path).read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        row = json.loads(line)
        anchor = row.get("anchor_corpus")
        if not anchor:
            problems.append(
                f"{row.get('id', '?')}: anchor_corpus missing (required)"
            )
        else:
            anchors.add(anchor)
    if len(anchors) > 1:
        problems.append(f"dataset anchor_corpus not uniform: {sorted(anchors)}")
    if anchors and anchors != {config.get("corpus_version")}:
        problems.append(
            f"dataset anchor_corpus {sorted(anchors)} != run corpus_version "
            f"{config.get('corpus_version')}"
        )
    if current.get("corpus_version") != config.get("corpus_version"):
        problems.append(
            f"registry pointer corpus_version {current.get('corpus_version')} != "
            f"run corpus_version {config.get('corpus_version')}"
        )
    if current.get("collection_name") != config.get("collection"):
        problems.append(
            f"registry pointer collection {current.get('collection_name')} != "
            f"run collection {config.get('collection')}"
        )
    expected_count = current.get("chunk_count")
    if not expected_count:
        problems.append("registry pointer chunk_count missing (required)")
    config_count = config.get("point_count")
    if not config_count:
        problems.append("run config point_count missing (required)")
    if expected_count and config_count and int(expected_count) != int(config_count):
        problems.append(
            f"point count mismatch: registry pointer chunk_count {expected_count} "
            f"!= run config point_count {config_count}"
        )
    if client is not None:
        collection = config.get("collection")
        if not client.collection_exists(collection):
            problems.append(
                f"run collection does not exist: {collection}"
            )
        else:
            point_count = int(client.get_collection(collection).points_count or 0)
            if point_count != int(config.get("point_count") or 0):
                problems.append(
                    f"point count mismatch: qdrant {point_count} != run config "
                    f"{config.get('point_count')}"
                )
            if expected_count and point_count != int(expected_count):
                problems.append(
                    f"point count mismatch: qdrant {point_count} != registry "
                    f"pointer chunk_count {expected_count}"
                )
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=ROOT / "eval" / "golden_v2.jsonl")
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    if config.get("classification") != "diagnostic":
        raise SystemExit("refusing to probe a non-diagnostic run")

    # ---- pre-run binding validation: refuse on ANY mismatch ----------------
    current = json.loads(
        (ROOT / "data" / "registry" / "corpus-current.json").read_text(encoding="utf-8")
    )
    client = QdrantClient(url=args.qdrant_url, timeout=60.0)
    problems = _binding_problems(config, args.dataset, current, client=client)
    if problems:
        raise SystemExit(
            "REFUSED: probe binding validation failed against run "
            f"{config.get('run_id')}:\n  - " + "\n  - ".join(problems)
        )
    prefetch = int(config["prefetch_limit"])
    collection = config["collection"]
    point_count = int(config["point_count"])

    cases = {
        row["id"]: row
        for row in args.dataset.read_text(encoding="utf-8").split("\n")
        if row.strip()
        for row in [json.loads(row)]
    }
    corpus = ChunkedCorpus()
    # point_id -> chunk_id map (one scroll over the published collection)
    id_map: dict[str, str] = {}
    offset = None
    while True:
        records, offset = client.scroll(
            collection_name=collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for record in records:
            chunk_id = (record.payload or {}).get("chunk_id")
            if chunk_id:
                id_map[str(record.id)] = str(chunk_id)
        if offset is None:
            break
    if len(id_map) != point_count:
        raise SystemExit(f"scroll map incomplete: {len(id_map)} != {point_count}")

    embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
    index = QdrantRetrievalIndex(
        client=client,
        collection_name=collection,
        vector_size=embedder.dimension,
        enable_bm25=True,
        hybrid_prefetch_limit=prefetch,
    )

    entries = []
    for case_path in sorted((run_dir / "cases").glob("G*.json")):
        result = json.loads(case_path.read_text(encoding="utf-8"))
        case = cases[result["id"]]
        for ev in result["evidence_results"]:
            if ev["match_rank"] is not None:
                continue
            evidence = case["evidence"][ev["evidence_index"]]
            doc = ev["doc_file"]
            entry: dict[str, object] = {
                "case": result["id"],
                "evidence_index": ev["evidence_index"],
                "doc_file": doc,
                "page": ev["page"],
                "usage_policy": ev["usage_policy"],
                "quote_head": (evidence.get("quote") or "")[:60],
            }
            quote_norm = _normalized(evidence.get("quote") or "")
            want_ids = set(evidence.get("element_ids") or [])
            carriers = [
                c
                for c in corpus.chunks.get(doc, [])
                if quote_norm and quote_norm in c["norm"] and want_ids and want_ids <= c["ids"]
            ]
            entry["carrier_count"] = len(carriers)
            entry["carrier_chunk_ids"] = [c["chunk_id"] for c in carriers]
            if carriers:
                dense_vector = embedder.embed_query(case["question"])
                dense_resp = client.query_points(
                    collection_name=collection,
                    query=list(dense_vector),
                    using="dense",
                    search_params=models.SearchParams(exact=True),
                    limit=point_count,
                    with_payload=False,
                )
                dense_ids = [id_map[str(p.id)] for p in dense_resp.points]
                bm25_resp = client.query_points(
                    collection_name=collection,
                    query=models.Document(
                        text=case["question"],
                        model="qdrant/bm25",
                        options=QdrantRetrievalIndex._bm25_options(),
                    ),
                    using="bm25",
                    limit=point_count,
                    with_payload=False,
                )
                bm25_ids = [id_map[str(p.id)] for p in bm25_resp.points]
                rrf_hits = index.search(
                    HybridQuery(text=case["question"], dense_vector=dense_vector),
                    mode="rrf",
                    filters=None,
                    limit=point_count,
                )
                rrf_ids = [h.chunk_id for h in rrf_hits]
                entry["question"] = case["question"]
                entry["dense_rank"] = min(
                    (r for r in (_rank_of(c["chunk_id"], dense_ids) for c in carriers) if r),
                    default=None,
                )
                entry["bm25_rank"] = min(
                    (r for r in (_rank_of(c["chunk_id"], bm25_ids) for c in carriers) if r),
                    default=None,
                )
                entry["rrf_rank_pf"] = min(
                    (r for r in (_rank_of(c["chunk_id"], rrf_ids) for c in carriers) if r),
                    default=None,
                )
                entry["in_dense_prefetch"] = bool(
                    entry["dense_rank"] is not None and entry["dense_rank"] <= prefetch
                )
                entry["in_bm25_prefetch"] = bool(
                    entry["bm25_rank"] is not None and entry["bm25_rank"] <= prefetch
                )
                entry["carrier_text_head"] = " ".join(carriers[0]["text"].split())[:80]
            entries.append(entry)

    out = run_dir / "miss_rerank_probe.json"
    out.write_text(
        json.dumps(
            {
                "run_id": config["run_id"],
                "dataset_sha256": config["dataset_sha256"],
                "corpus_version": config["corpus_version"],
                "collection": collection,
                "prefetch_limit": prefetch,
                "point_count": point_count,
                "probe_note": "read-only miss diagnosis: dense/bm25 full-corpus ranks + RRF fusion rank under the run's own prefetch limit",
                "misses": entries,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"probe -> {out} ({len(entries)} misses)")
    for e in entries:
        print(
            f"{e['case']}[{e['evidence_index']}] p{e['page']} "
            f"carriers={e['carrier_count']} dense={e.get('dense_rank')} "
            f"bm25={e.get('bm25_rank')} rrf@pf{prefetch}={e.get('rrf_rank_pf')}"
        )


if __name__ == "__main__":
    main()
