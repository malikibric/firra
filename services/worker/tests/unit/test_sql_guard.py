"""Guardrail: SQL lives only in firra/db and every statement is scoped by workspace_id.

Unscoped SQL is allowed only inside functions marked @cross_tenant_maintenance("reason").
"""

import ast
import re
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "firra"
DB_DIR = PACKAGE_ROOT / "db"
# Statement shapes, not bare keywords: prose such as "With a token..." must not match.
SQL_START = re.compile(
    r"^\s*(select\b[\s\S]*?\bfrom\b|select\s+\d|insert\s+into\b|update\s+\S+\s+set\b"
    r"|delete\s+from\b|with\s+\w+\s+as\s*\(|merge\s+into\b)",
    re.IGNORECASE,
)
MARKER = "cross_tenant_maintenance"

# Ignore SQL comments and literal contents, which cannot scope a statement.
SQL_NON_CODE = re.compile(
    r"--[^\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'"
    r"|(?P<tag>\$(?:\w+)?\$)[\s\S]*?(?P=tag)"
)
PARAMETER = r"%(?:\(\w+\))?s"
WORKSPACE_COLUMN = r"(?:\b\w+\.)?\bworkspace_id\b"
SCOPE_EQUALITY = re.compile(
    rf"(?:{WORKSPACE_COLUMN}\s*=\s*{PARAMETER}|{PARAMETER}\s*=\s*{WORKSPACE_COLUMN})",
    re.IGNORECASE,
)
WORKSPACE_JOIN = re.compile(
    r"(?P<left>\b\w+\.workspace_id)\s*=\s*(?P<right>\b\w+\.workspace_id)",
    re.IGNORECASE,
)
PREDICATE_CLAUSE = re.compile(
    r"\b(?:where|on|having)\b(.*?)(?=\b(?:select|from|join|group|order|limit|returning"
    r"|set|union|values)\b|$)",
    re.IGNORECASE | re.DOTALL,
)
JOIN_USING = re.compile(
    r"\bjoin\s+[\w.]+(?:\s+(?:as\s+)?\w+)?\s+using\s*\([^)]*\bworkspace_id\b[^)]*\)",
    re.IGNORECASE,
)
INSERT = re.compile(
    r"^\s*insert\s+into\s+([\w.]+)\s*\(([^()]*)\)\s*(values\s*\(|select\s+)",
    re.IGNORECASE,
)


def _expressions(sql: str) -> list[str]:
    """Split an INSERT's expressions, stopping at its closing parenthesis or FROM."""
    expressions: list[str] = []
    depth = start = 0
    for token in re.finditer(r"[()\[\],]|\bfrom\b", sql, re.IGNORECASE):
        value = token.group().lower()
        if depth == 0 and value in (")", "from"):
            return [*expressions, sql[start : token.start()].strip()]
        if value in ("(", "["):
            depth += 1
        elif value in (")", "]"):
            depth -= 1
        elif value == "," and depth == 0:
            expressions.append(sql[start : token.start()].strip())
            start = token.end()
    return [*expressions, sql[start:].strip()]


def _has_scope(sql: str) -> bool:
    sql = SQL_NON_CODE.sub("''", sql)
    # Quoted identifiers are still identifiers, unlike quoted literal contents.
    sql = re.sub(r'"(\w+)"', r"\1", sql)
    statements = [statement.strip() for statement in sql.split(";") if statement.strip()]
    return bool(statements) and all(_statement_has_scope(statement) for statement in statements)


def _statement_has_scope(sql: str) -> bool:
    for clause in PREDICATE_CLAUSE.finditer(sql):
        if SCOPE_EQUALITY.search(clause[1]):
            return True
        if any(
            join["left"].lower() != join["right"].lower()
            for join in WORKSPACE_JOIN.finditer(clause[1])
        ):
            return True
        # The workspace root table has id, not workspace_id.
        if re.search(r"\b(?:from|update)\s+public\.workspace\b", sql, re.IGNORECASE) and re.search(
            r"\b(?:\w+\.)?id\s*=\s*%\(workspace_id\)s", clause[1], re.IGNORECASE
        ):
            return True
    if JOIN_USING.search(sql):
        return True
    insert = INSERT.match(sql)
    if insert is None:
        return False
    columns = [column.strip().lower() for column in insert[2].split(",")]
    column = "id" if insert[1].lower() == "public.workspace" else "workspace_id"
    if column not in columns:
        return False
    remainder = re.split(
        r"\bon\s+conflict\b", sql[insert.end() :], maxsplit=1, flags=re.IGNORECASE
    )[0]
    # INSERT SELECT from tenant tables also needs a predicate; ensure_routines reads only unnest.
    if re.search(r"\bselect\b|\bfrom\s+(?!unnest\s*\()[\w.]", remainder, re.IGNORECASE):
        return False
    expressions = _expressions(remainder)
    index = columns.index(column)
    return index < len(expressions) and bool(
        re.fullmatch(
            r"(?:%s|%\(workspace_id\)s)(?:\s*::\s*uuid)?", expressions[index], re.IGNORECASE
        )
    )


