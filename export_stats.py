#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сводка по бенчмарку: обход папок Mxx-*, сбор results.jsonl + checks.csv
в один файл results/summary.csv.

Запуск:
    python export_stats.py [путь_к_корню_бенчмарка] [--raw]
    --raw  дополнительно слить все checks.csv в results/all_checks.csv
"""

import csv
import json
import re
import sys
from pathlib import Path

ARGS = sys.argv[1:]
RAW = "--raw" in ARGS
POS = [a for a in ARGS if not a.startswith("--")]
ROOT = Path(POS[0]).resolve() if POS else Path.cwd()

OUT_DIR = ROOT / "results"
SUMMARY_CSV = OUT_DIR / "summary.csv"
ALL_CHECKS_CSV = OUT_DIR / "all_checks.csv"

FOLDER_RE = re.compile(r"^(M\d+)(?:-(.+))?$")

# Возможные имена полей в results.jsonl (первое совпадение выигрывает)
COST_KEYS = ("cost_usd", "total_cost", "response_cost", "cost")
TIME_KEYS = ("duration_sec", "duration", "time_sec", "latency_s", "latency", "elapsed", "time")
TIME_BAD = ("stamp", "created", "date", "start", "end", "timeout")  # не считать это длительностью
IN_KEYS = ("prompt_tokens", "input_tokens", "tokens_in", "in_tokens")
OUT_KEYS = ("completion_tokens", "output_tokens", "tokens_out", "out_tokens")

FLAG_COLS = ["status_ok", "confidence_ok", "keys_exact", "strict_json", "trap_mentioned", "romashka"]
COLUMNS = (["Model_Code", "Model_Name", "Steps", "Cost_USD", "Time_Sec", "Tokens_In",
            "Tokens_Out", "Checks_Cost_USD", "Ok_Steps"] + FLAG_COLS + ["FZ_Cited", "Notes"])


def to_float(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return 0.0


def to_float_or_none(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def is_true(v):
    return (v or "").strip().lower() in ("true", "1", "yes", "да")


def flat_keys(d, prefix=""):
    out = []
    for k, v in d.items():
        out.append(prefix + str(k))
        if isinstance(v, dict):
            out.extend(flat_keys(v, prefix + str(k) + "."))
    return out


def pick(rec, keys, bad=()):
    """Ищет число: точное имя -> вложенные словари -> подстрока в имени ключа."""
    def exact(d):
        for k in keys:
            if k in d:
                n = to_float_or_none(d[k])
                if n is not None:
                    return n
        return None

    def substr(d):
        for k, v in d.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            lk = k.lower()
            if any(b in lk for b in bad):
                continue
            if any(kk in lk for kk in keys):
                return float(v)
        return None

    def walk(d, deep):
        r = exact(d)
        if r is None and deep:
            r = substr(d)
        if r is not None:
            return r
        for v in d.values():
            if isinstance(v, dict):
                r = walk(v, deep)
                if r is not None:
                    return r
        return None

    r = walk(rec, deep=False)
    if r is None:
        r = walk(rec, deep=True)
    return r if r is not None else 0.0


def read_checks(path):
    rows = []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                model = (row.get("model") or "").strip()
                step = (row.get("step") or "").strip()
                if not model or not step:
                    continue  # пустые строки и строки-итоги (тотал без model/step)
                rows.append(row)
    except FileNotFoundError:
        pass
    return rows


def agg_checks(rows):
    a = {"rows": 0, "cost": 0.0, "lat": 0.0, "tout": 0, "ok": 0,
         "flags": {c: 0 for c in FLAG_COLS}, "fz": set()}
    for r in rows:
        a["rows"] += 1
        a["cost"] += to_float(r.get("cost"))
        a["lat"] += to_float(r.get("latency_s"))
        a["tout"] += int(to_float(r.get("out_tokens")))
        if (r.get("status") or "").strip().lower() == "ok":
            a["ok"] += 1
        for c in FLAG_COLS:
            if is_true(r.get(c)):
                a["flags"][c] += 1
        fz = (r.get("fz_cited") or "").strip()
        if fz:
            a["fz"].update(x.strip() for x in fz.split(",") if x.strip())
    return a


def summary_row(code, st):
    ck = st["checks"]
    return [code, st["name"], st["steps"], round(st["cost"], 6), round(st["time"], 2),
            st["tin"], st["tout"], round(ck["cost"], 6), ck["ok"],
            *[ck["flags"][c] for c in FLAG_COLS],
            "; ".join(sorted(ck["fz"])), "; ".join(st["notes"])]


models = {}
jsonl_paths = sorted(ROOT.rglob("results.jsonl"))
print(f"Корень: {ROOT}\nНайдено results.jsonl: {len(jsonl_paths)}")

for path in jsonl_paths:
    code = name = None
    for part in path.parts:
        m = FOLDER_RE.match(part)
        if m:
            code, name = m.group(1), (m.group(2) or "")
            break
    if code is None:
        print(f"⚠️  {path.relative_to(ROOT)}: не распознал код модели — пропускаю")
        continue
    if code in models:
        print(f"⚠️  {code}: второй results.jsonl ({path.relative_to(ROOT)}) — пропускаю")
        continue

    st = {"name": name, "steps": 0, "cost": 0.0, "time": 0.0, "tin": 0, "tout": 0,
          "sample_keys": [], "notes": [], "raw_rows": []}
    models[code] = st

    with open(path, encoding="utf-8-sig") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                print(f"⚠️  {code}: битая JSON-строка {ln} — пропускаю")
                continue
            if not isinstance(rec, dict):
                continue
            if not st["sample_keys"]:
                st["sample_keys"] = flat_keys(rec)
            st["steps"] += 1
            st["cost"] += pick(rec, COST_KEYS)
            st["time"] += pick(rec, TIME_KEYS, bad=TIME_BAD)
            st["tin"] += int(pick(rec, IN_KEYS))
            st["tout"] += int(pick(rec, OUT_KEYS))

    st["raw_rows"] = read_checks(path.parent / "checks.csv")
    st["checks"] = agg_checks(st["raw_rows"])

    # Fallback: если в results.jsonl полей нет, берём из checks.csv
    ck = st["checks"]
    if st["cost"] == 0 and ck["cost"] > 0:
        st["cost"] = ck["cost"]; st["notes"].append("cost: checks.csv")
    if st["time"] == 0 and ck["lat"] > 0:
        st["time"] = ck["lat"]; st["notes"].append("time: checks.csv")
    if st["tout"] == 0 and ck["tout"] > 0:
        st["tout"] = ck["tout"]; st["notes"].append("tokens_out: checks.csv")
    if st["steps"] == 0 and ck["rows"] > 0:
        st["steps"] = ck["rows"]; st["notes"].append("steps: checks.csv")

# Папки моделей без логов
for d in sorted(p for p in ROOT.iterdir() if p.is_dir()):
    m = FOLDER_RE.match(d.name)
    if m and m.group(1) not in models:
        print(f"⚠️  {d.name}: нет results.jsonl (прогон не завершён?) — модель не в сводке")

if not models:
    print("❌ Не найдено ни одной модели. Запускай скрипт из корня бенчмарка или укажи путь аргументом.")
    sys.exit(1)

# Диагностика нулевой стоимости
for code, st in sorted(models.items()):
    if st["cost"] == 0 and st["steps"]:
        print(f"⚠️  {code}: стоимость = 0 и в results.jsonl, и в checks.csv.")
        if st["sample_keys"]:
            print(f"    ключи записи: {', '.join(st['sample_keys'][:25])}")

# TOTAL считаем только из фактических строк
tot = {"name": "", "steps": 0, "cost": 0.0, "time": 0.0, "tin": 0, "tout": 0, "notes": [],
       "checks": {"rows": 0, "cost": 0.0, "lat": 0.0, "tout": 0, "ok": 0,
                  "flags": {c: 0 for c in FLAG_COLS}, "fz": set()}}
for st in models.values():
    tot["steps"] += st["steps"]; tot["cost"] += st["cost"]; tot["time"] += st["time"]
    tot["tin"] += st["tin"]; tot["tout"] += st["tout"]
    for k in ("rows", "cost", "lat", "tout", "ok"):
        tot["checks"][k] += st["checks"][k]
    for c in FLAG_COLS:
        tot["checks"]["flags"][c] += st["checks"]["flags"][c]
    tot["checks"]["fz"] |= st["checks"]["fz"]

OUT_DIR.mkdir(parents=True, exist_ok=True)
with open(SUMMARY_CSV, "w", encoding="utf-8-sig", newline="") as f:  # BOM: кириллица в Excel без кракозябр
    w = csv.writer(f)
    w.writerow(COLUMNS)
    for code in sorted(models):
        w.writerow(summary_row(code, models[code]))
    w.writerow(summary_row("TOTAL", tot))

if RAW:
    fields = []
    for st in models.values():
        for r in st["raw_rows"]:
            fields.extend(k for k in r.keys() if k not in fields)
    with open(ALL_CHECKS_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Model_Code", "Model_Name"] + fields)
        for code in sorted(models):
            st = models[code]
            for r in st["raw_rows"]:
                w.writerow([code, st["name"]] + [r.get(k, "") for k in fields])
    print(f"✅ Слитые checks: {ALL_CHECKS_CSV}")

print(f"\n{'Code':<6}{'Model':<32}{'Steps':>6}{'Cost,$':>11}{'Time,s':>10}{'TokOut':>9}")
for code in sorted(models):
    st = models[code]
    print(f"{code:<6}{st['name'][:31]:<32}{st['steps']:>6}{st['cost']:>11.4f}{st['time']:>10.1f}{st['tout']:>9}")
print(f"\n✅ Сводка сохранена: {SUMMARY_CSV} ({len(models)} моделей + TOTAL)")