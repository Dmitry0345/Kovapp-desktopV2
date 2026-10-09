# Печатает числовую версию Kovapp.exe (напр. 3.0.12.0) из version_info.txt — для build.cmd.
import os
import re

here = os.path.dirname(os.path.abspath(__file__))
text = open(os.path.join(here, "version_info.txt"), encoding="utf-8").read()
m = re.search(r"filevers=\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)", text)
print(".".join(m.groups()) if m else "0.0.0.0")
