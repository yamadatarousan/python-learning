print("hello python")

x = 10
y = 3

print(x + y)

x = 10

if x > 5:
  print("x is large")
else:
  print("x is small")

for i in range(5):
  print(i)

names = ["alice", "bob", "carol"]

for name in names:
  print(name)

def add(a, b):
  return a + b

result = add(2, 3)
print(result)

def print_even_numbers(n):
  for i in range(n):
    if i % 2 == 0:
      print(i)

print_even_numbers(10)

numbers = [1, 2, 3, 4, 5]
evens = [n for n in numbers if n % 2 == 0]
print(evens)
