x = 10
y = 0

try:
  print(x / y)
except ZeroDivisionError:
  print("cannot divide by zero")

try:
  print(x / y)
except ZeroDivisionError as e:
  print("error:", e)

from pathlib import Path

p = Path("no_such_file.txt")

try:
  size = p.stat().st_size
  print(size)
except FileNotFoundError:
  print("file not found")

try:
  size = p.stat().st_size
except (FileNotFoundError, PermissionError):
  print("cannot access file")

try:
  print("do something")
finally:
  print("always runs")
