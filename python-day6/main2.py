import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--verbose", action="store_true")

args = parser.parse_args()
print(args.verbose)
