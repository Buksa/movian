#!/usr/bin/env python3
"""Deterministic tests for binary-specific lifecycle inventory generation."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "support" / "devtools" / "gdb"))

from inventory import (  # noqa: E402
    CONTRACTS,
    canonical_json,
    generate_inventory,
    parse_nm_output,
    validate_contracts,
    validate_inventory,
)


class NmParsing(unittest.TestCase):
    def test_skips_malformed_and_keeps_first_exact_record(self):
        records = parse_nm_output(
            "bad-line\n"
            "main_init T 10 20\n"
            "main_init T 30 20\n"
            "not-an-address T nope 1\n"
        )
        self.assertEqual(records["main_init"]["address"], "0x10")
        self.assertEqual(records["main_init"]["size"], 0x20)
        self.assertEqual(set(records), {"main_init"})


class ContractValidation(unittest.TestCase):
    def test_repository_contracts_have_unique_ids_and_symbols(self):
        validate_contracts()

    def test_duplicate_contract_is_rejected(self):
        duplicate = list(CONTRACTS) + [dict(CONTRACTS[0])]
        with self.assertRaisesRegex(ValueError, "duplicate contract id"):
            validate_contracts(duplicate)


class InventoryGeneration(unittest.TestCase):
    def test_generation_is_binary_evidenced_and_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "movian"
            binary.write_bytes(b"debug-binary")
            records = {
                "main_init": {"address": "0x10", "type": "T", "size": 4},
                "main_fini": {"address": "0x20", "type": "T", "size": 4},
                "hts_thread_create_detached": {
                    "address": "0x30", "type": "T", "size": 4,
                },
            }
            first = generate_inventory(binary, nm_records=records)
            second = generate_inventory(binary, nm_records=records)
            self.assertEqual(canonical_json(first), canonical_json(second))
            self.assertEqual(first["count"], 3)
            self.assertEqual(first["categoryCounts"]["core-init"], 2)
            self.assertEqual(first["categoryCounts"]["thread-create"], 1)
            self.assertTrue(all(entry["binaryEvidence"] for entry in first["entries"]))
            self.assertIn("app_shutdown", {
                item["symbol"] for item in first["missingCandidates"]
            })
            validate_inventory(first)

    def test_inventory_rejects_duplicate_missing_and_bad_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "movian"
            binary.write_bytes(b"debug-binary")
            records = {
                "main_init": {"address": "0x10", "type": "T"},
            }
            inventory = generate_inventory(binary, nm_records=records)

            duplicate = copy.deepcopy(inventory)
            duplicate["entries"].append(copy.deepcopy(duplicate["entries"][0]))
            duplicate["count"] += 1
            with self.assertRaisesRegex(ValueError, "duplicate"):
                validate_inventory(duplicate)

            missing_evidence = copy.deepcopy(inventory)
            missing_evidence["entries"][0].pop("binaryEvidence")
            with self.assertRaisesRegex(ValueError, "binary evidence"):
                validate_inventory(missing_evidence)

            bad_count = copy.deepcopy(inventory)
            bad_count["categoryCounts"]["core-init"] += 1
            with self.assertRaisesRegex(ValueError, "category count"):
                validate_inventory(bad_count)


if __name__ == "__main__":
    unittest.main()
