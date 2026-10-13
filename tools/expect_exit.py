#!/usr/bin/env python3
"""Run a command and succeed only if it exits with EXACTLY the expected code
and its output matches PATTERN (e.g. the release gate must refuse with 3 and
'BLOCKED:'). Any other outcome -- success, tool failure, traceback -- fails.

Usage: expect_exit.py CODE PATTERN -- command [args...]
"""
import re
import subprocess
import sys


def main(argv: list[str]) -> int:
    if len(argv) < 5 or argv[3] != '--':
        print('usage: expect_exit.py CODE PATTERN -- command [args...]', file=sys.stderr)
        return 1
    code, pattern, command = int(argv[1]), argv[2], argv[4:]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        print(f'FAIL: cannot run {command[0]}: {exc}', file=sys.stderr)
        return 1
    output = result.stdout + result.stderr
    sys.stdout.write(output)
    if result.returncode != code:
        print(f'FAIL: exit {result.returncode}, expected {code}', file=sys.stderr)
    elif 'Traceback (most recent call last)' in output:
        print('FAIL: unexpected Python exception in output', file=sys.stderr)
    elif not re.search(pattern, output, re.M):
        print(f'FAIL: output lacks /{pattern}/', file=sys.stderr)
    else:
        print(f'OK: exit {code} with /{pattern}/ as expected')
        return 0
    return 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
