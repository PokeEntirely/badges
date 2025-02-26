with open('games.txt', 'r') as f:
    lines = f.readlines()
unique = []
seen = set()
for line in lines:
    if line not in seen:
        seen.add(line)
        unique.append(line)
removed_count = len(lines) - len(unique)
with open('games.txt', 'w') as f:
    f.writelines(unique)
print("Original lines:", len(unique))
print("Removed duplicate lines:", removed_count)
