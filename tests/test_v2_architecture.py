import ast
from pathlib import Path
import unittest

class ArchitectureTests(unittest.TestCase):
    def test_domain_does_not_import_execution_io_cli_or_storage(self):
        root=Path(__file__).resolve().parents[1]/"taskclosurekit/domain"
        forbidden={"os","pathlib","subprocess","sys","time"}
        for p in root.glob("*.py"):
            for node in ast.walk(ast.parse(p.read_text())):
                modules=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or ""] if isinstance(node,ast.ImportFrom) else []
                for module in modules:
                    self.assertFalse(module.split(".")[0] in forbidden,(p.name,module))
                    self.assertFalse(any(x in module.split(".") for x in ("cli","storage","execution","snapshots","application","_primitives")),(p.name,module))