def _is_marked(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
        if name == MARKER:
            return True
    return False


def find_violations(source: str, *, in_db_dir: bool) -> list[str]:
    tree = ast.parse(source)
    marked: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _is_marked(node):
            marked.update(id(child) for child in ast.walk(node))

    violations: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if not SQL_START.search(node.value):
            continue
        if not in_db_dir:
            violations.append(f"line {node.lineno}: SQL outside firra/db")
        elif id(node) not in marked and not _has_scope(node.value):
            violations.append(f"line {node.lineno}: SQL without workspace scope")
    return violations


def test_codebase_has_no_unscoped_sql() -> None:
    problems: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        in_db_dir = DB_DIR in path.parents
        for violation in find_violations(path.read_text(), in_db_dir=in_db_dir):
            problems.append(f"{path.relative_to(PACKAGE_ROOT.parent)} {violation}")
    assert problems == []


def test_guard_flags_unscoped_query() -> None:
    source = 'Q = "select * from public.job where id = %s"\n'
    assert find_violations(source, in_db_dir=True) == ["line 1: SQL without workspace scope"]


def test_guard_flags_sql_outside_db_dir() -> None:
    source = 'Q = "select 1 from public.job where workspace_id = %s"\n'
    assert find_violations(source, in_db_dir=False) == ["line 1: SQL outside firra/db"]


def test_guard_allows_scoped_and_marked_queries() -> None:
    source = (
        'Q = "update public.job set status = 1 where workspace_id = %s"\n'
        "@cross_tenant_maintenance('purge sweep')\n"
        "def purge():\n"
        '    return "delete from public.x where purged"\n'
    )
    assert find_violations(source, in_db_dir=True) == []


def test_guard_ignores_prose_that_starts_with_sql_keywords() -> None:
    source = '"""With a sync token: changes since then."""\nX = "Update the cursor later"\n'
    assert find_violations(source, in_db_dir=False) == []


def test_guard_recognizes_ctes_and_select_literals() -> None:
    source = 'Q = "with due as (select id from public.x) delete from public.x"\nP = "select 1"\n'
    assert find_violations(source, in_db_dir=False) == [
        "line 1: SQL outside firra/db",
        "line 2: SQL outside firra/db",
    ]


@pytest.mark.parametrize(
    "sql",
    [
        "select workspace_id, counts from public.routine_run",
        "select counts from public.routine_run order by workspace_id",
        "select workspace_id = %s from public.routine_run",
        "select counts from public.routine_run where id = %s -- workspace_id = %s",
        "select counts from public.routine_run /* where workspace_id = %s */",
        "select counts from public.routine_run where kind = 'workspace_id = %s'",
        "select counts from public.routine_run where kind = $$where workspace_id = %s$$",
        "select counts from public.routine_run where kind = $q$where workspace_id = %s$q$",
        "select workspace_id from public.routine_run rr where rr.workspace_id = rr.workspace_id",
        "select counts from public.routine_run where workspace_id = %s; "
        "select workspace_id, counts from public.routine_run",
        "update public.routine_run set workspace_id = %s where id = %s",
        "insert into public.routine_run (workspace_id, counts) values ('fixed', %s)",
        "insert into public.routine_run (workspace_id, counts) values (%(counts)s, 'fixed')",
        "insert into public.routine_run (workspace_id, counts) select workspace_id, counts "
        "from public.routine_run",
        "insert into public.routine_run (workspace_id, counts) select %s, counts "
        "from public.routine_run",
    ],
)
def test_guard_rejects_workspace_mentions_without_scope(sql: str) -> None:
    assert find_violations(f"Q = {sql!r}", in_db_dir=True) == [
        "line 1: SQL without workspace scope"
    ]


@pytest.mark.parametrize(
    "sql",
    [
        "select counts from public.routine_run where workspace_id = %s",
        "select counts from public.routine_run where %(workspace_id)s = workspace_id",
        'select counts from public.routine_run where "workspace_id" = %(workspace_id)s::uuid',
        "select counts from public.routine_run rr join public.routine r "
        "on r.workspace_id = rr.workspace_id and r.id = rr.routine_id",
        "select counts from public.routine_run rr join public.routine r using (workspace_id)",
        "select counts from public.routine_run where id = %s and workspace_id = %s",
        "delete from public.routine_run where workspace_id = %s",
        "select timezone from public.workspace where id = %(workspace_id)s",
        "insert into public.routine_run (workspace_id, counts) values (%s, %s)",
        "insert into public.routine_run (counts, workspace_id) "
        "values (jsonb_build_object('x', 1), %s)",
        "insert into public.routine (workspace_id, kind) select %(workspace_id)s, kind "
        "from unnest(array['morning_scan', 'commitment_watch']) kind",
        "insert into public.routine_run (workspace_id, routine_id) "
        "select workspace_id, id from public.routine where workspace_id = %s",
    ],
)
def test_guard_allows_predicates_joins_and_parameter_scoped_inserts(sql: str) -> None:
    assert find_violations(f"Q = {sql!r}", in_db_dir=True) == []


@pytest.mark.parametrize("path", sorted(DB_DIR.glob("*.py")), ids=lambda path: path.name)
def test_every_current_repository_has_scoped_or_marked_sql(path: Path) -> None:
    assert find_violations(path.read_text(), in_db_dir=True) == []
