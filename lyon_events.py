#!/usr/bin/env python3
"""
Génère un calendrier .ics (abonnable sur iPhone) des concerts, spectacles,
pièces de théâtre et stand-up à Lyon et alentours.

Sources :
  - Ticketmaster Discovery API (clé gratuite)         -> TICKETMASTER_API_KEY
  - OpenAgenda (optionnel, petites salles / théâtres) -> OPENAGENDA_KEY + OPENAGENDA_AGENDAS
Descriptions :
  - Réécrites en 2-3 phrases par Claude si ANTHROPIC_API_KEY est défini (optionnel),
    sinon construites à partir des données de la source. Mises en cache.
"""
import hashlib
import html
import json
import os
import re
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar, Event

# ----------------------------------------------------------------- réglages
TZ = ZoneInfo("Europe/Paris")
LYON = (45.7640, 4.8357)                       # centre de Lyon
RADIUS_KM = int(os.getenv("RADIUS_KM", 40))    # 40 km : Décines, Villeurbanne, Vienne...
HORIZON_DAYS = int(os.getenv("HORIZON_DAYS", 180))
OUTPUT = Path(os.getenv("OUTPUT", "docs/lyon-evenements.ics"))
CACHE_FILE = Path("data/descriptions.json")
DEFAULT_DURATION = timedelta(hours=2, minutes=30)
CAL_NAME = "Spectacles & concerts – Lyon"
# Événements longs (expos, séries quotidiennes) regroupés en une seule entrée
MIN_RUN_DAYS = int(os.getenv("MIN_RUN_DAYS", 8))    # à partir de combien de jours quasi consécutifs
EXCLUDE_EXPOS = os.getenv("EXCLUDE_EXPOS", "0") == "1"  # 1 = supprimer complètement les expos

# Centres d'intérêt : passe un type à False pour le masquer, à True pour l'afficher.
# Le motif est cherché dans le genre Ticketmaster / les mots-clés OpenAgenda.
TYPES = {
    "Rock / Indé":                   (True,  r"rock|alternative|indie|ind[ié]"),
    "Pop / Chanson française":       (True,  r"\bpop\b|chanson|vari[ée]t[ée]|french"),
    "Rap / Hip-hop / R&B":           (True,  r"hip.?hop|\brap\b|r&b|\brnb\b|urban"),
    "Électro / DJ":                  (True,  r"[ée]lectro|techno|\bhouse\b|\bdj\b|trance"),
    "Jazz / Blues / Soul":           (True,  r"jazz|blues|soul|funk|gospel"),
    "Musiques du monde / Reggae":    (True,  r"world|reggae|latin|afro|\bska\b|\bdub\b|du monde|flamenco|salsa"),
    "Théâtre":                       (True,  r"theat|th[ée][âa]tre|drama|\bplay\b|com[ée]die(?! musicale)"),
    "Stand-up / Humour":             (True,  r"comedy|humou?r|stand.?up|one.?(wo)?man"),
    "Classique / Opéra":             (False, r"classical|classique|op[ée]ra|symphon|orchestr|baroque|lyrique|philharmon"),
    "Metal / Punk":                  (False, r"metal|punk|hardcore|grindcore"),
    "Comédies musicales / Danse":    (False, r"musical|com[ée]die musicale|ballet|\bdanse\b|\bdance\b(?!\s*/\s*electronic)"),
    "Cirque / Magie / Jeune public": (False, r"circus|cirque|magi[ce]|illusion|children|enfant|jeune public|famil|puppet|marionnette"),
}
KEEP_UNCLASSIFIED = True  # garder les événements sans genre reconnu (souvent nombreux)

MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]

# Faux événements Ticketmaster (parkings, packs VIP...) à ignorer
EXCLUDE = re.compile(r"parking|pack ?vip|\bvip\b|hospitalit|upgrade|navette|shuttle|fast ?lane", re.I)
COMEDY = re.compile(r"comedy|humou?r|stand.?up|one.?man|one.?woman", re.I)
EXPO = re.compile(r"\bexpos?\b|exposition|exhibition|fine art|mus[ée]e|museum|galerie", re.I)
THEATRE = re.compile(r"theat|théât|drama|play", re.I)


# ----------------------------------------------------------------- utilitaires
def clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def first_sentences(text: str, n: int = 3, max_chars: int = 400) -> str:
    parts = re.split(r"(?<=[.!?])\s+", clean_html(text))
    out = " ".join(parts[:n]).strip()
    return out if len(out) <= max_chars else out[: max_chars - 1].rsplit(" ", 1)[0] + "…"


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def txt(v):
    """OpenAgenda renvoie parfois {'fr': '...'} au lieu d'une chaîne."""
    if isinstance(v, dict):
        return v.get("fr") or next(iter(v.values()), "")
    return v or ""


