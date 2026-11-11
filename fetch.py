#!/usr/bin/env python3
"""Skenuje fotbal přes The Odds API, vyhodnotí Pinnacle (po odečtení marže)
a uloží kandidáty do data.json. Bez externích knihoven."""
import json, os, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

API = "https://api.the-odds-api.com/v4"
KEY = os.environ["ODDS_API_KEY"]

# --- NASTAVENÍ -------------------------------------------------------
LEAGUES = {
    "soccer_uefa_champs_league": "Liga mistrů",
    "soccer_uefa_europa_league": "Evropská liga",
    "soccer_uefa_europa_conference_league": "Konferenční liga",
    "soccer_epl": "Anglie 1",
    "soccer_efl_champ": "Anglie 2",
    "soccer_germany_bundesliga": "Německo 1",
    "soccer_germany_bundesliga2": "Německo 2",
    "soccer_spain_la_liga": "Španělsko 1",
    "soccer_italy_serie_a": "Itálie 1",
    "soccer_france_ligue_one": "Francie 1",
    "soccer_netherlands_eredivisie": "Nizozemsko 1",
    "soccer_portugal_primeira_liga": "Portugalsko 1",
}
MARKETS = ["h2h", "totals"]      # výherce + počet gólů
TOTAL_LINE = 2.5                 # sleduje se jen hranice 2,5
REFERENCE = "pinnacle"           # hlavní reference
CHECKS = ["betfair_ex_eu", "matchbook"]  # další ostré zdroje (kontrola shody)
MIN_P = 0.40                     # min. pravděpodobnost výsledku
MIN_EDGE = 0.03                  # min. hrana proti férovému kurzu
MIN_ODDS = 1.80                  # nejnižší kurz, na který se vyplatí sázet
MAX_ODDS = 3.50
MAX_DISAGREE = 0.03              # max. rozdíl pravděpodobnosti mezi zdroji
HORIZON_H = 48                   # kolik hodin dopředu
# ---------------------------------------------------------------------

quota = {}


def get(path, **params):
    params["apiKey"] = KEY
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        for h in ("x-requests-remaining", "x-requests-used"):
            if r.headers.get(h) is not None:
                quota[h] = r.headers.get(h)
        return json.loads(r.read())


def devig(odds):
    """Power metoda: najde exponent c, aby součet q^c = 1."""
    q = [1 / o for o in odds]
    s = sum(q)
    if s <= 1:
        return [x / s for x in q]
    lo, hi = 1.0, 10.0
    for _ in range(60):
        c = (lo + hi) / 2
        if sum(x ** c for x in q) > 1:
            lo = c
        else:
            hi = c
    return [x ** c for x in q]


def probs(bm, key):
    for m in bm.get("markets", []):
        if m["key"] != key:
            continue
        outs = m["outcomes"]
        if key == "totals":
            outs = [o for o in outs if o.get("point") == TOTAL_LINE]
            if len(outs) != 2:
                return None
        elif len(outs) != 3:
            return None
        p = devig([o["price"] for o in outs])
        return {o["name"]: (pp, o["price"]) for o, pp in zip(outs, p)}
    return None


def label(key, name):
    if key == "totals":
        return "Více než 2,5 gólu" if name == "Over" else "Méně než 2,5 gólu"
    return f"Výhra: {name}"


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    now = datetime.now(timezone.utc)
    win = {"commenceTimeFrom": iso(now), "commenceTimeTo": iso(now + timedelta(hours=HORIZON_H))}
    found = []
    for sport, league in LEAGUES.items():
        try:
            # dotaz na zápasy by neměl spotřebovat kredity; když selže, pokračuje se dál
            try:
                if not get(f"/sports/{sport}/events", **win):
                    continue
            except Exception:
                pass
            events = get(f"/sports/{sport}/odds", bookmakers=",".join([REFERENCE] + CHECKS),
                         markets=",".join(MARKETS), oddsFormat="decimal", **win)
        except Exception as e:
            print(f"{sport}: {e}")
            continue
        for ev in events:
            books = {b["key"]: b for b in ev.get("bookmakers", [])}
            if REFERENCE not in books:
                continue
            for key in MARKETS:
                ref = probs(books[REFERENCE], key)
                if not ref:
                    continue
                others = [o for o in (probs(books[k], key) for k in CHECKS if k in books) if o]
                for name, (p, price) in ref.items():
                    if name == "Draw" or p < MIN_P:
                        continue
                    fair = 1 / p
                    need = max(MIN_ODDS, fair * (1 + MIN_EDGE))
                    if need > MAX_ODDS:
                        continue
                    diffs = [abs(o[name][0] - p) for o in others if name in o]
                    if diffs and max(diffs) > MAX_DISAGREE:
                        continue
                    found.append({
                        "id": f"{ev['id']}|{key}|{name}",
                        "league": league, "home": ev["home_team"], "away": ev["away_team"],
                        "kickoff": ev["commence_time"], "pick": label(key, name),
                        "p": round(p, 4), "fair": round(fair, 3), "need": round(need, 2),
                        "pinnacle": price, "sources": 1 + len(diffs),
                    })
    found.sort(key=lambda x: x["kickoff"])

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump({"generated": iso(now), "quota": quota, "picks": found}, f, ensure_ascii=False, indent=1)

    # záznam pro pozdější vyhodnocení (první zaznamenaný kurz každého tipu)
    seen = set()
    if os.path.exists("picks.jsonl"):
        with open("picks.jsonl", encoding="utf-8") as f:
            seen = {json.loads(l)["id"] for l in f if l.strip()}
    with open("picks.jsonl", "a", encoding="utf-8") as f:
        for p in found:
            if p["id"] not in seen:
                f.write(json.dumps({**p, "first_seen": iso(now)}, ensure_ascii=False) + "\n")
    print(f"Kandidátů: {len(found)}, kredity: {quota}")


if __name__ == "__main__":
    main()
