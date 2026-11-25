import csv

with open("repos_raw.csv", "r", newline="", encoding="utf-8-sig") as f:
    reader = csv.reader(f)
    print(reader)          # <_csv.reader object at 0x...>
    print(list(reader))    # []  — если файл реально пуст