"""Public Python API gate, including imports deferred until a human claim."""
from __future__ import annotations

import ast
import importlib
from pathlib import Path
import unittest
from unittest.mock import patch

import account_session_resume_state_contract as contract
import account_session_resume_plan_store as store
from auto_restart_runtime import validate_auto_restart_request_at_claim


ROOT = Path(contract.__file__).resolve().parent
SYMBOL = "RESUME_STATE_RESUME_REQUESTED"


class ResumeStatePythonApiContractGate(unittest.TestCase):
    def test_canonical_symbol_and_compatibility_export_have_exact_value(self):
        self.assertEqual(getattr(contract, SYMBOL), "resume_requested")
        self.assertEqual(getattr(store, SYMBOL), getattr(contract, SYMBOL))

    def test_single_definition_and_all_runtime_symbol_imports_resolve(self):
        definitions = []
        for source in ROOT.glob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == SYMBOL for target in node.targets
                ):
                    definitions.append(source.name)
                if isinstance(node, ast.ImportFrom) and node.module in {
                    "account_session_resume_state_contract", "account_session_resume_plan_store"
                }:
                    for alias in node.names:
                        if alias.name.startswith("RESUME_STATE_"):
                            module = importlib.import_module(node.module)
                            self.assertTrue(hasattr(module, alias.name), (source.name, node.lineno, alias.name))
                            self.assertIn(getattr(module, alias.name), contract.DB_ALLOWED_RESUME_STATES)
        self.assertEqual(definitions, ["account_session_resume_state_contract.py"])

    def test_lazy_human_import_executes_before_invalid_request_is_refused(self):
        with patch.object(store, "load_resume_plan") as load:
            self.assertEqual(validate_auto_restart_request_at_claim(
                account_id="synthetic-account", metadata={
                    "auto_restart": True, "source": "auto_restart_tick",
                    "recovery_mode": "human_confirmed_resume",
                }), (False, "resume_plan_invalid", None))
            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
