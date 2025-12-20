from pathlib import Path

p = Path(".")
print(p)

print(p.exists())
print(p.is_dir())
print(p.is_file())

for child in p.iterdir():
  print(child)

for child in p.iterdir():
  if child.is_file():
    print("file:", child)

for child in p.iterdir():
    if child.is_dir():
      print("dir:", child)

for path in p.rglob("*"):
  print(path)

for path in p.rglob("*.py"):
  print(path)
