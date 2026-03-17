#!/usr/bin/env python3
import os
import sys
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime, timedelta

def get_yesterday_msk():
    # Compute "yesterday" in Moscow time (UTC+3) to match user's timezone preference.
    now_utc = datetime.utcnow()
    now_msk = now_utc + timedelta(hours=3)
    y = (now_msk.date() - timedelta(days=1))
    return y.strftime('%Y-%m-%d')

def fetch(offset_date, ref):
    url = 'https://vprognoze.ru/webmaster_moduls/webmasters_robobet.php'
    params = {
        'offset': offset_date,
        'ref': ref,
        'text_color': '000',
        'row_bgc': 'FFFFFF',
        'row_fontsize': '12',
        'head_bgc': 'C7D6E9',
        'head_fontsize': '12',
        'row_bgc_cursor': 'FBFFCD',
        'g': 'key'
    }
    r = requests.get(url, params=params, timeout=30, headers={'User-Agent':'GHActionFetcher/1.0'})
    r.raise_for_status()
    return r.text

def parse_table(html):
    soup = BeautifulSoup(html, 'html.parser')
    div = soup.find('div', {'id':'roboblock'}) or soup.find('div', {'class':'roboblock'})
    if not div:
        raise RuntimeError('roboblock not found')
    table = div.find('table', {'class':'robot-table'})
    if not table:
        # fallback: return raw div
        return {'html': str(div), 'rows': []}

    rows = []
    tbody = table.find('tbody')
    if not tbody:
        return {'html': str(div), 'rows': []}

    for tr in tbody.find_all('tr'):
        tds = tr.find_all('td')
        if not tds:
            continue
        # columns per widget structure
        # try to extract country code from flag class in the match cell
        country_code = ''
        if len(tds) > 1:
            match_td = tds[1]
            img = match_td.find('img')
            if img and img.has_attr('class'):
                classes = img.get('class')
                # BeautifulSoup may return list for class attribute
                if isinstance(classes, list):
                    for c in classes:
                        if c.startswith('flag-') and c != 'flags':
                            country_code = c.split('-',1)[1]
                            break
                else:
                    for c in classes.split():
                        if c.startswith('flag-') and c != 'flags':
                            country_code = c.split('-',1)[1]
                            break

        item = {
            'time': tds[0].get_text(strip=True) if len(tds) > 0 else '',
            'match': tds[1].get_text(strip=True) if len(tds) > 1 else '',
            'country': country_code,
            'p1': tds[2].get_text(strip=True) if len(tds) > 2 else '',
            'px': tds[3].get_text(strip=True) if len(tds) > 3 else '',
            'p2': tds[4].get_text(strip=True) if len(tds) > 4 else '',
            'bet': tds[5].get_text(strip=True) if len(tds) > 5 else '',
            'odd1': tds[6].get_text(strip=True) if len(tds) > 6 else '',
            'oddx': tds[7].get_text(strip=True) if len(tds) > 7 else '',
            'odd2': tds[8].get_text(strip=True) if len(tds) > 8 else '',
            'result': tds[9].get_text(strip=True) if len(tds) > 9 else ''
        }
        rows.append(item)

    return {'html': str(div), 'rows': rows}

def write_outputs(data):
    # roboblock.html contains the inner HTML of div.roboblock
    with open('roboblock.html', 'w', encoding='utf-8') as f:
        f.write(data.get('html',''))
    with open('roboblock.json', 'w', encoding='utf-8') as f:
        json.dump({'generated_at': datetime.utcnow().isoformat() + 'Z', 'rows': data.get('rows',[])}, f, ensure_ascii=False, indent=2)

def main():
    ref = os.environ.get('REF_URL', 'https://botanapp.github.io/neurobet/')
    offset_date = get_yesterday()
    print('Fetching offset=', offset_date)
    html = fetch(offset_date, ref)
    parsed = parse_table(html)
    write_outputs(parsed)
    print('Wrote roboblock.html and roboblock.json (rows=', len(parsed.get('rows',[])),')')

if __name__ == '__main__':
    main()
