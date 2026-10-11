"""Safe arithmetic evaluator so all Level 2 numbers come from code, not from the model's mental maths."""
from __future__ import annotations

import ast
import operator
import statistics


def _clean(xs):
    return [x for x in xs if isinstance(x, (int, float))]


FUNCS = {
    "sum": lambda xs: sum(_clean(xs)),
    "mean": lambda xs: statistics.mean(_clean(xs)),
    "avg": lambda xs: statistics.mean(_clean(xs)),
    "median": lambda xs: statistics.median(_clean(xs)),
    "min": lambda *a: min(_clean(a[0]) if len(a) == 1 else _clean(a)),
    "max": lambda *a: max(_clean(a[0]) if len(a) == 1 else _clean(a)),
    "len": lambda xs: len(_clean(xs)),
    "count": lambda xs: len(_clean(xs)),
    "abs": abs,
    "round": lambda x, n=2: round(x, int(n)),
    "pct": lambda a, b: a / b * 100,
    "pct_change": lambda new, old: (new - old) / abs(old) * 100,
    "count_above": lambda xs, t: sum(1 for x in _clean(xs) if x > t),
    "count_at_least": lambda xs, t: sum(1 for x in _clean(xs) if x >= t),
    "count_below": lambda xs, t: sum(1 for x in _clean(xs) if x < t),
    "last": lambda xs, n=1: _clean(xs)[-int(n):],
    "first": lambda xs, n=1: _clean(xs)[:int(n)],
}

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.Pow: operator.pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv,
       ast.USub: operator.neg, ast.UAdd: operator.pos}


class CalcError(ValueError):
    pass


def evaluate(expr: str, variables: dict):
    """Evaluate an arithmetic expression over known variables and whitelisted functions only."""
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise CalcError(f"unknown variable '{node.id}'")
            v = variables[node.id]
            if v is None:
                raise CalcError(f"'{node.id}' is not printed on the bill")
            return v
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            return OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
            return OPS[type(node.op)](ev(node.operand))
        if isinstance(node, ast.List):
            return [ev(e) for e in node.elts]
        if isinstance(node, ast.Subscript):
            return ev(node.value)[ev(node.slice)]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fn = FUNCS.get(node.func.id) or variables.get(node.func.id)
            if callable(fn):
                return fn(*[ev(a) for a in node.args])
        raise CalcError(f"unsupported expression element: {ast.dump(node)[:60]}")

    try:
        result = ev(ast.parse(expr, mode="eval"))
    except CalcError:
        raise
    except Exception as e:
        raise CalcError(f"{type(e).__name__}: {e}") from e
    if isinstance(result, float):
        result = round(result, 4)
    return result
