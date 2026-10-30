#!/usr/bin/env python3
"""
validate_schema.py — GraphQL schema validator using graphql-core.

Usage: python3 validate_schema.py <schema.graphql>
Exit 0 if valid, exit 1 with parse error on stderr if invalid.
"""
import sys
from graphql import build_schema, GraphQLSyntaxError


def validate(path: str) -> tuple[bool, str]:
    """Return (is_valid, error_message)."""
    try:
        text = open(path).read()
        build_schema(text)
        return True, ""
    except GraphQLSyntaxError as e:
        # Format the error with context
        lines = text.split('\n') if 'text' in dir() else []
        msg = str(e)
        return False, msg
    except Exception as e:
        return False, str(e)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: validate_schema.py <schema.graphql>", file=sys.stderr)
        sys.exit(2)
    ok, err = validate(sys.argv[1])
    if ok:
        print(f"VALID: {sys.argv[1]}")
        sys.exit(0)
    else:
        print(f"INVALID: {sys.argv[1]}: {err}", file=sys.stderr)
        sys.exit(1)