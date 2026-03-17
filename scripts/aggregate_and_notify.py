#!/usr/bin/env python3
import os
import sys
import json
import argparse
import re
from datetime import datetime, timedelta, timezone

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


def norm_bet(s):
    """Normalize bet string into a set of choices using {'1','2','X'}.
    Handles composites like '1X','X2','12' and Cyrillic 'П1'/'П2'."""
    if not s:
        return set()
    t = str(s).strip().upper()
    # replace common Cyrillic P (П) with empty so 'П1' -> '1'
    t = t.replace('П', '')
    # remove spaces and separators
    t = re.sub(r"[^0-9A-Z]", "", t)
    choices = set()
    for ch in t:
        if ch == '1' or ch == '2' or ch == 'X':
            choices.add(ch)
    return choices


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

    # Use yesterday in Moscow time for the report date
    now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
    msk = timezone(timedelta(hours=3))
    msk_now = now_utc.astimezone(msk)
    msk_yesterday = (msk_now - timedelta(days=1)).date()
    # format as DD.MM.YYYY to match example
    date_str = args.date or msk_yesterday.strftime('%d.%m.%Y')

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

    # collect detail lines for message
    details = []

    for r in rows:
        bet = (r.get('bet') or '')
        if not bet:
            continue
        choices = norm_bet(bet)
        if not choices:
            continue
        # Only accept single-choice bets exactly '1', '2' or 'X'. Exclude composites like '1X','12','X2'.
        single_choices = choices & set(('1', '2', 'X'))
        if len(single_choices) != 1:
            continue
        b = next(iter(single_choices))
        stats['filtered_count'] += 1
        result_raw = r.get('result')
        outcome, why = detect_outcome(result_raw)
        if outcome == 'CANCELLED':
            stats['cancelled_count'] += 1
            continue
        if outcome is None:
            stats['skipped_count'] += 1
            continue

        # determine win/lose for single-choice bet
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

        # record detail line for messaging (only for non-cancelled, non-skipped single bets)
        odd_field = 'odd1' if b == '1' else ('odd2' if b == '2' else 'oddx')
        odd_raw = r.get(odd_field) or '-'
        profit_int = int(round(profit)) if win else -int(round(stake))
        emoji = '🟢' if win else '🔴'
        bet_display = 'П1' if b == '1' else ('П2' if b == '2' else 'X')
        details.append({'time': r.get('time',''), 'match': r.get('match',''), 'bet': bet_display, 'odd': odd_raw, 'result': r.get('result',''), 'profit': profit_int, 'emoji': emoji})

    # round sums to integer for messaging
    stats['wins_profit_sum'] = int(round(stats['wins_profit_sum']))
    stats['losses_sum'] = int(round(stats['losses_sum']))
    stats['overall_result'] = int(round(stats['overall_result']))

    # round sums to integer for messaging
    stats['wins_profit_sum'] = int(round(stats['wins_profit_sum']))
    stats['losses_sum'] = int(round(stats['losses_sum']))
    stats['overall_result'] = int(round(stats['overall_result']))

    # update or compute month-to-date total using simple file-store (data/monthly_totals.json)
    month_key = msk_now.strftime('%Y-%m')
    month_total = None
    totals_path = os.path.join('data', 'monthly_totals.json')
    totals = {}
    if os.path.exists(totals_path):
        try:
            with open(totals_path, 'r', encoding='utf-8') as tf:
                totals = json.load(tf)
        except Exception:
            totals = {}
    prev = int(totals.get(month_key, 0))
    month_total = prev + stats['overall_result']
    if not args.dry_run:
        # ensure directory
        os.makedirs(os.path.dirname(totals_path), exist_ok=True)
        totals[month_key] = month_total
        with open(totals_path, 'w', encoding='utf-8') as tf:
            json.dump(totals, tf, ensure_ascii=False, indent=2)

    # build formatted message (header + per-event lines)
    header_lines = []
    header_lines.append(f"📊 Итоги за {date_str}:")
    header_lines.append(f"Всего ставок: {stats['filtered_count']}, Побед: {stats['wins_count']}, Поражений: {stats['losses_count']}, Отмен: {stats['cancelled_count']}")
    sign = '+' if stats['overall_result'] >= 0 else '-'
    header_lines.append(f"Итог дня: {sign}{abs(stats['overall_result'])} руб.")
    sign_m = '+' if month_total >= 0 else '-'
    header_lines.append(f"Итог текущего месяца: {sign_m}{abs(month_total)} руб.")

    detail_lines = []
    for d in details:
        s = f"{d['emoji']} {d['time']} | {d['match']} | {d['bet']} | {d['odd']} | {d['result']} | {('+' if d['profit']>=0 else '-')}{abs(d['profit'])} руб."
        detail_lines.append(s)

    message = '\n'.join(header_lines + [''] + detail_lines)

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
