from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.application.corpus_publish import _read_jsonl as read_publish_jsonl
from src.evaluation.evidence_builder import _read_jsonl as read_evidence_jsonl
from src.evaluation.retrieval_chunk_builder import _read_jsonl as read_chunk_jsonl


class JsonlUnicodeSeparatorTest(unittest.TestCase):
    def test_unicode_line_separator_inside_json_string_is_not_a_record_boundary(self):
        records = [{"text": "\u7532\u2028\u4e59"}, {"text": "\u4e19"}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            with path.open("w", encoding="utf-8", newline="\n") as handle:
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")

            self.assertEqual(read_evidence_jsonl(path), records)
            self.assertEqual(read_chunk_jsonl(path), records)
            self.assertEqual(read_publish_jsonl(path), records)


if __name__ == "__main__":
    unittest.main()