def categorize(*hints: str) -> str:
    joined = " ".join(h for h in hints if h)
    if COMEDY.search(joined):
        return "Stand-up / humour"
    if THEATRE.search(joined):
        return "Théâtre"
    if re.search(r"music|musique|concert|rock|pop|jazz|rap|hip|electro|metal|classi|chanson", joined, re.I):
        return "Concert"
    return "Spectacle"


# ----------------------------------------------------------------- Ticketmaster
def fetch_ticketmaster(key: str) -> list[dict]:
    now = datetime.now(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    events = []
    for segment in ("Music", "Arts & Theatre"):
        page = 0
        while page * 200 < 1000:  # limite de pagination de l'API
            r = requests.get(
                "https://app.ticketmaster.com/discovery/v2/events.json",
                params={
                    "apikey": key,
                    "latlong": f"{LYON[0]},{LYON[1]}",
                    "radius": RADIUS_KM,
                    "unit": "km",
                    "segmentName": segment,
                    "startDateTime": now.strftime(fmt),
                    "endDateTime": (now + timedelta(days=HORIZON_DAYS)).strftime(fmt),
                    "size": 200,
                    "page": page,
                    "sort": "date,asc",
                    "locale": "*",
                },
                timeout=30,
            )
            if r.status_code == 429:
                time.sleep(2)
                continue
            r.raise_for_status()
            data = r.json()
            for e in data.get("_embedded", {}).get("events", []):
                ev = parse_ticketmaster(e)
                if ev:
                    events.append(ev)
            page += 1
            if page >= data.get("page", {}).get("totalPages", 0):
                break
            time.sleep(0.3)  # 5 requêtes/s max
        else:
            print(f"⚠️  {segment} : plus de 1000 résultats, réduis HORIZON_DAYS", file=sys.stderr)
    print(f"Ticketmaster : {len(events)} événements")
    return events


def parse_ticketmaster(e: dict) -> dict | None:
    name = (e.get("name") or "").strip()
    dates = e.get("dates", {})
    if not name or EXCLUDE.search(name):
        return None
    if dates.get("status", {}).get("code") in ("cancelled", "canceled"):
        return None
    start = dates.get("start", {})
    d = start.get("localDate")
    if not d:
        return None
    t = start.get("localTime")
    if t and not start.get("timeTBA") and not start.get("noSpecificTime"):
        dt = datetime.fromisoformat(f"{d}T{t}").replace(tzinfo=TZ)
    else:
        dt = date.fromisoformat(d)  # heure inconnue -> journée entière

    venue = (e.get("_embedded", {}).get("venues") or [{}])[0]
    cls = (e.get("classifications") or [{}])[0]
    genre = cls.get("genre", {}).get("name", "")
    subgenre = cls.get("subGenre", {}).get("name", "")
    segment = cls.get("segment", {}).get("name", "")
    genre = "" if genre == "Undefined" else genre
    subgenre = "" if subgenre == "Undefined" else subgenre
    address = ", ".join(
        x for x in (venue.get("address", {}).get("line1"), venue.get("postalCode"), venue.get("city", {}).get("name")) if x
    )
    return {
        "uid": f"tm-{e['id']}",
        "title": name,
        "start": dt,
        "venue": venue.get("name", ""),
        "address": address,
        "url": e.get("url"),
        "category": categorize(genre, subgenre, segment),
        "genre": " / ".join(x for x in (genre, subgenre) if x),
        "tags": f"{genre} {subgenre}",
        "raw_text": " ".join(x for x in (e.get("description"), e.get("info")) if x),
        "source": "Ticketmaster",
    }


# ----------------------------------------------------------------- OpenAgenda
def fetch_openagenda(key: str, agenda_uids: list[str]) -> list[dict]:
    limit = datetime.now(TZ) + timedelta(days=HORIZON_DAYS)
    events = []
    for agenda in agenda_uids:
        after = None
        while True:
            params = {"key": key, "relative[]": ["current", "upcoming"], "detailed": 1, "size": 100, "monolingual": "fr"}
            if after:
                params["after[]"] = after
            r = requests.get(f"https://api.openagenda.com/v2/agendas/{agenda}/events", params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            batch = data.get("events", [])
            for e in batch:
                events.extend(parse_openagenda(e, agenda, limit))
            after = data.get("after")
            if not batch or not after:
                break
    print(f"OpenAgenda : {len(events)} séances")
    return events


def parse_openagenda(e: dict, agenda: str, limit: datetime) -> list[dict]:
    title = txt(e.get("title")).strip()
    if not title:
        return []
    loc = e.get("location") or {}
    link = next((reg.get("value") for reg in e.get("registration") or [] if reg.get("type") == "link"), None)
    kw = txt(e.get("keywords"))
    keywords = " ".join(kw) if isinstance(kw, list) else str(kw)
    out = []
    now = datetime.now(TZ)
    for i, timing in enumerate(e.get("timings") or []):
        try:
            begin = datetime.fromisoformat(timing["begin"]).astimezone(TZ)
            end = datetime.fromisoformat(timing["end"]).astimezone(TZ)
        except (KeyError, ValueError):
            continue
        if begin < now or begin > limit:
            continue
        out.append({
            "uid": f"oa-{agenda}-{e.get('uid')}-{i}",
            "title": title,
            "start": begin,
            "end": end if end > begin else None,
            "venue": loc.get("name", ""),
            "address": loc.get("address", ""),
            "url": link,
            "category": categorize(title, keywords, txt(e.get("description"))),
            "genre": "",
            "tags": keywords,
            "raw_text": txt(e.get("longDescription")) or txt(e.get("description")),
            "source": "OpenAgenda",
        })
    return out


# ----------------------------------------------------------------- descriptions
def fallback_description(ev: dict) -> str:
    if ev["raw_text"]:
        s = first_sentences(ev["raw_text"])
        if len(s) > 40:
            return s
    where = ev["venue"] + (f" ({ev['address'].split(', ')[-1]})" if ev["address"] else "")
    s = f"{ev['category']} « {ev['title']} » à {where}."
    if ev["genre"]:
        s += f" Genre : {ev['genre']}."
    return s + " Réservation via le lien de billetterie ci-dessous."


def claude_description(ev: dict, api_key: str) -> str | None:
    facts = {k: ev[k] for k in ("title", "category", "genre", "venue", "address")}
    facts["date"] = str(ev["start"])
    facts["texte_source"] = clean_html(ev["raw_text"])[:1500]
    prompt = (
        "Rédige en français une présentation de 2 à 3 phrases de cet événement pour un agenda personnel. "
        "Tu peux t'appuyer sur ce que tu sais de façon sûre de l'artiste ou de l'œuvre, mais n'invente "
        "aucun fait (prix, invités, durée...). Si tu ne connais pas l'artiste, reste sobre et factuel. "
        "Réponds uniquement avec le texte.\n\n" + json.dumps(facts, ensure_ascii=False)
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5", "max_tokens": 250, "messages": [{"role": "user", "content": prompt}]},
            timeout=60,
        )
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"]).strip() or None
    except Exception as exc:  # on ne bloque jamais la génération pour une description
        print(f"  description IA échouée pour {ev['title']!r} : {exc}", file=sys.stderr)
        return None


def add_descriptions(events: list[dict]) -> None:
    cache = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}
    api_key = os.getenv("ANTHROPIC_API_KEY")
    for ev in events:
        # une même tournée/pièce partage la description sur toutes ses dates
        ckey = norm(ev["title"]) + "|" + norm(ev["venue"])
        if ckey not in cache:
            cache[ckey] = (api_key and claude_description(ev, api_key)) or fallback_description(ev)
        ev["description"] = cache[ckey]
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True))


