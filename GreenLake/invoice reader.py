import tkinter as tk
from tkinter import filedialog
import os
from pypdf import PdfReader   # use pypdf (recommended)

# Hide GUI
root = tk.Tk()
root.withdraw()

# Select multiple files
print("Select one or more files (PDF/TXT):")
file_paths = filedialog.askopenfilenames()

combined_content = ""

for file_path in file_paths:
    print(f"Processing: {file_path}")

    if file_path.lower().endswith(".pdf"):
        reader = PdfReader(file_path)

        for page in reader.pages:
            text = page.extract_text()
            if text:
                combined_content += text + "\n"

    else:
        with open(file_path, "r", encoding="utf-8") as f:
            combined_content += f.read() + "\n"

# Save combined text
output_txt = "combined_output.txt"
with open(output_txt, "w", encoding="utf-8") as f:
    f.write(combined_content)

print(f"\n✅ Combined text saved as: {output_txt}")

# Ask input method
choice = input("\nHow do you want to input words?\n1. From file\n2. Paste in terminal\nEnter 1 or 2: ")

# Get words
if choice == "1":
    print("Select the WORDS file:")
    words_file = filedialog.askopenfilename()
    with open(words_file, "r", encoding="utf-8") as f:
        words = [line.strip() for line in f if line.strip()]

elif choice == "2":
    print("\nPaste your words (press ENTER twice to finish):")
    words = []
    while True:
        line = input()
        if line == "":
            break
        words.append(line.strip())

else:
    print("Invalid choice!")
    exit()

# Faster lookup
content_set = set(combined_content.split())

found = []
not_found = []

# Check words
for word in words:
    if word in content_set:
        found.append(word)
    else:
        not_found.append(word)

# Output
print("\n✅ FOUND:")
for f in found:
    print(f)

print("\n❌ NOT FOUND:")
for nf in not_found:
    print(nf)

# Save results
with open("found.txt", "w") as f:
    f.write("\n".join(found))

with open("not_found.txt", "w") as f:
    f.write("\n".join(not_found))

print("\n📁 Results saved as found.txt and not_found.txt")