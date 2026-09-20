import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "funding_story"


def test_application_and_domain_are_framework_and_infrastructure_independent():
    forbidden = ("fastapi", "celery", "psycopg", "infrastructure")
    for layer in ("application", "domain"):
        for source in (PACKAGE / layer).rglob("*.py"):
            tree = ast.parse(source.read_text())
            imports = [
                alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
            ] + [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            assert not any(name in module.split(".") for module in imports for name in forbidden), source


def test_composition_root_is_the_only_runtime_entry_point_importing_infrastructure():
    for name in ("api.py", "graph.py", "provider.py", "tasks.py"):
        assert "infrastructure" not in (PACKAGE / name).read_text(), name
    assert "infrastructure" in (PACKAGE / "bootstrap.py").read_text()
    assert not (PACKAGE / "assets.py").exists()
    assert not (PACKAGE / "store.py").exists()


def test_content_insight_domain_modules_do_not_depend_on_web_or_worker_frameworks():
    forbidden = ("fastapi", "celery", "psycopg", "infrastructure")
    for name in ("models.py", "policy.py", "generators.py", "service.py", "worker.py"):
        source = PACKAGE / "content_insights" / name
        tree = ast.parse(source.read_text())
        imports = [
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        ] + [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(part in module.split(".") for module in imports for part in forbidden), source