# ----------------------------------------------------------------- centres d'intérêt
def matches_interests(ev: dict) -> bool:
    tags, title = ev.get("tags", ""), ev["title"]
    if EXPO.search(f"{title} {tags} {ev['venue']}"):
        return True  # les expos sont gérées par EXCLUDE_EXPOS
    hit = lambda pat, text: re.search(pat, text, re.I)
    if any(hit(pat, tags) for on, pat in TYPES.values() if not on):
        return False                      # genre explicitement non voulu (ex. Rock / Punk)
    if any(hit(pat, tags) for on, pat in TYPES.values() if on):
        return True                       # genre voulu
    if any(hit(pat, title) for on, pat in TYPES.values() if not on):
        return False                      # pas de genre, mais le titre trahit (« Cirque… », « Orchestre… »)
    return KEEP_UNCLASSIFIED


# ----------------------------------------------------------------- événements longs
def day_of(start) -> date:
    return start.date() if isinstance(start, datetime) else start


def fmt_day(d: date) -> str:
    return f"{d.day} {MOIS[d.month - 1]} {d.year}"


def collapse_long_runs(events: list[dict]) -> list[dict]:
    """Regroupe en UNE entrée (journée entière, au premier jour) :
    - les expositions,
    - les événements présents presque tous les jours sur MIN_RUN_DAYS jours ou plus."""
    groups = defaultdict(list)
    for ev in events:
        groups[(norm(ev["title"]), norm(ev["venue"]))].append(ev)

    out = []
    for key, evs in groups.items():
        is_expo = any(EXPO.search(f"{e['title']} {e['genre']} {e['venue']}") for e in evs)
        if is_expo and EXCLUDE_EXPOS:
            continue
        days = sorted({day_of(e["start"]) for e in evs})
        span = (days[-1] - days[0]).days + 1
        is_daily = len(days) >= MIN_RUN_DAYS and len(days) >= 0.8 * span
        if len(days) < 2 or not (is_expo or is_daily):
            out.extend(evs)
            continue
        first = next(e for e in evs if day_of(e["start"]) == days[0])
        out.append(dict(
            first,
            uid="run-" + hashlib.md5("|".join(key).encode()).hexdigest()[:16],  # stable d'une semaine à l'autre
            start=days[0],
            end=None,
            last_day=days[-1],
            category="Exposition" if is_expo else first["category"],
            url=first["url"] or next((e["url"] for e in evs if e["url"]), None),
        ))
    return out


