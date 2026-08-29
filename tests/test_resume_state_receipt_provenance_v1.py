from __future__ import annotations

import hashlib
import unittest

from follow60_lock_v3 import (
    RESUME_CONTRACT_GATES,
    RESUME_CONTRACT_SOURCE_FILES,
    RESUME_RECEIPT_HASH_CONTRACT,
    bind_resume_receipt_provenance,
    canonical_resume_receipt_bytes,
    verify_resume_state_contract_evidence,
)


class ResumeStateReceiptProvenanceV1Tests(unittest.TestCase):
    def _receipt(self) -> dict:
        return {
            "schema": "RESUME_STATE_END_TO_END_CONTRACT_V1",
            "status": "PASS",
            "gates": {name: "PASS" for name in RESUME_CONTRACT_GATES},
            "backend_sha": "b" * 40,
            "source_files": {name: "a" * 64 for name in RESUME_CONTRACT_SOURCE_FILES},
            "production_mutations": 0,
        }

    def _manifest(self, receipt: dict) -> dict:
        return {
            "resume_state_contract": receipt,
            "protected_entries": {
                name: {"sha256": "a" * 64}
                for name in RESUME_CONTRACT_SOURCE_FILES
            },
        }

    def test_generator_binding_hashes_exact_prebinding_receipt_bytes(self) -> None:
        receipt = self._receipt()
        prebound = dict(receipt)
        prebound["receipt_hash_contract"] = RESUME_RECEIPT_HASH_CONTRACT
        expected = hashlib.sha256(canonical_resume_receipt_bytes(prebound)).hexdigest()
        bound = bind_resume_receipt_provenance(receipt)
        self.assertEqual(bound["receipt_sha256"], expected)
        self.assertEqual(bound["receipt_hash_contract"], RESUME_RECEIPT_HASH_CONTRACT)
        self.assertNotIn("receipt_sha256", receipt)
        self.assertTrue(verify_resume_state_contract_evidence(self._manifest(bound))["ok"])

    def test_any_bound_receipt_mutation_fails_self_verification(self) -> None:
        bound = bind_resume_receipt_provenance(self._receipt())
        bound["production_mutations"] = 1
        result = verify_resume_state_contract_evidence(self._manifest(bound))
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["reason"],
            "resume_state_contract_receipt_sha256_mismatch",
        )

    def test_missing_provenance_still_fails_closed(self) -> None:
        result = verify_resume_state_contract_evidence(self._manifest(self._receipt()))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "resume_state_contract_provenance_missing")


if __name__ == "__main__":
    unittest.main()
