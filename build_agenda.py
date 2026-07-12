#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Metz Sorties IA — robot sans IA.
Collecte des événements messins (RSS tout-metz + agenda metz.fr), scoring par
mots-clés selon le profil pondéré, fusion avec les activités permanentes
vérifiées, puis génération de docs/metz.ics (4 activités par soir, 7 jours).

Exécuté chaque nuit par GitHub Actions. Aucune clé, aucune IA.
Usage local : python build_agenda.py [--offline]
"""

import json, re, sys, hashlib, unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

OFFLINE = "--offline" in sys.argv
RACINE = Path(__file__).parent
SORTIE = RACINE / "docs" / "metz.ics"

# ---------------------------------------------------------------- profil ----
# Pondérations du profil : proximité x1, originalité x2, adapté x3, social x4
MOTS_INTERETS = {  # mot-clé (sans accents, minuscules) -> points de proximité
    "chill": 1, "detente": 1, "guinguette": 2, "terrasse": 1,
    "sport": 2, "course": 1, "velo": 1, "plage": 2,
    "asie": 3, "asiatique": 3, "vietnam": 3, "vietnamien": 3,
    "japon": 3, "japonais": 3, "manga": 2, "sushi": 2, "ramen": 2,
    "photo": 2, "photographie": 2, "expo": 2, "exposition": 2,
    "film": 2, "cinema": 2, "projection": 2, "art": 2, "mapping": 2,
    "combat": 3, "mma": 3, "boxe": 3, "martiaux": 3, "muay": 3,
    "bar": 2, "biere": 1, "cocktail": 1, "degustation": 1,
    "nourriture": 2, "food": 2, "gastronomie": 2, "marche": 1, "brunch": 1,
    "rencontre": 3, "celibataire": 3, "speed dating": 3, "dating": 3,
}
MOTS_SOCIAL = ["soiree", "dj", "concert", "festival", "bal", "fete", "guinguette",
               "rencontre", "celibataire", "afterwork", "danse", "karaoke", "bar",
               "social", "foule", "plage"]
MOTS_ORIGINALITE = ["insolite", "premiere", "unique", "nocturne", "plein air",
                    "escape", "immersif", "mapping", "cloture", "inedit"]
EXCLUSIONS = ["thionville", "nancy", "luxembourg", "sarreguemines", "forbach",
              "hagondange", "amneville", "enfant 3", "sponsorise", "annule"]
BUDGET_MAX = 50

def sans_accents(s: str) -> str:
    return unicodedata.normalize("NFD", (s or "").lower()).encode("ascii", "ignore").decode()

def score(texte: str, permanent: bool) -> int:
    """Score /20 : proximité x1 + originalité x2 + adapté x3 + social x4 (+ véracité)."""
    t = sans_accents(texte)
    proximite = min(4, sum(p for m, p in MOTS_INTERETS.items() if m in t))
    adapte = min(2, sum(1 for m, p in MOTS_INTERETS.items() if p >= 3 and m in t))
    social = min(2, sum(1 for m in MOTS_SOCIAL if m in t))
    originalite = min(2, sum(1 for m in MOTS_ORIGINALITE if m in t) + (0 if permanent else 1))
    veracite = 2 if permanent else 1        # les permanentes sont vérifiées à la main
    total = proximite * 1 + originalite * 2 + adapte * 3 + social * 4 + veracite
    return max(6, min(20, total))

def exclu(texte: str) -> bool:
    t = sans_accents(texte)
    if any(m in t for m in EXCLUSIONS):
        return True
    prix = re.search(r"(\d+)\s*(?:€|euros)", t)
    return bool(prix and int(prix.group(1)) > BUDGET_MAX)

# ------------------------------------------------------------- collecte -----
def collecter_flux() -> list[dict]:
    """Événements datés depuis les sources web. Chaque source est optionnelle."""
    if OFFLINE:
        return []
    events, erreurs = [], []

    # 1) RSS tout-metz.com (WordPress) — articles d'agenda avec dates dans le titre/texte
    try:
        import feedparser
        flux = feedparser.parse("https://tout-metz.com/feed")
        for e in flux.entries[:40]:
            texte = f"{e.get('title','')} {e.get('summary','')}"
            d = extraire_date(texte)
            if d and not exclu(texte):
                events.append(evenement_depuis_article(e, d, texte))
    except Exception as ex:
        erreurs.append(f"tout-metz: {ex}")

    # 2) Agenda metz.fr — pages fiche-XXXX listées sur /agenda/
    try:
        import requests
        from bs4 import BeautifulSoup
        html = requests.get("https://metz.fr/agenda/", timeout=20,
                            headers={"User-Agent": "AgendaIA-perso"}).text
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[href*='fiche-']")[:30]:
            titre = a.get_text(" ", strip=True)
            bloc = a.find_parent().get_text(" ", strip=True) if a.find_parent() else titre
            d = extraire_date(bloc)
            if titre and d and not exclu(bloc):
                events.append({
                    "name": titre[:80], "date": d, "start": None, "end": None,
                    "place": "Metz (voir fiche)", "address": "57000 Metz",
                    "url": "https://metz.fr" + a["href"] if a["href"].startswith("/") else a["href"],
                    "price": "voir fiche", "permanent": False, "verify": True,
                    "why": "Événement daté repéré sur l'agenda officiel metz.fr.",
                })
    except Exception as ex:
        erreurs.append(f"metz.fr: {ex}")

    if erreurs:
        print("Sources en échec (non bloquant):", "; ".join(erreurs))
    return events

MOIS_FR = {m: i + 1 for i, m in enumerate(
    ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet",
     "aout", "septembre", "octobre", "novembre", "decembre"])}

def extraire_date(texte: str):
    """Trouve une date française (ex: '17 juillet' ou '17/07/2026') dans un texte."""
    t = sans_accents(texte)
    auj = date.today()
    m = re.search(r"\b(\d{1,2})\s+(" + "|".join(MOIS_FR) + r")(?:\s+(\d{4}))?", t)
    if m:
        j, mois = int(m.group(1)), MOIS_FR[m.group(2)]
        annee = int(m.group(3)) if m.group(3) else auj.year
        try:
            d = date(annee, mois, j)
            return d if d >= auj else None
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", t)
    if m:
        try:
            d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            return d if d >= auj else None
        except ValueError:
            return None
    return None

def evenement_depuis_article(e, d, texte) -> dict:
    h = re.search(r"\b(\d{1,2})h(\d{2})?\b", sans_accents(texte))
    start = f"{int(h.group(1)):02d}:{h.group(2) or '00'}" if h else None
    return {"name": e.get("title", "Événement")[:80], "date": d,
            "start": start, "end": None,
            "place": "Metz", "address": "57000 Metz", "url": e.get("link", ""),
            "price": "voir article", "permanent": False,
            "verify": start is None,
            "why": "Repéré via tout-metz.com — recouper l'horaire sur l'article."}

# ------------------------------------------------------- planification ------
def charger_permanentes() -> list[dict]:
    data = json.loads((RACINE / "activites_permanentes.json").read_text(encoding="utf-8"))
    return data["activites"]

def cadrer_horaire(start: str, end: str, jour: date):
    """Retourne (start, end) décalés au seuil (17h30 semaine / 8h week-end), ou None si impossible."""
    limite = "08:00" if jour.weekday() >= 5 else "17:30"
    start, end = start or "20:00", end or None
    if start >= limite:
        return start, end
    if end and end > limite:            # la plage couvre le seuil → on décale le début
        return limite, end
    return None

def candidats_du_jour(jour: date, events_web, permanentes, deja_utilise) -> list[dict]:
    out = []
    for ev in events_web:
        if ev["date"] != jour or ev["name"] in deja_utilise:
            continue
        cadre = cadrer_horaire(ev["start"], ev["end"], jour)
        if not cadre:
            continue
        s = score(ev["name"] + " " + ev.get("why", ""), permanent=False)
        out.append({**ev, "start": cadre[0], "end": cadre[1] or "22:00", "score": s})
    for p in permanentes:
        if jour.weekday() not in p["days"] or p["name"] in deja_utilise:
            continue
        if p.get("from") and jour.isoformat() < p["from"]:
            continue
        if p.get("to") and jour.isoformat() > p["to"]:
            continue
        cadre = cadrer_horaire(p["start"], p.get("end"), jour)
        if not cadre:
            continue
        s = score(p["name"] + " " + p.get("why", "") + " " + " ".join(p.get("tags", [])),
                  permanent=True)
        out.append({**p, "date": jour, "start": cadre[0], "end": cadre[1] or cadre[0],
                    "score": s, "permanent": True})
    return sorted(out, key=lambda c: -c["score"])

def planifier(events_web: list[dict]) -> dict[date, list[dict]]:
    """4 activités max par soir, réparties en round-robin sur la semaine :
    à chaque passe, chaque jour prend son meilleur candidat encore libre —
    aucune activité ne sert deux fois dans la semaine (règle anti-répétition),
    et un jour ne peut pas assécher les suivants."""
    lundi = date.today() - timedelta(days=date.today().weekday())
    jours = [lundi + timedelta(days=i) for i in range(7)]
    permanentes = charger_permanentes()
    planning = {j: [] for j in jours}
    deja_utilise: set[str] = set()

    for _passe in range(4):                       # 4 créneaux par soir
        for jour in jours:
            if len(planning[jour]) >= 4:
                continue
            noms_du_jour = {e["name"] for e in planning[jour]}
            for c in candidats_du_jour(jour, events_web, permanentes, deja_utilise):
                if c["name"] in noms_du_jour:
                    continue
                planning[jour].append(c)
                deja_utilise.add(c["name"])       # règle : pas de répétition dans la semaine
                break

    return {j: sorted(evts, key=lambda c: c["start"]) for j, evts in planning.items()}

# ---------------------------------------------------------------- ICS -------
def esc(s: str) -> str:
    return str(s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

VTZ = """BEGIN:VTIMEZONE\r\nTZID:Europe/Paris\r\nBEGIN:DAYLIGHT\r\nTZOFFSETFROM:+0100\r\nTZOFFSETTO:+0200\r\nTZNAME:CEST\r\nDTSTART:19700329T020000\r\nRRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU\r\nEND:DAYLIGHT\r\nBEGIN:STANDARD\r\nTZOFFSETFROM:+0200\r\nTZOFFSETTO:+0100\r\nTZNAME:CET\r\nDTSTART:19701025T030000\r\nRRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU\r\nEND:STANDARD\r\nEND:VTIMEZONE"""

def generer_ics(planning: dict[date, list[dict]]) -> str:
    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//MetzSortiesIA-robot//FR",
         "CALSCALE:GREGORIAN", "X-WR-CALNAME:IA", "X-WR-TIMEZONE:Europe/Paris",
         "X-APPLE-CALENDAR-COLOR:#007AFF", VTZ]
    from datetime import timezone
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for jour, evts in planning.items():
        ymd = jour.strftime("%Y%m%d")
        for i, ev in enumerate(evts, 1):
            hm = lambda t: t.replace(":", "") + "00"
            fin = ev["end"] if ev.get("end") and ev["end"] > ev["start"] else ev["start"]
            desc = (f"SCORE : {ev['score']}/20\nBudget : {ev.get('price','?')}\n"
                    f"{ev.get('why','')}\n{ev.get('url','')}")
            L += ["BEGIN:VEVENT",
                  f"UID:metz-ia-{ymd}-{i}@gengiscard.github.io",
                  f"DTSTAMP:{stamp}",
                  f"DTSTART;TZID=Europe/Paris:{ymd}T{hm(ev['start'])}",
                  f"DTEND;TZID=Europe/Paris:{ymd}T{hm(fin)}",
                  f"SUMMARY:{esc(ev['name'])}{' (À CONFIRMER)' if ev.get('verify') else ''}",
                  f"LOCATION:{esc(', '.join(filter(None, [ev.get('place'), ev.get('address')])))}",
                  f"DESCRIPTION:{esc(desc)}"]
            if ev.get("url"):
                L.append(f"URL:{ev['url']}")
            L += ["CATEGORIES:IA", "END:VEVENT"]
    L.append("END:VCALENDAR")
    return "\r\n".join(L) + "\r\n"

# ---------------------------------------------------------------- main ------
if __name__ == "__main__":
    events_web = collecter_flux()
    print(f"{len(events_web)} événement(s) daté(s) collecté(s) sur le web.")
    planning = planifier(events_web)
    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    SORTIE.write_text(generer_ics(planning), encoding="utf-8")
    total = sum(len(v) for v in planning.values())
    print(f"OK — {total} activités écrites dans {SORTIE}")
    for jour, evts in planning.items():
        print(" ", jour.strftime("%a %d/%m"), "→", " | ".join(f"{e['start']} {e['name'][:38]} ({e['score']}/20)" for e in evts))
