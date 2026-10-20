import os
import glob
import json
import re

folders = sorted([d for d in glob.glob("M*") if os.path.isdir(d)])

if not folders:
    print("Не найдено папок вида M01, M02...")
    exit()

print(f"Найдено папок с моделями: {len(folders)}")

os.makedirs("results", exist_ok=True)

key_map = {}
all_data = {}  # { step: { model_code: content } }
steps_order = ["q1", "q2", "q3", "q4", "q5", "q6"]

for s in steps_order:
    all_data[s] = {}

merged_records = []

def parse_markdown_blind(filepath):
    """Парсит текст с разделителями вида # Q1 ... # Q6"""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()
    
    # Разбиваем по заголовкам вида # Q1, # Q2 и т.д.
    pattern = r"(?i)#+\s*(q[1-6])\b"
    splits = re.split(pattern, text)
    
    res = {}
    if len(splits) > 1:
        for i in range(1, len(splits), 2):
            step_name = splits[i].lower()
            content = splits[i+1].strip()
            # Убираем подзаголовок вида "## M.. / прогон 1", если он есть в начале
            content = re.sub(r"^##\s*M\d+.*?\n", "", content).strip()
            res[step_name] = content
    return res

for folder in folders:
    prefix = folder.split("-")[0].strip()
    key_map[prefix] = folder

    jsonl_path = os.path.join(folder, "results.jsonl")
    if not os.path.exists(jsonl_path):
        sub_jsonl = os.path.join(folder, "results", "results.jsonl")
        if os.path.exists(sub_jsonl):
            jsonl_path = sub_jsonl

    # Вариант 1: Есть results.jsonl
    if os.path.exists(jsonl_path):
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                merged_records.append(rec)
                step = rec.get("step")
                content = rec.get("content", "").strip()
                if step in all_data:
                    all_data[step][prefix] = content
        continue

    # Вариант 2: Нет jsonl, ищем blind.md или txt файл
    md_files = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(('.md', '.txt'))]
    parsed = {}
    chosen_file = None
    
    for fpath in md_files:
        p = parse_markdown_blind(fpath)
        if p:
            parsed = p
            chosen_file = os.path.basename(fpath)
            break
            
    if parsed:
        print(f"Спарсен ручной файл {chosen_file} для {folder} 👍")
        for step, content in parsed.items():
            if step in all_data:
                all_data[step][prefix] = content
            # Генерируем запись в results.jsonl, чтобы всё было единообразно
            merged_records.append({
                "model": folder,
                "run": 1,
                "step": step,
                "status": "ok",
                "content": content,
                "source": "manual_chat"
            })
    else:
        print(f"⚠️ Пропуск {folder}: нет ни results.jsonl, ни валидного blind.md/txt с разметкой # Q1..# Q6")

# 1. Сохраняем объединенный results.jsonl
with open(os.path.join("results", "results.jsonl"), "w", encoding="utf-8") as f:
    for rec in merged_records:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

# 2. Сохраняем key.json
with open(os.path.join("results", "key.json"), "w", encoding="utf-8") as f:
    json.dump(key_map, f, ensure_ascii=False, indent=2)

# 3. Сохраняем слепой blind.md
with open(os.path.join("results", "blind.md"), "w", encoding="utf-8") as f:
    for step in steps_order:
        f.write(f"# {step.upper()}\n\n")
        for model_code in sorted(all_data[step].keys()):
            f.write(f"## {model_code} / прогон 1\n\n")
            text = all_data[step][model_code]
            f.write(text if text else "*[Нет ответа или ошибка]*")
            f.write("\n\n---\n\n")

print("\nВсё готово! 🎉 Созданы:")
print(" - results/blind.md (полный свод со всеми 27 моделями)")
print(" - results/key.json (со всеми ключами)")
print(" - results/results.jsonl (общий лог для аналитики)")