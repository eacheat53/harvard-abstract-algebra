#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Production-grade global boundary rebalancer and SRT builder for Harvard Abstract Algebra.
Repairs all mid-sentence cuts, shifts dangling sentence tails forward, splits long multi-sentence rows,
and recompiles all 33 lectures with verified overlaps=0, inversions=0.
"""

import os
import re
import sys
import shutil
import subprocess

SCRIPT_DIR = "/home/deadalux/.gemini/config/skills/lecture-bilingual-srt/scripts"
WORKSPACE = "/home/deadalux/Documents/Abstract Algebra"
VIDEO_DIR = os.path.join(WORKSPACE, "哈佛大学：抽象代数 Abstract Algebra, Prof. Benedict Gross")

def rebalance_and_split_rows(rows):
    """
    1. Shifts dangling tails forward to the next row (Rule 1).
    2. Shifts short completion heads backward to preceding row (Rule 2).
    3. Splits long multi-sentence rows into readable chunks (Rule 3).
    """
    # Pass 1: Boundary rebalancing
    changed = True
    pass_num = 0
    total_shifts = 0
    
    while changed and pass_num < 6:
        changed = False
        pass_num += 1
        i = 0
        while i < len(rows) - 1:
            en1 = rows[i][0].strip()
            zh1 = rows[i][1].strip()
            en2 = rows[i+1][0].strip()
            zh2 = rows[i+1][1].strip()
            
            # Rule 1: en1 has sentence stop followed by non-terminated tail (e.g., "That's", "And once you", "so the")
            m_en = re.search(r'([.?!])\s+([A-Za-z0-9\s,\'\"\(\)\-\–\—]+)$', en1)
            m_zh = re.search(r'([。？！；;])([^。？！；;]+)$', zh1)
            
            applied = False
            if m_en and m_zh:
                tail_en = m_en.group(2).strip()
                tail_zh = m_zh.group(2).strip()
                words = tail_en.split()
                if 1 <= len(words) <= 15 and 1 <= len(tail_zh) <= 30:
                    en1_clean = en1[:m_en.start(1)+1].strip()
                    zh1_clean = zh1[:m_zh.start(1)+1].strip()
                    en2_clean = tail_en + ' ' + en2
                    zh2_clean = tail_zh + zh2
                    rows[i] = [en1_clean, zh1_clean]
                    rows[i+1] = [en2_clean, zh2_clean]
                    changed = True
                    applied = True
                    total_shifts += 1
            
            # Rule 2: en1 does NOT end with sentence stop, and en2 starts with short completion
            if not applied and not rows[i][0].endswith(('.', '?', '!', '.\"', '?\"', '!”', '。”', ':', '：')):
                m_head_en = re.match(r'^([^.?!]+[.?!])\s+(.*)$', rows[i+1][0])
                if m_head_en:
                    head_en = m_head_en.group(1).strip()
                    rest_en = m_head_en.group(2).strip()
                    if len(head_en.split()) <= 10:
                        m_head_zh = re.match(r'^([^。？！]+[。？！])(.*)$', rows[i+1][1])
                        if m_head_zh:
                            head_zh = m_head_zh.group(1).strip()
                            rest_zh = m_head_zh.group(2).strip()
                            en1_clean = rows[i][0] + ' ' + head_en
                            zh1_clean = rows[i][1] + head_zh
                            rows[i] = [en1_clean, zh1_clean]
                            rows[i+1] = [rest_en, rest_zh]
                            changed = True
                            total_shifts += 1
            i += 1
            
    # Pass 2: Split long multi-sentence rows
    new_rows = []
    total_splits = 0
    for en, zh in rows:
        words = en.split()
        if len(words) > 22:
            m_en = re.search(r'^([^.?!]+[.?!])\s+([A-Z0-9].*)$', en)
            if m_en:
                en_part1 = m_en.group(1).strip()
                en_part2 = m_en.group(2).strip()
                m_zh = re.search(r'^([^。？！]+[。？！])(.*)$', zh)
                if m_zh:
                    zh_part1 = m_zh.group(1).strip()
                    zh_part2 = m_zh.group(2).strip()
                    if len(en_part1.split()) >= 6 and len(en_part2.split()) >= 6:
                        new_rows.append([en_part1, zh_part1])
                        new_rows.append([en_part2, zh_part2])
                        total_splits += 1
                        continue
        new_rows.append([en, zh])
        
    cleaned = [r for r in new_rows if r[0].strip() and r[1].strip()]
    return cleaned, total_shifts, total_splits

def process_lecture(tsv_path, raw_srt_path, out_srt_path, video_srt_path=None, extra_copy=None):
    base = os.path.basename(tsv_path)
    with open(tsv_path, 'r', encoding='utf-8') as f:
        rows = [l.strip().split('\t') for l in f if l.strip()]
        
    rebalanced, shifts, splits = rebalance_and_split_rows(rows)
    
    # Save back to TSV
    with open(tsv_path, 'w', encoding='utf-8') as f:
        for en, zh in rebalanced:
            f.write(f"{en}\t{zh}\n")
            
    # Build SRT
    qa_path = f"/tmp/qa_{os.path.basename(out_srt_path)}.txt"
    cmd = [
        "python3",
        f"{SCRIPT_DIR}/lecture_srt_tool.py",
        "build",
        raw_srt_path,
        tsv_path,
        "-o", out_srt_path,
        "--order", "zh-en",
        "--report", qa_path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"ERROR building {base}: {res.stderr}")
        return False, shifts, splits, ""
        
    if video_srt_path:
        shutil.copy2(out_srt_path, video_srt_path)
    if extra_copy:
        shutil.copy2(out_srt_path, extra_copy)
        
    qa_summary = ""
    if os.path.exists(qa_path):
        with open(qa_path, 'r', encoding='utf-8') as qf:
            qa_summary = qf.read().strip()
            
    return True, shifts, splits, qa_summary

def main():
    print("=== STARTING GLOBAL HIGH-PRECISION REBALANCE & BUILD ===")
    tasks = []
    
    # Lec 06
    tasks.append({
        'name': 'Lec 06',
        'tsv': f"{WORKSPACE}/06 - Lec 6 ｜ Abstract Algebra.blocks.tsv",
        'raw': f"{WORKSPACE}/Lec06_raw.en.srt",
        'out': f"{WORKSPACE}/06 - Lec 6 ｜ Abstract Algebra.en-zh.srt",
        'video': None,
        'extra': f"{WORKSPACE}/lec06.en-zh.srt"
    })
    
    # Lec 07
    tasks.append({
        'name': 'Lec 07',
        'tsv': f"{WORKSPACE}/07 - Lec 7 ｜ Abstract Algebra.blocks.tsv",
        'raw': f"{WORKSPACE}/07 - Lec 7 ｜ Abstract Algebra.en.srt",
        'out': f"{WORKSPACE}/07 - Lec 7 ｜ Abstract Algebra.en-zh.srt",
        'video': None,
        'extra': f"{WORKSPACE}/lec07.en-zh.srt"
    })
    
    # Lec 08 to 36
    for num in range(8, 37):
        p_str = f"[P{num:02d}]{num}.Lec {num}  Abstract Algebra"
        tasks.append({
            'name': f"Lec {num:02d}",
            'tsv': f"{WORKSPACE}/{num:02d} - Lec {num} ｜ Abstract Algebra.blocks.tsv",
            'raw': f"{WORKSPACE}/{num:02d} - Lec {num} ｜ Abstract Algebra.en.srt",
            'out': f"{WORKSPACE}/{num:02d} - Lec {num} ｜ Abstract Algebra.en-zh.srt",
            'video': f"{VIDEO_DIR}/{p_str}.en-zh.srt",
            'extra': None
        })
        
    # Review 1 (Lec 37)
    tasks.append({
        'name': 'Review 1 (Lec 37)',
        'tsv': f"{WORKSPACE}/37 - Review 1.blocks.tsv",
        'raw': f"{WORKSPACE}/37 - Review 1.en.srt",
        'out': f"{WORKSPACE}/37 - Review 1.en-zh.srt",
        'video': f"{VIDEO_DIR}/[P37]37.Review 1.en-zh.srt",
        'extra': None
    })
    
    # Review 2 (Lec 38)
    tasks.append({
        'name': 'Review 2 (Lec 38)',
        'tsv': f"{WORKSPACE}/38 - Review 2.blocks.tsv",
        'raw': f"{WORKSPACE}/38 - Review 2.en.srt",
        'out': f"{WORKSPACE}/38 - Review 2.en-zh.srt",
        'video': f"{VIDEO_DIR}/[P38]38.Review 2.en-zh.srt",
        'extra': None
    })
    
    total_shifts_all = 0
    total_splits_all = 0
    all_ok = True
    
    for t in tasks:
        if not os.path.exists(t['tsv']) or not os.path.exists(t['raw']):
            print(f"Skipping {t['name']}: file not found")
            continue
            
        print(f"Processing {t['name']}...", end=" ", flush=True)
        ok, shifts, splits, qa = process_lecture(t['tsv'], t['raw'], t['out'], t['video'], t['extra'])
        if ok:
            total_shifts_all += shifts
            total_splits_all += splits
            overlaps = "overlaps=0" in qa
            inversions = "inversions=0" in qa
            print(f"OK! Shifts: {shifts:2d} | Splits: {splits:2d} | overlaps=0: {overlaps} | inversions=0: {inversions}")
        else:
            all_ok = False
            print("FAILED!")
            
    print(f"\n=== GLOBAL REBALANCING COMPLETE! Total shifts: {total_shifts_all}, Total splits: {total_splits_all} ===")
    return all_ok

if __name__ == '__main__':
    main()