# ----------------------------------------------------------------- calendrier
def dedupe(events: list[dict]) -> list[dict]:
    seen, out = set(), []
    # Ticketmaster en premier : il a presque toujours un lien de billetterie
    for ev in sorted(events, key=lambda e: e["source"] != "Ticketmaster"):
        day = ev["start"] if isinstance(ev["start"], date) and not isinstance(ev["start"], datetime) else ev["start"].date()
        key = (norm(ev["title"]), day)
        if key not in seen:
            seen.add(key)
            out.append(ev)
    return out


def build_calendar(events: list[dict]) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//Agenda Lyon//FR")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", CAL_NAME)
    cal.add("x-wr-timezone", "Europe/Paris")
    cal.add("refresh-interval", timedelta(days=1), parameters={"VALUE": "DURATION"})
    cal.add("x-published-ttl", "PT12H")
    stamp = datetime.now(timezone.utc)

    for ev in events:
        ve = Event()
        ve.add("uid", f"{ev['uid']}@agenda-lyon")
        ve.add("dtstamp", stamp)
        emoji = {"Concert": "🎵", "Théâtre": "🎭", "Stand-up / humour": "🎤", "Exposition": "🖼"}.get(ev["category"], "🎟")
        title = f"{emoji} {ev['title']}"
        if ev.get("last_day"):
            title += f" (jusqu'au {ev['last_day'].day} {MOIS[ev['last_day'].month - 1]})"
        ve.add("summary", title)
        start = ev["start"]
        if isinstance(start, datetime):
            end = ev.get("end") or start + DEFAULT_DURATION
            ve.add("dtstart", start.astimezone(timezone.utc))  # UTC = zéro souci de fuseau
            ve.add("dtend", end.astimezone(timezone.utc))
        else:
            ve.add("dtstart", start)
            ve.add("dtend", start + timedelta(days=1))
        location = ", ".join(x for x in (ev["venue"], ev["address"]) if x)
        if location:
            ve.add("location", location)
        desc = ev["description"]
        if ev.get("last_day"):
            desc = f"📅 Du {fmt_day(ev['start'])} au {fmt_day(ev['last_day'])}.\n\n" + desc
        if ev["url"]:
            desc += f"\n\n🎟 Billetterie : {ev['url']}"
            ve.add("url", ev["url"])
        ve.add("description", desc)
        ve.add("categories", [ev["category"]])
        cal.add_component(ve)
    return cal.to_ical()


# ----------------------------------------------------------------- main
def main() -> None:
    events = []
    if key := os.getenv("TICKETMASTER_API_KEY"):
        events += fetch_ticketmaster(key)
    agendas = [a.strip() for a in os.getenv("OPENAGENDA_AGENDAS", "").split(",") if a.strip()]
    if (key := os.getenv("OPENAGENDA_KEY")) and agendas:
        try:
            events += fetch_openagenda(key, agendas)
        except Exception as exc:
            print(f"⚠️  OpenAgenda ignoré : {exc}", file=sys.stderr)

    before = len(events)
    events = [e for e in dedupe(events) if matches_interests(e)]
    print(f"Filtre centres d'intérêt : {len(events)} gardés sur {before}")
    events = collapse_long_runs(events)
    if not events:
        # Ne jamais écraser le calendrier existant en cas de panne d'API
        sys.exit("Aucun événement récupéré : calendrier existant conservé.")

    events.sort(key=lambda e: e["start"] if isinstance(e["start"], datetime)
                else datetime.combine(e["start"], datetime.min.time(), TZ))
    add_descriptions(events)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(build_calendar(events))
    print(f"✅ {len(events)} événements écrits dans {OUTPUT}")


if __name__ == "__main__":
    main()
