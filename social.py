#!/usr/bin/env python3
"""
Pubblicazione programmata di post (testo + locandina) su Facebook e Instagram.

Comandi:
  python social.py controlla        controlla post.yaml (nessuna connessione a Meta)
  python social.py prepara          prepara le immagini nel formato accettato da Instagram
  python social.py pubblica         pubblica i post arrivati all'orario previsto
  python social.py pubblica --prova mostra cosa verrebbe pubblicato, senza pubblicare
  python social.py verifica         controlla token e collegamento a Pagina e Instagram

Variabili d'ambiente (in GitHub sono i "Secrets"):
  META_PAGE_TOKEN   token di accesso della Pagina Facebook (non scade)
  FB_PAGE_ID        ID della Pagina Facebook
  IG_USER_ID        ID dell'account Instagram professionale collegato alla Pagina
Opzionali:
  GRAPH_VERSION     versione delle API Meta (default v25.0)
  TOLLERANZA_ORE    ritardo massimo accettato per un post in arretrato (default 6)
  FUSO_ORARIO       fuso degli orari in post.yaml (default Europe/Rome)
  URL_BASE_MEDIA    URL pubblico della radice del repository (default: raw.githubusercontent.com)
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml
from PIL import Image, ImageColor, ImageOps, ImageStat

ROOT = Path(__file__).resolve().parent
FILE_POST = ROOT / "post.yaml"
FILE_STATO = ROOT / "stato" / "pubblicati.json"
DIR_IG = ROOT / "media" / "instagram"

TZ = ZoneInfo(os.environ.get("FUSO_ORARIO", "Europe/Rome"))
GRAPH = f"https://graph.facebook.com/{os.environ.get('GRAPH_VERSION', 'v25.0')}"
TOLLERANZA = timedelta(hours=float(os.environ.get("TOLLERANZA_ORE", "6")))
MAX_TENTATIVI = 3

PIATTAFORME = ("facebook", "instagram")
# Instagram accetta proporzioni tra 4:5 (verticale) e 1.91:1 (orizzontale).
# Margine minimo per non sforare per arrotondamenti.
IG_RAPPORTO_MIN = 0.801
IG_RAPPORTO_MAX = 1.90
IG_LARGHEZZA_MAX = 1440
IG_MAX_CARATTERI = 2200
IG_MAX_HASHTAG = 30


class ErroreConfig(Exception):
    pass


class ErroreMeta(Exception):
    pass


# ---------------------------------------------------------------- post.yaml

def parse_data(valore):
    if isinstance(valore, datetime):
        dt = valore
    elif isinstance(valore, date):
        raise ValueError("manca l'orario")
    else:
        testo = str(valore).strip().replace("T", " ")
        for formato in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):
            try:
                dt = datetime.strptime(testo, formato)
                break
            except ValueError:
                continue
        else:
            raise ValueError("formato non riconosciuto")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def carica_post():
    if not FILE_POST.exists():
        raise ErroreConfig(f"File {FILE_POST.name} non trovato")
    dati = yaml.safe_load(FILE_POST.read_text(encoding="utf-8")) or {}
    voci = (dati.get("post") if isinstance(dati, dict) else dati) or []

    post, errori, visti = [], [], set()
    for n, voce in enumerate(voci, 1):
        if not isinstance(voce, dict):
            errori.append(f"post n.{n}: formato non valido")
            continue
        immagine = voce.get("immagine")
        pid = str(voce.get("id") or (Path(immagine).stem if immagine else "")).strip()
        dove = f"post '{pid or n}'"

        if not pid:
            errori.append(f"{dove}: manca 'id'")
        elif not re.fullmatch(r"[A-Za-z0-9_.-]+", pid):
            errori.append(f"{dove}: 'id' può contenere solo lettere, numeri, '-', '_' e '.'")
        elif pid in visti:
            errori.append(f"{dove}: 'id' duplicato")
        visti.add(pid)

        testo = str(voce.get("testo") or "").strip()
        if not testo:
            errori.append(f"{dove}: manca 'testo'")
        testo_ig = str(voce.get("testo_instagram") or testo).strip()

        if not immagine:
            errori.append(f"{dove}: manca 'immagine'")
        elif not (ROOT / immagine).is_file():
            errori.append(f"{dove}: immagine '{immagine}' non trovata nel repository")

        piattaforme = voce.get("piattaforme") or list(PIATTAFORME)
        if isinstance(piattaforme, str):
            piattaforme = [piattaforme]
        piattaforme = [str(p).lower().strip() for p in piattaforme]
        sconosciute = [p for p in piattaforme if p not in PIATTAFORME]
        if sconosciute:
            errori.append(f"{dove}: piattaforme non valide {sconosciute} (ammesse: facebook, instagram)")

        if "instagram" in piattaforme:
            if len(testo_ig) > IG_MAX_CARATTERI:
                errori.append(f"{dove}: testo per Instagram troppo lungo ({len(testo_ig)} > {IG_MAX_CARATTERI} caratteri)")
            n_hashtag = len(re.findall(r"(?<!\w)#\w", testo_ig))
            if n_hashtag > IG_MAX_HASHTAG:
                errori.append(f"{dove}: troppi hashtag per Instagram ({n_hashtag} > {IG_MAX_HASHTAG})")

        date_grezze = voce.get("date") or []
        if not isinstance(date_grezze, list):
            date_grezze = [date_grezze]
        if not date_grezze:
            errori.append(f"{dove}: manca 'date'")
        quando = []
        for d in date_grezze:
            try:
                quando.append(parse_data(d))
            except ValueError as e:
                errori.append(f"{dove}: data '{d}' non valida ({e}); usa il formato \"AAAA-MM-GG HH:MM\"")

        post.append({
            "id": pid,
            "testo": testo,
            "testo_ig": testo_ig,
            "immagine": immagine,
            "piattaforme": piattaforme,
            "date": sorted(set(quando)),
            "sfondo": str(voce.get("sfondo") or "auto"),
        })

    if errori:
        raise ErroreConfig("Errori in post.yaml:\n  - " + "\n  - ".join(errori))
    return post


# ---------------------------------------------------------------- stato

def carica_stato():
    if FILE_STATO.exists():
        return json.loads(FILE_STATO.read_text(encoding="utf-8") or "{}")
    return {}


def salva_stato(stato):
    FILE_STATO.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE_STATO.with_suffix(".tmp")
    tmp.write_text(json.dumps(stato, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(FILE_STATO)


def chiave(post, quando, piattaforma):
    return f"{post['id']}|{quando.strftime('%Y-%m-%d %H:%M')}|{piattaforma}"


def ora_iso():
    return datetime.now(TZ).isoformat(timespec="seconds")


# ---------------------------------------------------------------- immagini Instagram

def percorso_ig(post):
    sorgente = (ROOT / post["immagine"]).read_bytes()
    impronta = hashlib.sha256(sorgente + post["sfondo"].encode()).hexdigest()[:10]
    return DIR_IG / f"{post['id']}-{impronta}.jpg"


def colore_bordo(img):
    w, h = img.size
    strisce = [img.crop((0, 0, w, 1)), img.crop((0, h - 1, w, h)),
               img.crop((0, 0, 1, h)), img.crop((w - 1, 0, w, h))]
    pesi = [s.width * s.height for s in strisce]
    medie = [ImageStat.Stat(s).mean for s in strisce]
    return tuple(round(sum(m[c] * p for m, p in zip(medie, pesi)) / sum(pesi)) for c in range(3))


def prepara_immagine_ig(post):
    """Converte in JPEG e aggiunge bande laterali se le proporzioni non sono accettate da Instagram
    (es. una locandina A4 è più stretta del 4:5)."""
    dest = percorso_ig(post)
    if dest.exists():
        return dest, False

    img = ImageOps.exif_transpose(Image.open(ROOT / post["immagine"]))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        fondo = Image.new("RGB", img.size, (255, 255, 255))
        fondo.paste(img, mask=img.split()[-1])
        img = fondo
    else:
        img = img.convert("RGB")

    colore = colore_bordo(img) if post["sfondo"] == "auto" else ImageColor.getrgb(post["sfondo"])
    w, h = img.size
    rapporto = w / h
    if rapporto < IG_RAPPORTO_MIN:
        nuova_w = math.ceil(h * IG_RAPPORTO_MIN)
        tela = Image.new("RGB", (nuova_w, h), colore)
        tela.paste(img, ((nuova_w - w) // 2, 0))
        img = tela
    elif rapporto > IG_RAPPORTO_MAX:
        nuova_h = math.ceil(w / IG_RAPPORTO_MAX)
        tela = Image.new("RGB", (w, nuova_h), colore)
        tela.paste(img, (0, (nuova_h - h) // 2))
        img = tela

    if img.width > IG_LARGHEZZA_MAX:
        img = img.resize((IG_LARGHEZZA_MAX, round(img.height * IG_LARGHEZZA_MAX / img.width)), Image.LANCZOS)

    DIR_IG.mkdir(parents=True, exist_ok=True)
    for vecchia in DIR_IG.glob("*.jpg"):  # versioni precedenti della stessa locandina
        if vecchia.stem.rsplit("-", 1)[0] == post["id"]:
            vecchia.unlink()
    img.save(dest, "JPEG", quality=90, optimize=True)
    return dest, True


def url_immagine_ig(post):
    percorso = percorso_ig(post)
    if not percorso.exists():
        raise ErroreMeta(f"immagine Instagram non preparata ({percorso.name}): esegui prima 'prepara'")
    relativo = percorso.relative_to(ROOT).as_posix()
    base = os.environ.get("URL_BASE_MEDIA")
    if not base:
        repo, sha = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("URL_SHA") or os.environ.get("GITHUB_SHA")
        if not repo or not sha:
            raise ErroreMeta("impossibile costruire l'URL pubblico dell'immagine (manca URL_BASE_MEDIA)")
        base = f"https://raw.githubusercontent.com/{repo}/{sha}"
    url = f"{base.rstrip('/')}/{relativo}"
    # l'immagine appena caricata può impiegare qualche secondo a essere raggiungibile
    for _ in range(6):
        try:
            if requests.head(url, timeout=20, allow_redirects=True).status_code == 200:
                return url
        except requests.RequestException:
            pass
        time.sleep(5)
    raise ErroreMeta(f"immagine non raggiungibile pubblicamente: {url} (il repository è pubblico?)")


def da_considerare(quando, ora):
    return ora - quando <= TOLLERANZA


# ---------------------------------------------------------------- API Meta

@dataclass
class Config:
    token: str
    page_id: str
    ig_id: str

    @classmethod
    def da_env(cls):
        mancanti = [n for n in ("META_PAGE_TOKEN", "FB_PAGE_ID") if not os.environ.get(n)]
        if mancanti:
            raise ErroreConfig("Mancano le variabili/secret: " + ", ".join(mancanti))
        return cls(os.environ["META_PAGE_TOKEN"].strip(), os.environ["FB_PAGE_ID"].strip(),
                   os.environ.get("IG_USER_ID", "").strip())


def graph(metodo, percorso, token, **kwargs):
    intestazioni = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.request(metodo, f"{GRAPH}/{percorso}", headers=intestazioni, timeout=120, **kwargs)
    except requests.RequestException as e:
        raise ErroreMeta(f"errore di rete verso Meta: {type(e).__name__}") from None
    try:
        dati = r.json()
    except ValueError:
        raise ErroreMeta(f"risposta non valida da Meta (HTTP {r.status_code})") from None
    if "error" in dati:
        e = dati["error"]
        dettaglio = e.get("error_user_msg") or e.get("message")
        raise ErroreMeta(f"Meta {e.get('code')}/{e.get('error_subcode', '-')}: {dettaglio}")
    if not r.ok:
        raise ErroreMeta(f"HTTP {r.status_code} da Meta")
    return dati


def pubblica_facebook(post, cfg):
    sorgente = ROOT / post["immagine"]
    with open(sorgente, "rb") as f:
        dati = graph("POST", f"{cfg.page_id}/photos", cfg.token,
                     data={"message": post["testo"], "published": "true"},
                     files={"source": (sorgente.name, f)})
    return dati.get("post_id") or dati.get("id")


def pubblica_instagram(post, cfg):
    if not cfg.ig_id:
        raise ErroreConfig("Manca IG_USER_ID")
    contenitore = graph("POST", f"{cfg.ig_id}/media", cfg.token,
                        data={"image_url": url_immagine_ig(post), "caption": post["testo_ig"]})["id"]
    for _ in range(30):
        s = graph("GET", contenitore, cfg.token, params={"fields": "status_code,status"})
        codice = s.get("status_code")
        if codice == "FINISHED":
            break
        if codice in ("ERROR", "EXPIRED"):
            raise ErroreMeta(f"Instagram ha rifiutato l'immagine ({codice}): {s.get('status')}")
        time.sleep(4)
    else:
        raise ErroreMeta("Instagram non ha finito di elaborare l'immagine in tempo")
    return graph("POST", f"{cfg.ig_id}/media_publish", cfg.token, data={"creation_id": contenitore})["id"]


# ---------------------------------------------------------------- comandi

def cmd_controlla(_args):
    posts = carica_post()
    stato = carica_stato()
    ora = datetime.now(TZ)
    print(f"post.yaml valido: {len(posts)} post.\n")
    for p in posts:
        try:
            with Image.open(ROOT / p["immagine"]) as img:
                img.verify()
        except Exception as e:
            raise ErroreConfig(f"post '{p['id']}': immagine non leggibile ({e})")
        print(f"• {p['id']}  [{', '.join(p['piattaforme'])}]  {p['immagine']}")
        for q in p["date"]:
            esiti = {pl: stato.get(chiave(p, q, pl), {}).get("esito") for pl in p["piattaforme"]}
            if all(e == "ok" for e in esiti.values()):
                nota = "pubblicato"
            elif q > ora:
                nota = "in programma"
            elif da_considerare(q, ora):
                nota = "da pubblicare al prossimo giro"
            else:
                nota = "passato" + ("" if any(esiti.values()) else " (non verrà pubblicato)")
            print(f"    {q.strftime('%a %d/%m/%Y %H:%M')}  → {nota}")
    return 0


def cmd_prepara(_args):
    posts = carica_post()
    ora = datetime.now(TZ)
    create = 0
    for p in posts:
        if "instagram" in p["piattaforme"] and any(da_considerare(q, ora) for q in p["date"]):
            percorso, nuova = prepara_immagine_ig(p)
            if nuova:
                create += 1
                print(f"Preparata {percorso.relative_to(ROOT)}")
    print(f"Immagini Instagram nuove: {create}")
    return 0


def cmd_pubblica(args):
    posts = carica_post()
    stato = carica_stato()
    cfg = None if args.prova else Config.da_env()
    ora = datetime.now(TZ)
    pubblicati = errori = 0

    for p in posts:
        for quando in p["date"]:
            if quando > ora:
                continue
            for piattaforma in p["piattaforme"]:
                k = chiave(p, quando, piattaforma)
                record = stato.get(k, {})
                if record.get("esito") in ("ok", "saltato", "fallito"):
                    continue
                etichetta = f"{p['id']} ({quando.strftime('%d/%m %H:%M')}) su {piattaforma}"

                if not da_considerare(quando, ora):
                    print(f"::warning::{etichetta}: saltato, in ritardo di oltre {TOLLERANZA}")
                    if args.prova:
                        continue
                    stato[k] = {"esito": "saltato", "il": ora_iso(), "motivo": "troppo in ritardo"}
                    salva_stato(stato)
                    continue

                if args.prova:
                    print(f"[PROVA] pubblicherei {etichetta}")
                    continue

                try:
                    if piattaforma == "facebook":
                        id_meta = pubblica_facebook(p, cfg)
                    else:
                        id_meta = pubblica_instagram(p, cfg)
                    stato[k] = {"esito": "ok", "il": ora_iso(), "id": id_meta}
                    pubblicati += 1
                    print(f"Pubblicato {etichetta} → {id_meta}")
                except (ErroreMeta, ErroreConfig, OSError) as e:
                    tentativi = record.get("tentativi", 0) + 1
                    definitivo = tentativi >= MAX_TENTATIVI
                    stato[k] = {"esito": "fallito" if definitivo else "errore", "il": ora_iso(),
                                "tentativi": tentativi, "errore": str(e)[:500]}
                    errori += 1
                    print(f"::error::{etichetta}: {e}"
                          + (" (rinuncio dopo 3 tentativi)" if definitivo else " (riprovo al prossimo giro)"))
                salva_stato(stato)

    print(f"Pubblicati: {pubblicati}, errori: {errori}")
    return 1 if errori else 0


def cmd_verifica(_args):
    cfg = Config.da_env()
    pagina = graph("GET", "me", cfg.token, params={"fields": "id,name"})
    print(f"Token valido per la Pagina: {pagina.get('name')} (ID {pagina.get('id')})")
    if pagina.get("id") != cfg.page_id:
        print(f"::warning::FB_PAGE_ID ({cfg.page_id}) non corrisponde alla Pagina del token ({pagina.get('id')})")
    collegato = graph("GET", cfg.page_id, cfg.token, params={"fields": "instagram_business_account"})
    ig_collegato = (collegato.get("instagram_business_account") or {}).get("id")
    if not ig_collegato:
        print("::warning::Nessun account Instagram professionale collegato alla Pagina")
    elif cfg.ig_id and cfg.ig_id != ig_collegato:
        print(f"::warning::IG_USER_ID ({cfg.ig_id}) diverso dall'account collegato alla Pagina ({ig_collegato})")
    if cfg.ig_id:
        ig = graph("GET", cfg.ig_id, cfg.token, params={"fields": "username"})
        print(f"Instagram: @{ig.get('username')} (ID {cfg.ig_id})")
        limite = graph("GET", f"{cfg.ig_id}/content_publishing_limit", cfg.token,
                       params={"fields": "quota_usage,config"})
        dati = (limite.get("data") or [{}])[0]
        print(f"Post Instagram via API nelle ultime 24h: {dati.get('quota_usage')} "
              f"(limite {(dati.get('config') or {}).get('quota_total', '?')})")
    print("Tutto a posto.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Pubblicazione programmata su Facebook e Instagram")
    sotto = parser.add_subparsers(dest="comando", required=True)
    sotto.add_parser("controlla").set_defaults(func=cmd_controlla)
    sotto.add_parser("prepara").set_defaults(func=cmd_prepara)
    p = sotto.add_parser("pubblica")
    p.add_argument("--prova", action="store_true", help="mostra cosa verrebbe pubblicato senza pubblicare")
    p.set_defaults(func=cmd_pubblica)
    sotto.add_parser("verifica").set_defaults(func=cmd_verifica)
    args = parser.parse_args()
    try:
        return args.func(args)
    except (ErroreConfig, ErroreMeta) as e:
        print(f"::error::{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
