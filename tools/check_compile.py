import py_compile, traceback, sys

try:
    py_compile.compile(r'c:\\Users\\dca77\\DV-Odonto\\app.py', doraise=True)
    print('OK')
except Exception:
    print('ERR')
    traceback.print_exc()
    sys.exit(1)
