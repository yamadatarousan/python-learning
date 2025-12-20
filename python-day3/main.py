def greet(name):
  print(f"hello {name}")

greet("alice")
greet("bob")

def add(a, b):
  return a + b

result = add(3, 5)
print(result)

def is_even(n):
  if n % 2 == 0:
    return True
  else:
    return False

print(is_even(4))
print(is_even(5))

numbers = [1, 2, 3, 4, 5, 6]

def filter_even(nums):
  result = []
  for n in nums:
    if n % 2 == 0:
      result.append(n)
  return result

evens = filter_even(numbers)
print(evens)

def greater_then(nums, threshold):
  result = []
  for n in nums:
      if n > threshold:
        result.append(n)
  return result

print(greater_then(numbers, 3))

users = [
    {"name": "alice", "age": 20},
    {"name": "bob", "age": 25},
    {"name": "carol", "age": 30},
]

def get_names_over_age(users, age):
  result = []
  for user in users:
    if user["age"] >= age:
      result.append(user["name"])
  return result

print(get_names_over_age(users, 25))

