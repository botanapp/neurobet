#!/usr/bin/env python3
import os
import sys
import json
import argparse
import re
from datetime import datetime

try:
    import requests
except Exception:
    requests = None


SCORE_RE = re.compile(r"(\d+)\s*[:\-\u2013\u2014]\s*(\d+)")
CANCEL_MARKS = ["отм", "отмен", "отмена", "cancel", "canceled", "cancelled"]


def parse_args():
    p = argparse.ArgumentParser(description='Aggregate roboblock.json and send short summary to Telegram')
    p.add_argument('--json', default='roboblock.json')
    p.add_argument('--stake', type=float, default=100.0)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--date', help='override date for message (YYYY-MM-DD)')
    return p.parse_args()


def thousands(x):
    try:
        x = int(round(x))
        return f"{x:,}".replace(',', ' ')
    except Exception:
        return str(x)


def norm_odd(s):
    if not s:
        return None
    s = str(s).strip()
    s = s.replace(',', '.')
    m = re.search(r"\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def detect_outcome(result_text):
    if not result_text:
        return None, 'no_result'
    t = str(result_text).strip()
    low = t.lower()
    # cancelled?
    for c in CANCEL_MARKS:
        if c in low:
            return 'CANCELLED', 'cancel'
    # try score parse
    m = SCORE_RE.search(t)
    if m:
        try:
            home = int(m.group(1))
            away = int(m.group(2))
            if home > away:
                return '1', 'score'
            if home < away:
                return '2', 'score'
            return 'X', 'score'
        except Exception:
            pass
    # fallback: search keywords
    if re.search(r"\bничья\b|\bdraw\b", low):
        return 'X', 'keyword'
    if re.search(r"\bпобед(а|ил(а)?)\s*1\b|\bwin\s*1\b", low):
        return '1', 'keyword'
    if re.search(r"\bпобед(а|ил(а)?)\s*2\b|\bwin\s*2\b", low):
        return '2', 'keyword'
    # last resort: look for standalone X or 1 or 2
    if re.search(r"\bX\b", t, flags=re.IGNORECASE):
        return 'X', 'fallback'
    if re.search(r"\b1\b", t):
        return '1', 'fallback'
    if re.search(r"\b2\b", t):
        return '2', 'fallback'
    return None, 'unknown'


def build_message(date_str, stats):
    filtered = stats['filtered_count']
    wins = stats['wins_count']
    losses = stats['losses_count']
    cancelled = stats.get('cancelled_count', 0)
    skipped = stats['skipped_count']
    wins_profit = stats['wins_profit_sum']
    losses_sum = stats['losses_sum']
    overall = stats['overall_result']
    parts = []
    parts.append(f"{date_str} — {filtered} событий (bet∈{{1,2,X}}).")
    parts.append(f"Выиграно: {wins} (профит +{thousands(wins_profit)}).")
    parts.append(f"Проиграно: {losses} (убыток −{thousands(losses_sum)}).")
    parts.append(f"Отменено: {cancelled}.")
    parts.append(f"Итог: {('+' if overall>=0 else '-')}{thousands(abs(overall))}.")
    if skipped:
        parts.append(f"Пропущено: {skipped}.")
    return ' '.join(parts)


def main():
    args = parse_args()
    jpath = args.json
    if not os.path.exists(jpath):
        print('ERROR: json not found:', jpath, file=sys.stderr)
        sys.exit(2)
    with open(jpath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    rows = data.get('rows') or []
    gen_at = data.get('generated_at')
    if args.date:
        date_str = args.date
    else:
        if gen_at:
            try:
                date_str = datetime.fromisoformat(gen_at.replace('Z','')).strftime('%Y-%m-%d')
            except Exception:
                date_str = datetime.utcnow().strftime('%Y-%m-%d')
        else:
            date_str = datetime.utcnow().strftime('%Y-%m-%d')

    stats = {
        'filtered_count': 0,
        'wins_count': 0,
        'losses_count': 0,
        'skipped_count': 0,
        'cancelled_count': 0,
        'wins_profit_sum': 0.0,
        'losses_sum': 0.0,
        'overall_result': 0.0,
    }

    stake = float(args.stake)

    for r in rows:
        bet = (r.get('bet') or '').strip()
        if not bet:
            continue
        b = bet.upper()
        if b not in ('1','2','X'):
            continue
        stats['filtered_count'] += 1
        result_raw = r.get('result')
        outcome, why = detect_outcome(result_raw)
        if outcome == 'CANCELLED':
            stats['cancelled_count'] += 1
            continue
        if outcome is None:
            stats['skipped_count'] += 1
            continue

        # determine win/lose
        win = (outcome == b)
        if win:
            odd_field = 'odd1' if b == '1' else ('odd2' if b == '2' else 'oddx')
            odd = norm_odd(r.get(odd_field))
            if odd is None:
                stats['skipped_count'] += 1
                continue
            profit = stake * (odd - 1.0)
            stats['wins_count'] += 1
            stats['wins_profit_sum'] += profit
            stats['overall_result'] += profit
        else:
            # lost
            stats['losses_count'] += 1
            loss = stake
            stats['losses_sum'] += loss
            stats['overall_result'] -= loss

    # round sums to integer for messaging
    stats['wins_profit_sum'] = int(round(stats['wins_profit_sum']))
    stats['losses_sum'] = int(round(stats['losses_sum']))
    stats['overall_result'] = int(round(stats['overall_result']))

    message = build_message(date_str, stats)

    # log details
    print('--- Aggregation summary ---')
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print('Message:')
    print(message)

    if args.dry_run:
        print('Dry-run mode: not sending to Telegram')
        sys.exit(0)

    # send to telegram
    bot = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat = os.environ.get('TELEGRAM_CHAT_ID')
    if not bot or not chat:
        print('ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in environment', file=sys.stderr)
        sys.exit(3)
    if requests is None:
        print('ERROR: requests library not available', file=sys.stderr)
        sys.exit(4)

    url = f'https://api.telegram.org/bot{bot}/sendMessage'
    payload = {'chat_id': chat, 'text': message}
    try:
        resp = requests.post(url, data=payload, timeout=15)
        resp.raise_for_status()
        print('Telegram send OK:', resp.status_code)
        sys.exit(0)
    except Exception as e:
        print('ERROR sending to Telegram:', e, file=sys.stderr)
        print('Response (if any):', getattr(e, 'response', None))
        sys.exit(5)


if __name__ == '__main__':
    main()
