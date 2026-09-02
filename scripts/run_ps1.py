# -*- coding: utf-8 -*-
"""以 base64 EncodedCommand 方式执行 ps1（规避 PS5.1 无 BOM 中文乱码）"""
import base64
import subprocess
import sys

ps1 = sys.argv[1]
with open(ps1, encoding="utf-8") as f:
    code = f.read()
b64 = base64.b64encode(code.encode("utf-16-le")).decode("ascii")
r = subprocess.run(
    ["powershell", "-ExecutionPolicy", "Bypass", "-EncodedCommand", b64],
    capture_output=True, text=True,
)
print(r.stdout)
print(r.stderr)
sys.exit(r.returncode)
