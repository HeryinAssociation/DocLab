#!/usr/bin/env python
"""JSON-only entry point for the Doclab intake MCP and durable worker."""
from __future__ import annotations
import json
import sys
from core import intake

if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':
        intake.worker(sys.argv[2])
    else:
        try:
            request = json.load(sys.stdin)
            result = intake.dispatch(request['tool'], request.get('arguments') or {})
            print(json.dumps(result, ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({'ok': False, 'error': type(exc).__name__,
                              'message': str(exc)}, ensure_ascii=False))
            sys.exit(1)
