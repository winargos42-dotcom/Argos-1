import ast
import re
from decimal import Decimal, DecimalException, DivisionByZero, localcontext


_PREFIX = re.compile(r"^(?:вычисли|посчитай|сколько\s+будет|calculate)\b\s*:?\s*", re.IGNORECASE)
_NUMBER = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
_MAX_VALUE = Decimal("1e50")
_MIN_VALUE = Decimal("1e-50")


def _bounded(value: Decimal) -> Decimal:
    magnitude = value.copy_abs()
    if not value.is_finite() or magnitude > _MAX_VALUE or (value and magnitude < _MIN_VALUE):
        raise ValueError("число выходит за допустимые пределы")
    return value


def _calculate(node, source: str, depth: int = 0) -> Decimal:
    if depth > 16:
        raise ValueError("выражение слишком сложное")
    if isinstance(node, ast.Expression):
        return _calculate(node.body, source, depth + 1)
    if isinstance(node, ast.Constant):
        literal = ast.get_source_segment(source, node)
        if literal is None or not _NUMBER.fullmatch(literal):
            raise ValueError("неподдерживаемое число")
        return _bounded(Decimal(literal))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _calculate(node.operand, source, depth + 1)
        return value if isinstance(node.op, ast.UAdd) else value.copy_negate()
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
        left = _calculate(node.left, source, depth + 1)
        right = _calculate(node.right, source, depth + 1)
        if isinstance(node.op, ast.Add):
            result = left + right
        elif isinstance(node.op, ast.Sub):
            result = left - right
        elif isinstance(node.op, ast.Mult):
            result = left * right
        else:
            if not right:
                raise DivisionByZero
            result = left / right
        return _bounded(result)
    raise ValueError("поддерживаются только +, -, *, / и скобки")


def try_calculate(text: str) -> str | None:
    sample = text[:513].strip()
    prefix = _PREFIX.match(sample)
    arithmetic = bool(re.fullmatch(r"[0-9.,+*/()\s-]+", sample) and re.search(r"[+*/-]", sample))
    if not prefix and not arithmetic:
        return None
    if len(text) > 512:
        return "Ошибка: выражение длиннее 512 символов."
    expression = text.strip()
    if prefix:
        expression = expression[prefix.end():]
    expression = re.sub(r"\.\s*ответь\s+только\s+числом\.?$", "", expression, flags=re.IGNORECASE)
    expression = expression.rstrip("?").strip().replace(",", ".")
    if not prefix and re.fullmatch(r"[+-]?\s*(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", expression):
        return None
    if not expression or not re.fullmatch(r"[0-9.+*/()\s-]+", expression):
        return "Ошибка: допустимы только числа, +, -, *, / и скобки."
    try:
        nesting = 0
        for character in expression:
            nesting += (character == "(") - (character == ")")
            if nesting > 16:
                raise ValueError("слишком много вложенных скобок")
        if any(sum(c.isdigit() for c in literal) > 50 for literal in _NUMBER.findall(expression)):
            raise ValueError("число длиннее 50 цифр")
        tree = ast.parse(expression, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 128:
            raise ValueError("выражение слишком сложное")
        with localcontext() as context:
            context.prec = 50
            context.Emax = 50
            context.Emin = -50
            result = _calculate(tree, expression)
        if not result:
            return "0"
        output = format(result, "f")
        return output.rstrip("0").rstrip(".") if "." in output else output
    except DivisionByZero:
        return "Ошибка: деление на ноль."
    except (SyntaxError, ValueError, RecursionError, DecimalException):
        return "Ошибка: неверное выражение или превышены пределы вычисления."
