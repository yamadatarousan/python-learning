numbers = [1, 2, 3, 4, 5]
print(numbers)
print(numbers[0])
print(numbers[-1])
print(numbers[-2])

for n in numbers:
  print(n * 2)

for n in numbers:
  if n % 2 == 0:
    print(f"even: {n}")

users = [
  {"name": "alice", "age": 20},
  {"name": "bob", "age": 25},
  {"name": "carol", "age": 30},
]

for user in users:
  if user["age"] >= 25:
    print(user["name"])

older = [u for u in users if u["age"] >= 25]
print(older)

