# -*- coding: utf-8 -*-
"""Wrapper - loads app from .pyc bytecode"""
import sys, types, marshal
sys.path.insert(0, r'D:\python-leanrn\codex')

with open(r'D:\python-leanrn\codex\__pycache__\app.cpython-311.pyc', 'rb') as f:
    header = f.read(16)
    code = marshal.load(f)

module = types.ModuleType('app')
module.__file__ = r'D:\python-leanrn\codex\app.py'
sys.modules['app'] = module
exec(code, module.__dict__)

# Re-export
app = module.app
process_question = module.process_question

if __name__ == '__main__':
    print('='*50)
    print('  Flask app http://127.0.0.1:7860')
    print('='*50)
    module.app.run(host='127.0.0.1', port=7860, debug=False)
