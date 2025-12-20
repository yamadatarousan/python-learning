from pathlib import Path
import argparse

parser = argparse.ArgumentParser()
parser.add_argument(
  "directory",
  nargs="?",
  default=Path("."),
  type=Path,
)

args = parser.parse_args()
print(args.directory)
