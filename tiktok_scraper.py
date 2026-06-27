"""
TikTok Influencer Scraper — Mode interactif + CLI
==================================================

Modes d'utilisation :

  1. Interactif (recommandé) :
       python tiktok_scraper.py
       → Menu de sélection du pays, saisie des paramètres, puis lancement

  2. CLI express (sans interaction) :
       python tiktok_scraper.py --pays CI --min 5000 --max 200000 --profils 100
       python tiktok_scraper.py --pays FR --min 10000 --max 0 --profils 500
       python tiktok_scraper.py --pays SN --categories cuisine mode --min 5000

  3. Avec fichier de config YAML :
       python tiktok_scraper.py --config config.yaml

  4. Commandes utilitaires :
       python tiktok_scraper.py --list-pays
       python tiktok_scraper.py --list-categories

Stratégie "filet large" (sans catégorie) :
  - Tous les hashtags viraux/tendance du pays sont parcourus
  - Tous les créateurs avec le bon nombre de followers sont acceptés
  - Aucun filtre catégorie → volume maximal
"""

import csv
import re
import sys
import json
import time
import random
import logging
import argparse
import hashlib
import unicodedata
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict, field
from typing import Optional
from urllib.parse import quote_plus

import requests
import yaml
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import WebDriverException
from webdriver_manager.chrome import ChromeDriverManager

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# CATÉGORIES
# ─────────────────────────────────────────────────────────────────────────────
CATEGORIES_KEYWORDS: dict[str, list[str]] = {
    "fitness":       ["fitness", "musculation", "workout", "entrainement", "gym"],
    "yoga":          ["yoga", "meditation", "bien etre"],
    "cuisine":       ["recette", "cuisine", "food", "gastronomie", "chef", "garba", "attieke", "alloco"],
    "voyage":        ["voyage", "travel", "aventure", "vacances"],
    "mode":          ["mode", "fashion", "outfit", "style", "tendance"],
    "beaute":        ["beaute", "maquillage", "skincare", "makeup", "cosmetique"],
    "famille":       ["famille", "parentalite", "bebe", "enfants", "maman"],
    "gaming":        ["gaming", "gamer", "jeu video", "esport", "stream"],
    "tech":          ["tech", "technologie", "smartphone", "ia", "informatique"],
    "coding":        ["programmation", "developpeur", "code", "python", "dev"],
    "finance":       ["investissement", "crypto", "bourse", "finance", "trading"],
    "business":      ["entrepreneur", "startup", "marketing", "business"],
    "musique":       ["musique", "chanteur", "beatmaker", "rap", "cover"],
    "art":           ["art", "dessin", "peinture", "illustration", "creatif"],
    "cinema":        ["cinema", "film", "serie", "critique", "acteur"],
    "comedie":       ["humour", "sketch", "comedy", "blague", "drole", "comedie"],
    "education":     ["apprendre", "cours", "astuce", "conseil", "tuto"],
    "developpement": ["developpement personnel", "motivation", "mindset"],
    "sport":         ["sport", "football", "basket", "tennis", "athlete"],
    "nature":        ["nature", "animaux", "ecologie", "jardinage"],
    "politique":     ["politique", "election", "gouvernement", "president", "depute", "assemblee", "debat politique", "geopolitique", "societe"],
    "actualite":     ["actualite", "actu", "news", "info", "journal", "reportage", "media", "breaking", "fait divers"],
    "lifestyle":     ["lifestyle", "routine", "vie quotidienne", "day in my life", "aesthetic", "morning routine"],
    "immobilier":    ["immobilier", "immo", "appartement", "investissement locatif", "real estate"],
    "automobile":    ["automobile", "voiture", "auto", "car", "supercar", "mecanique"],
    "sante":         ["sante", "nutrition", "medecine", "bien etre", "psychologie", "mental"],
    "danse":         ["danse", "dance", "choregraphie", "afrobeat dance", "dancer"],
    "general":       ["vlog", "viral", "storytime", "journee"],
}

CATEGORIES_DISPONIBLES = sorted(CATEGORIES_KEYWORDS.keys())


# ─────────────────────────────────────────────────────────────────────────────
# CHARGEMENT DES PAYS
# ─────────────────────────────────────────────────────────────────────────────
def charger_pays(countries_file: str = "countries.yaml") -> dict:
    path = Path(countries_file)
    if not path.exists():
        log.warning(f"Fichier pays introuvable : {countries_file}")
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    log.info(f"{len(data)} pays chargés : {', '.join(data.keys())}")
    return data


# ─────────────────────────────────────────────────────────────────────────────
# OUTILS TEXTE
# ─────────────────────────────────────────────────────────────────────────────
def normalize_text(value: str) -> str:
    if not value:
        return ""
    value = value.lower()
    value = unicodedata.normalize("NFD", value)
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    value = value.replace("\u2019", "'")
    return value


def parse_count(texte: str) -> int:
    if not texte:
        return 0
    texte = normalize_text(texte)
    texte = re.sub(r"[\s,\u202f\xa0]", "", texte)
    m = re.match(r"([\d.]+)([km]?)", texte)
    if not m:
        return 0
    nb = float(m.group(1))
    if m.group(2) == "k":
        nb *= 1_000
    elif m.group(2) == "m":
        nb *= 1_000_000
    return int(nb)


def delai_aleatoire(minimum: float = 3.0, maximum: float = 8.0) -> None:
    time.sleep(random.uniform(minimum, maximum))


def fingerprint_url(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def url_vers_username(url: str) -> str:
    match = re.search(r"tiktok\.com/@([^/?#]+)", url)
    if match:
        return match.group(1).strip()
    return url.rstrip("/").split("/")[-1].lstrip("@")


def username_depuis_video_url(url: str) -> Optional[str]:
    match = re.search(r"tiktok\.com/@([^/?#]+)/video/", url)
    if match:
        return match.group(1).strip()
    return None


def detecter_categories(
    categories_cibles: list[str],
    bio: str = "",
    username: str = "",
    sources: str = "",
) -> list[str]:
    """
    Détecte les catégories en cherchant dans :
      1. La bio du profil
      2. L'username (@artbymarco, @dessinatrice…)
      3. Les hashtags/sources qui ont conduit à ce créateur
    Un match dans l'une des trois zones suffit.
    """
    if not categories_cibles:
        return []
    texte = normalize_text(f"{bio} {username} {sources}")
    return [
        cat for cat in categories_cibles
        if any(normalize_text(kw) in texte for kw in CATEGORIES_KEYWORDS.get(cat, [cat]))
    ]


# ─────────────────────────────────────────────────────────────────────────────
# MODÈLE CSV — minimaliste et traçable
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Influenceur:
    date_collecte: str    # YYYY-MM-DD HH:MM:SS — quand le profil a été enregistré
    pays: str             # Code pays cible (CI, FR, SN…)
    pseudo: str           # @username TikTok
    nom: str              # Nom affiché sur le profil
    followers: int        # Nombre d'abonnés
    likes_total: int      # Total des likes sur la chaîne
    nb_videos: int        # Nombre de vidéos publiées
    engagement: float     # Ratio likes/followers (ex: 0.1234 = 12.34%)
    bio: str              # Bio du créateur (tronquée à 200 chars)
    langue: str           # Langue détectée par TikTok
    region: str           # Région/pays TikTok du compte
    score_pays: int       # Score d'appartenance au pays (0–20+)
    categories: str       # Catégories détectées, séparées par |
    url: str              # URL profil TikTok
    sources: str          # Hashtags/mots-clés qui ont permis de trouver ce compte

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class CreatorCandidate:
    username: str
    profile_url: str
    source_terms: set[str] = field(default_factory=set)
    video_urls: set[str] = field(default_factory=set)

    def add_source(self, term: str, video_url: Optional[str] = None) -> None:
        if term:
            self.source_terms.add(term)
        if video_url:
            self.video_urls.add(video_url)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Config:
    pays_code: str = "CI"
    langue_code: str = ""
    tiktok_locale: str = ""
    datacenter_cookie: str = ""

    min_followers: int = 5_000
    max_followers: int = 200_000   # 0 = illimité

    ville: str = ""                                       # Ville cible (ex: "Paris") — "" = pays entier
    niche_libre: str = ""                                 # Niche personnalisée libre (ex: "crypto memecoin")

    categories: list[str] = field(default_factory=list)   # [] = filet large
    mots_cles: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)

    max_profils: int = 100
    max_videos_par_recherche: int = 150
    max_scrolls_par_recherche: int = 20
    stop_apres_scrolls_sans_nouveau: int = 5

    min_country_score: int = 1   # Abaissé à 1 pour filet large

    output_csv: str = ""
    checkpoint_file: str = ""

    delai_min: float = 4.0
    delai_max: float = 9.0
    scroll_pause: float = 3.0
    max_retries: int = 3

    collect_from_hashtags: bool = True
    collect_from_search: bool = True   # Activé par défaut pour volume max

    countries_file: str = "countries.yaml"

    @classmethod
    def from_yaml(cls, path: str) -> "Config":
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        valid = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in valid})

    @classmethod
    def from_args(cls, args: argparse.Namespace, base: Optional["Config"] = None) -> "Config":
        c = base or cls()
        if getattr(args, "pays", None):
            c.pays_code = args.pays.upper()
        if getattr(args, "langue", None):
            c.langue_code = args.langue
        if getattr(args, "locale", None):
            c.tiktok_locale = args.locale
        if getattr(args, "min", None) is not None:
            c.min_followers = args.min
        if getattr(args, "max", None) is not None:
            c.max_followers = args.max
        if getattr(args, "profils", None):
            c.max_profils = args.profils
        if getattr(args, "ville", None):
            c.ville = args.ville
        if getattr(args, "niche_libre", None):
            c.niche_libre = args.niche_libre
        if getattr(args, "categories", None):
            c.categories = args.categories
        if getattr(args, "mots_cles", None):
            c.mots_cles = args.mots_cles
        if getattr(args, "hashtags", None):
            c.hashtags = args.hashtags
        if getattr(args, "output", None):
            c.output_csv = args.output
        if getattr(args, "min_score", None) is not None:
            c.min_country_score = args.min_score
        if getattr(args, "countries_file", None):
            c.countries_file = args.countries_file
        if getattr(args, "from_hashtags", None) is not None:
            c.collect_from_hashtags = args.from_hashtags
        if getattr(args, "from_search", None) is not None:
            c.collect_from_search = args.from_search
        return c

    def apply_country_defaults(self, country_targets: dict) -> None:
        target = country_targets.get(self.pays_code)
        if not target:
            log.warning(
                f"Pays '{self.pays_code}' absent de countries.yaml. "
                "Utilisez --hashtags pour définir les sources."
            )
            self.langue_code = self.langue_code or "fr"
            self.tiktok_locale = self.tiktok_locale or f"fr-{self.pays_code}"
            self.datacenter_cookie = self.datacenter_cookie or "fra02"
        else:
            self.langue_code = self.langue_code or target.get("langue_code", "fr")
            self.tiktok_locale = self.tiktok_locale or target.get("tiktok_locale", f"fr-{self.pays_code}")
            self.datacenter_cookie = self.datacenter_cookie or target.get("datacenter_cookie", "fra02")
            if not self.hashtags:
                self.hashtags = target.get("hashtags", [])

        # Ciblage ville : on place les hashtags de la ville en tête de liste
        if self.ville:
            ville_tag = re.sub(r"[^a-z0-9]", "", normalize_text(self.ville))
            if ville_tag:
                extra = [ville_tag, f"{ville_tag}tiktok", f"{ville_tag}vie", f"vlog{ville_tag}"]
                self.hashtags = list(dict.fromkeys(extra + self.hashtags))

        pays_lower = self.pays_code.lower()
        ville_slug = re.sub(r"[^a-z0-9]", "", normalize_text(self.ville)) if self.ville else ""
        suffixe = f"{pays_lower}_{ville_slug}" if ville_slug else pays_lower
        if not self.output_csv:
            ts = datetime.now().strftime("%Y%m%d_%H%M")
            self.output_csv = f"tiktok_{suffixe}_{ts}.csv"
        if not self.checkpoint_file:
            self.checkpoint_file = f"checkpoint_{suffixe}.json"

    def build_mots_cles(self, country_targets: dict) -> list[str]:
        if self.mots_cles:
            return self.mots_cles

        target = country_targets.get(self.pays_code, {})
        local_keywords = target.get("local_keywords", [])
        default_phrases = target.get("default_search_phrases", [])
        viral_phrases = target.get("viral_search_phrases", [])

        # Si une ville est précisée, elle devient l'ancrage géographique principal.
        # Sinon on retombe sur les mots-clés locaux du pays.
        ancres = [self.ville] if self.ville else local_keywords[:10]

        mots = []
        if self.categories:
            for cat in self.categories:
                cat_keywords = CATEGORIES_KEYWORDS.get(cat, [cat])
                for ancre in ancres:
                    for ck in cat_keywords[:3]:
                        mots.append(f"{ck} {ancre}")
                        mots.append(f"{ancre} {ck}")
        elif self.ville:
            # Filet large mais centré sur la ville
            for phrase in ["vlog", "ma journee", "vie a", "routine", "tiktok", "humour", "influenceur"]:
                mots.append(f"{phrase} {self.ville}")
        else:
            # Filet large national : on empile tout
            mots.extend(local_keywords)
            mots.extend(default_phrases)
            mots.extend(viral_phrases)

        return list(dict.fromkeys(mots))


# ─────────────────────────────────────────────────────────────────────────────
# CHECKPOINT
# ─────────────────────────────────────────────────────────────────────────────
class Checkpoint:
    def __init__(self, path: str):
        self.path = Path(path)
        self.data: dict = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        return {"traites": [], "valides": 0}

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def deja_traite(self, url: str) -> bool:
        return fingerprint_url(url) in self.data["traites"]

    def marquer(self, url: str, valide: bool = False) -> None:
        fp = fingerprint_url(url)
        if fp not in self.data["traites"]:
            self.data["traites"].append(fp)
        if valide:
            self.data["valides"] += 1
        self.save()

    @property
    def nb_valides(self) -> int:
        return self.data["valides"]


# ─────────────────────────────────────────────────────────────────────────────
# DRIVER
# ─────────────────────────────────────────────────────────────────────────────
class DriverFactory:
    @staticmethod
    def creer(config: Config) -> webdriver.Chrome:
        opts = webdriver.ChromeOptions()
        opts.add_argument(f"--lang={config.tiktok_locale}")
        opts.add_argument("--disable-notifications")
        opts.add_argument("--window-size=1440,900")
        opts.add_experimental_option("prefs", {
            "intl.accept_languages": f"{config.tiktok_locale},{config.langue_code};q=0.9",
            "profile.default_content_setting_values.geolocation": 1,
        })
        return webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=opts,
        )


# ─────────────────────────────────────────────────────────────────────────────
# EXTRACTION PROFIL
# ─────────────────────────────────────────────────────────────────────────────
class ProfileExtractor:
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "{locale},fr;q=0.9,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.tiktok.com/",
    }

    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            k: v.format(locale=config.tiktok_locale) for k, v in self.HEADERS.items()
        })

    def extraire_metadata(self, username: str) -> dict:
        url = f"https://www.tiktok.com/@{username}"
        for attempt in range(self.config.max_retries):
            try:
                r = self.session.get(url, timeout=15)
                if r.status_code == 429:
                    log.warning("Rate limit 429 — pause 30s")
                    time.sleep(30)
                    continue
                if r.status_code != 200:
                    log.warning(f"HTTP {r.status_code} pour @{username}")
                    return {}
                soup = BeautifulSoup(r.text, "html.parser")
                return (
                    self._parse_universal(soup)
                    or self._parse_next_data(soup)
                    or {}
                )
            except requests.RequestException as e:
                log.warning(f"Tentative {attempt + 1}/{self.config.max_retries} : {e}")
                time.sleep(5 * (attempt + 1))
        return {}

    def _parse_universal(self, soup: BeautifulSoup) -> dict:
        script = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")
        if not script or not script.string:
            return {}
        try:
            raw = json.loads(script.string)
            ui = (
                raw.get("__DEFAULT_SCOPE__", {})
                .get("webapp.user-detail", {})
                .get("userInfo", {})
            )
            return self._build_meta(ui.get("user", {}), ui.get("stats", {}))
        except Exception:
            return {}

    def _parse_next_data(self, soup: BeautifulSoup) -> dict:
        script = soup.find("script", id="__NEXT_DATA__")
        if not script or not script.string:
            return {}
        try:
            raw = json.loads(script.string)
            ui = (
                raw.get("props", {})
                .get("pageProps", {})
                .get("userInfo", {})
            )
            return self._build_meta(ui.get("user", {}), ui.get("stats", {}))
        except Exception:
            return {}

    @staticmethod
    def _build_meta(user: dict, stats: dict) -> dict:
        if not user and not stats:
            return {}
        return {
            "langue":    user.get("language", ""),
            "region":    user.get("region", ""),
            "bio":       user.get("signature", ""),
            "verified":  user.get("verified", False),
            "followers": stats.get("followerCount", 0),
            "likes":     stats.get("heartCount", 0),
            "videos":    stats.get("videoCount", 0),
        }


# ─────────────────────────────────────────────────────────────────────────────
# COLLECTEUR
# ─────────────────────────────────────────────────────────────────────────────
class UrlCollector:
    def __init__(self, driver: webdriver.Chrome, config: Config):
        self.driver = driver
        self.config = config

    def initialiser(self) -> None:
        home = f"https://www.tiktok.com/{self.config.langue_code}/"
        log.info(f"Ouverture TikTok : {home}")
        self.driver.get(home)
        time.sleep(4)
        for name, value in {
            "tt-target-idc":  self.config.datacenter_cookie,
            "app_language":   self.config.langue_code,
            "webapp_language": self.config.langue_code,
        }.items():
            try:
                self.driver.add_cookie({"name": name, "value": value})
            except Exception:
                pass
        self.driver.refresh()
        time.sleep(3)

    def depuis_hashtag(self, hashtag: str) -> dict[str, CreatorCandidate]:
        url = f"https://www.tiktok.com/tag/{quote_plus(hashtag)}"
        return self._collecter(url, source_term=f"#{hashtag}")

    def depuis_recherche(self, mot_cle: str) -> dict[str, CreatorCandidate]:
        url = f"https://www.tiktok.com/search/video?q={quote_plus(mot_cle)}"
        return self._collecter(url, source_term=mot_cle)

    def _collecter(self, url: str, source_term: str) -> dict[str, CreatorCandidate]:
        log.info(f"  → '{source_term}'")
        self.driver.get(url)
        time.sleep(5)

        candidates: dict[str, CreatorCandidate] = {}
        sans_nouveau = 0
        scroll_count = 0

        while (
            scroll_count < self.config.max_scrolls_par_recherche
            and sans_nouveau < self.config.stop_apres_scrolls_sans_nouveau
            and len(candidates) < self.config.max_videos_par_recherche
        ):
            nouveau = 0
            soup = BeautifulSoup(self.driver.page_source, "html.parser")

            for video_url in self._extraire_video_urls(soup):
                username = username_depuis_video_url(video_url)
                if not username:
                    continue
                profile_url = f"https://www.tiktok.com/@{username}"
                if username not in candidates:
                    candidates[username] = CreatorCandidate(username=username, profile_url=profile_url)
                    nouveau += 1
                candidates[username].add_source(source_term, video_url)

            for profile_url in self._extraire_profile_urls(soup):
                username = url_vers_username(profile_url)
                if not username:
                    continue
                if username not in candidates:
                    candidates[username] = CreatorCandidate(username=username, profile_url=profile_url)
                    nouveau += 1
                candidates[username].add_source(source_term)

            log.info(
                f"    Scroll {scroll_count + 1} — {len(candidates)} créateurs (+{nouveau})"
            )
            sans_nouveau = 0 if nouveau else sans_nouveau + 1
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(random.uniform(self.config.scroll_pause, self.config.scroll_pause + 2.5))
            scroll_count += 1

        return candidates

    def _extraire_video_urls(self, soup: BeautifulSoup) -> set[str]:
        urls = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/video/" not in href:
                continue
            if href.startswith("/"):
                href = "https://www.tiktok.com" + href
            href = href.split("?")[0]
            if "tiktok.com/@" in href:
                urls.add(href)
        return urls

    def _extraire_profile_urls(self, soup: BeautifulSoup) -> set[str]:
        urls = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/@" not in href or "/video/" in href or href in ("/@", "/@/"):
                continue
            if href.startswith("/"):
                href = "https://www.tiktok.com" + href
            href = href.split("?")[0].rstrip("/")
            if re.search(r"tiktok\.com/@[^/?#]+$", href):
                urls.add(href)
        return urls

    def construire_influenceur(
        self,
        candidate: CreatorCandidate,
        meta: dict,
        country_score: int,
        pays_code: str,
        categories_cibles: list[str],
    ) -> Optional[Influenceur]:
        url = candidate.profile_url
        try:
            self.driver.get(url)
            time.sleep(3)
        except Exception:
            log.warning(f"  Profil inaccessible : {url}")
            return None

        soup = BeautifulSoup(self.driver.page_source, "html.parser")

        def txt(selector: str) -> str:
            el = soup.find(attrs={"data-e2e": selector})
            return el.get_text(strip=True) if el else ""

        nom        = txt("user-title") or candidate.username
        pseudo     = txt("user-subtitle").lstrip("@") or candidate.username
        followers  = meta.get("followers") or parse_count(txt("followers-count"))
        likes      = meta.get("likes")     or parse_count(txt("likes-count"))
        nb_videos  = meta.get("videos")    or parse_count(txt("video-count"))
        bio        = meta.get("bio", "")
        engagement = round(likes / followers, 4) if followers > 0 else 0.0
        sources_str = ",".join(sorted(candidate.source_terms))
        cats        = detecter_categories(
            categories_cibles,
            bio=bio,
            username=candidate.username,
            sources=sources_str,
        )

        return Influenceur(
            date_collecte=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            pays=pays_code,
            pseudo=pseudo,
            nom=nom,
            followers=followers,
            likes_total=likes,
            nb_videos=nb_videos,
            engagement=engagement,
            bio=bio[:200],
            langue=meta.get("langue", ""),
            region=meta.get("region", ""),
            score_pays=country_score,
            categories="|".join(cats) if cats else "",
            url=url,
            sources=",".join(sorted(candidate.source_terms))[:150],
        )


# ─────────────────────────────────────────────────────────────────────────────
# SCRAPER PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────
class TikTokScraper:
    def __init__(
        self,
        config: Config,
        country_targets: dict,
        progress_callback=None,
        stop_event=None,
    ):
        self.config = config
        self.country_targets = country_targets
        # Callback(nb_valides: int, max_profils: int, dernier_profil: dict) → None
        self.progress_callback = progress_callback
        # threading.Event() — si .is_set(), arrêt propre demandé par l'UI
        self.stop_event = stop_event

        # Niche libre → catégorie dynamique "__perso__"
        if self.config.niche_libre:
            tokens = [t.strip() for t in re.split(r"[,/]| et ", self.config.niche_libre) if t.strip()]
            if tokens:
                CATEGORIES_KEYWORDS["__perso__"] = [normalize_text(t) for t in tokens]
                if "__perso__" not in self.config.categories:
                    self.config.categories = self.config.categories + ["__perso__"]

        self.config.apply_country_defaults(country_targets)
        if not self.config.mots_cles:
            self.config.mots_cles = self.config.build_mots_cles(country_targets)

        self.checkpoint = Checkpoint(config.checkpoint_file)
        self.extractor = ProfileExtractor(config)
        self.driver: Optional[webdriver.Chrome] = None
        self.collector: Optional[UrlCollector] = None

        self._log_config()

    def _stop_demande(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def _log_config(self) -> None:
        max_str = f"{self.config.max_followers:,}" if self.config.max_followers > 0 else "illimité"
        cats_str = ", ".join(self.config.categories) if self.config.categories else "toutes (filet large)"
        log.info("=" * 65)
        log.info(f"  Pays         : {self.config.pays_code}")
        if self.config.ville:
            log.info(f"  Ville cible  : {self.config.ville}")
        if self.config.niche_libre:
            log.info(f"  Niche libre  : {self.config.niche_libre}")
        log.info(f"  Locale       : {self.config.tiktok_locale}")
        log.info(f"  Followers    : {self.config.min_followers:,} → {max_str}")
        log.info(f"  Catégories   : {cats_str}")
        log.info(f"  Hashtags     : {len(self.config.hashtags)} sources")
        log.info(f"  Mots-clés    : {len(self.config.mots_cles)} sources")
        log.info(f"  Max profils  : {self.config.max_profils}")
        log.info(f"  Sortie CSV   : {self.config.output_csv}")
        log.info("=" * 65)

    def compute_country_score(self, candidate: CreatorCandidate, meta: dict) -> int:
        target = self.country_targets.get(self.config.pays_code, {})
        strong = [normalize_text(x) for x in target.get("strong_signals", [])]
        local  = [normalize_text(x) for x in target.get("local_keywords", [])]

        text = normalize_text(" ".join([
            candidate.username,
            " ".join(candidate.source_terms),
            meta.get("bio", ""),
            meta.get("region", ""),
            meta.get("langue", ""),
        ]))

        score = 0
        if meta.get("region") == self.config.pays_code:
            score += 5
        if meta.get("langue") == self.config.langue_code:
            score += 1
        # Bonus fort si la ville ciblée apparaît dans le profil/sources
        if self.config.ville:
            ville_norm = normalize_text(self.config.ville)
            if ville_norm and ville_norm in text:
                score += 4
        for s in strong:
            if s and s in text:
                score += 3
        for s in local:
            if s and s in text:
                score += 1
        if any(t.startswith("#") for t in candidate.source_terms):
            score += 1
        if len(candidate.video_urls) >= 2:
            score += 1
        return score

    def _est_valide(self, inf: Influenceur) -> tuple[bool, str]:
        cfg = self.config
        if inf.score_pays < cfg.min_country_score:
            return False, f"Score pays={inf.score_pays} < {cfg.min_country_score}"
        if inf.followers < cfg.min_followers:
            return False, f"Followers {inf.followers:,} < {cfg.min_followers:,}"
        if cfg.max_followers > 0 and inf.followers > cfg.max_followers:
            return False, f"Followers {inf.followers:,} > {cfg.max_followers:,}"
        if cfg.categories and not inf.categories:
            return False, "Aucune catégorie cible détectée"
        return True, ""

    def _merge(self, base: dict, new: dict) -> None:
        for username, c in new.items():
            if username not in base:
                base[username] = c
            else:
                base[username].source_terms |= c.source_terms
                base[username].video_urls   |= c.video_urls

    def run(self) -> None:
        self.driver = DriverFactory.creer(self.config)
        self.collector = UrlCollector(self.driver, self.config)

        # Candidats déjà vus (pour ne pas retraiter entre sources)
        traites_global: set[str] = set()

        output = Path(self.config.output_csv)
        mode = "a" if output.exists() else "w"
        headers = list(Influenceur.__dataclass_fields__.keys())

        try:
            self.collector.initialiser()

            with open(output, mode, newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                if mode == "w":
                    writer.writeheader()

                # Générateur de sources : hashtags puis mots-clés
                sources: list[tuple[str, str]] = []
                if self.config.collect_from_hashtags:
                    sources += [("hashtag", h) for h in self.config.hashtags]
                if self.config.collect_from_search:
                    sources += [("search", m) for m in self.config.mots_cles]

                log.info(f"── Pipeline collecte+validation ({len(sources)} sources)")

                for src_type, src_value in sources:

                    # ── Arrêt demandé par l'utilisateur (UI)
                    if self._stop_demande():
                        log.info("  ⏹  Arrêt demandé par l'utilisateur — sauvegarde et sortie")
                        break

                    # ── Objectif déjà atteint → stop total
                    if self.checkpoint.nb_valides >= self.config.max_profils:
                        log.info(f"  🎯 Objectif atteint ({self.config.max_profils} profils) — arrêt")
                        break

                    # ── Collecte d'une source
                    if src_type == "hashtag":
                        nouveaux = self.collector.depuis_hashtag(src_value)
                    else:
                        nouveaux = self.collector.depuis_recherche(src_value)

                    # Filtrer les candidats déjà traités
                    a_valider = {
                        u: c for u, c in nouveaux.items()
                        if u not in traites_global
                        and not self.checkpoint.deja_traite(c.profile_url)
                    }

                    if not a_valider:
                        log.info(f"  Aucun nouveau candidat pour '{src_value}'")
                        delai_aleatoire(1, 3)
                        continue

                    log.info(f"  {len(a_valider)} nouveaux candidats → validation immédiate")

                    # ── Validation immédiate des nouveaux candidats
                    # Trier par richesse du signal (plus de sources = plus fiable)
                    tries = sorted(
                        a_valider.values(),
                        key=lambda c: len(c.source_terms) + len(c.video_urls),
                        reverse=True,
                    )

                    for candidate in tries:
                        traites_global.add(candidate.username)

                        if self._stop_demande():
                            log.info("  ⏹  Arrêt demandé par l'utilisateur")
                            break

                        if self.checkpoint.nb_valides >= self.config.max_profils:
                            log.info(f"  🎯 Objectif atteint ({self.config.max_profils} profils) — arrêt")
                            break

                        # Métadonnées rapides (requests, pas Selenium)
                        meta = self.extractor.extraire_metadata(candidate.username)
                        if not meta:
                            self.checkpoint.marquer(candidate.profile_url)
                            continue

                        # Score pays
                        score = self.compute_country_score(candidate, meta)
                        if score < self.config.min_country_score:
                            log.info(f"    ✗ @{candidate.username} score={score}")
                            self.checkpoint.marquer(candidate.profile_url)
                            continue

                        # Pré-filtre followers depuis l'API (sans ouvrir Selenium)
                        followers_api = meta.get("followers", 0)
                        if followers_api > 0:
                            if followers_api < self.config.min_followers:
                                log.info(f"    ✗ @{candidate.username} followers={followers_api:,} < {self.config.min_followers:,}")
                                self.checkpoint.marquer(candidate.profile_url)
                                continue
                            if self.config.max_followers > 0 and followers_api > self.config.max_followers:
                                log.info(f"    ✗ @{candidate.username} followers={followers_api:,} > {self.config.max_followers:,}")
                                self.checkpoint.marquer(candidate.profile_url)
                                continue

                        # Pré-filtre catégorie rapide sur username + sources (sans Selenium)
                        if self.config.categories:
                            sources_str = ",".join(sorted(candidate.source_terms))
                            pre_cats = detecter_categories(
                                self.config.categories,
                                username=candidate.username,
                                sources=sources_str,
                            )
                            if not pre_cats:
                                log.info(f"    ✗ @{candidate.username} catégorie non détectée (username+sources)")
                                self.checkpoint.marquer(candidate.profile_url)
                                continue

                        # Ouverture Selenium uniquement si le profil passe les pré-filtres
                        log.info(f"    → @{candidate.username} (score={score}, ~{followers_api:,} followers)")
                        inf = self.collector.construire_influenceur(
                            candidate=candidate,
                            meta=meta,
                            country_score=score,
                            pays_code=self.config.pays_code,
                            categories_cibles=self.config.categories,
                        )
                        if inf is None:
                            self.checkpoint.marquer(candidate.profile_url)
                            continue

                        valide, raison = self._est_valide(inf)
                        if not valide:
                            log.info(f"    ✗ {raison}")
                            self.checkpoint.marquer(candidate.profile_url)
                            continue

                        writer.writerow(inf.as_dict())
                        f.flush()
                        self.checkpoint.marquer(candidate.profile_url, valide=True)
                        log.info(
                            f"    ✓ [{self.checkpoint.nb_valides}/{self.config.max_profils}]"
                            f" @{inf.pseudo} | {inf.followers:,} followers | "
                            f"score={inf.score_pays} | cats={inf.categories or '—'}"
                        )
                        if self.progress_callback:
                            try:
                                self.progress_callback(
                                    self.checkpoint.nb_valides,
                                    self.config.max_profils,
                                    inf.as_dict(),
                                )
                            except Exception:
                                pass
                        delai_aleatoire(self.config.delai_min, self.config.delai_max)

                    delai_aleatoire(2, 4)

        except KeyboardInterrupt:
            log.info("Interruption — progression sauvegardée.")
        except WebDriverException as e:
            log.error(f"Erreur Selenium : {e}")
        finally:
            if self.driver:
                self.driver.quit()
            log.info(
                f"Terminé : {self.checkpoint.nb_valides} profils valides → {self.config.output_csv}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# MODE INTERACTIF (TUI sans dépendance externe)
# ─────────────────────────────────────────────────────────────────────────────
def _input_int(prompt: str, defaut: int) -> int:
    while True:
        val = input(f"  {prompt} [{defaut}] : ").strip()
        if not val:
            return defaut
        try:
            return int(val.replace(" ", "").replace(",", ""))
        except ValueError:
            print("    ⚠  Entrez un nombre entier.")


def _input_bool(prompt: str, defaut: bool) -> bool:
    d = "O" if defaut else "N"
    while True:
        val = input(f"  {prompt} [O/N, défaut={d}] : ").strip().upper()
        if not val:
            return defaut
        if val in ("O", "OUI", "Y", "YES", "1"):
            return True
        if val in ("N", "NON", "NO", "0"):
            return False
        print("    ⚠  Tapez O ou N.")


def _selectionner_pays(country_targets: dict) -> str:
    codes = list(country_targets.keys())
    print("\n" + "┌" + "─" * 62 + "┐")
    print("│{:^62}│".format("SÉLECTION DU PAYS"))
    print("├" + "─" * 4 + "┬" + "─" * 6 + "┬" + "─" * 8 + "┬" + "─" * 14 + "┬" + "─" * 26 + "┤")
    print("│  # │ Code │ Langue │   Hashtags   │ Locale                   │")
    print("├" + "─" * 4 + "┼" + "─" * 6 + "┼" + "─" * 8 + "┼" + "─" * 14 + "┼" + "─" * 26 + "┤")
    for i, code in enumerate(codes, 1):
        t = country_targets[code]
        nb_h = len(t.get("hashtags", []))
        nb_kw = len(t.get("local_keywords", []))
        total = nb_h + nb_kw
        print(
            f"│ {i:>2} │ {code:<4} │ {t.get('langue_code','?'):<6} │ "
            f"{total:>5} sources │ {t.get('tiktok_locale','?'):<24} │"
        )
    print("└" + "─" * 4 + "┴" + "─" * 6 + "┴" + "─" * 8 + "┴" + "─" * 14 + "┴" + "─" * 26 + "┘")

    while True:
        val = input(f"\n  Numéro du pays (1-{len(codes)}) : ").strip()
        try:
            idx = int(val) - 1
            if 0 <= idx < len(codes):
                choix = codes[idx]
                print(f"  ✓ Pays sélectionné : {choix}")
                return choix
        except ValueError:
            pass
        print(f"    ⚠  Entrez un nombre entre 1 et {len(codes)}.")


def _selectionner_categories() -> list[str]:
    print("\n" + "┌" + "─" * 60 + "┐")
    print("│{:^60}│".format("CATÉGORIES (optionnel)"))
    print("│{:^60}│".format("Laisser vide = filet large, tous créateurs acceptés"))
    print("├" + "─" * 4 + "┬" + "─" * 22 + "┬" + "─" * 30 + "┤")
    for i, cat in enumerate(CATEGORIES_DISPONIBLES, 1):
        kws = ", ".join(CATEGORIES_KEYWORDS[cat][:3])
        print(f"│ {i:>2} │ {cat:<20} │ {kws:<28} │")
    print("└" + "─" * 4 + "┴" + "─" * 22 + "┴" + "─" * 30 + "┘")
    print("\n  Numéros séparés par des espaces, ou Entrée pour tout accepter.")
    val = input("  Catégories : ").strip()

    if not val:
        print("  ✓ Mode filet large — aucun filtre catégorie")
        return []

    selected = []
    for token in val.split():
        try:
            idx = int(token) - 1
            if 0 <= idx < len(CATEGORIES_DISPONIBLES):
                selected.append(CATEGORIES_DISPONIBLES[idx])
        except ValueError:
            pass

    if selected:
        print(f"  ✓ Catégories sélectionnées : {', '.join(selected)}")
    else:
        print("  ✓ Mode filet large")
    return selected


def mode_interactif(country_targets: dict) -> Config:
    print("\n" + "═" * 63)
    print("  🎯  TikTok Influencer Scraper — Configuration interactive")
    print("═" * 63)

    config = Config()

    # Pays
    config.pays_code = _selectionner_pays(country_targets)

    # Followers
    print("\n── Filtres followers ─────────────────────────────────────────")
    config.min_followers = _input_int("Minimum followers", 5_000)
    config.max_followers = _input_int("Maximum followers (0 = illimité)", 200_000)

    # Nombre de profils
    print("\n── Objectif ──────────────────────────────────────────────────")
    config.max_profils = _input_int("Nombre de profils à collecter", 100)

    # Catégories
    config.categories = _selectionner_categories()

    # Sources
    print("\n── Sources ───────────────────────────────────────────────────")
    config.collect_from_hashtags = _input_bool("Collecter depuis les hashtags", True)
    config.collect_from_search   = _input_bool("Collecter depuis la recherche vidéo", True)

    # Résumé et confirmation
    max_str = f"{config.max_followers:,}" if config.max_followers > 0 else "illimité"
    cats_str = ", ".join(config.categories) if config.categories else "toutes (filet large)"
    print("\n" + "─" * 63)
    print("  Récapitulatif :")
    print(f"    Pays          : {config.pays_code}")
    print(f"    Followers     : {config.min_followers:,} → {max_str}")
    print(f"    Profils cible : {config.max_profils}")
    print(f"    Catégories    : {cats_str}")
    print(f"    Hashtags      : {'✓' if config.collect_from_hashtags else '✗'}")
    print(f"    Recherche     : {'✓' if config.collect_from_search else '✗'}")
    print("─" * 63)

    if not _input_bool("\n  Lancer le scraper", True):
        print("Annulé.")
        sys.exit(0)

    return config


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "TikTok Influencer Scraper\n\n"
            "Sans arguments → mode interactif\n\n"
            "Exemples :\n"
            "  python tiktok_scraper.py\n"
            "  python tiktok_scraper.py --pays CI --min 5000 --max 200000 --profils 100\n"
            "  python tiktok_scraper.py --pays FR --min 10000 --max 0 --profils 500\n"
            "  python tiktok_scraper.py --pays SN --categories cuisine mode\n"
            "  python tiktok_scraper.py --list-pays\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    p.add_argument("--list-pays",       action="store_true",  help="Lister les pays disponibles et quitter")
    p.add_argument("--list-categories", action="store_true",  help="Lister les catégories et quitter")

    p.add_argument("--config",          metavar="FICHIER",    help="Fichier YAML de config de base")
    p.add_argument("--countries-file",  metavar="FICHIER",    default="countries.yaml")

    p.add_argument("--pays",            metavar="CODE",       help="Code pays ISO-2 (CI, SN, FR, NG…)")
    p.add_argument("--langue",          metavar="CODE")
    p.add_argument("--locale",          metavar="LOCALE")

    p.add_argument("--min",             type=int, metavar="N", help="Minimum followers")
    p.add_argument("--max",             type=int, metavar="N", help="Maximum followers (0=illimité)")
    p.add_argument("--profils",         type=int, metavar="N", help="Nombre de profils cible")

    p.add_argument("--ville",           metavar="VILLE", help="Ville cible (ex: Paris) — concentre la recherche sur cette ville")
    p.add_argument("--niche-libre",     dest="niche_libre", metavar="NICHE", help="Niche personnalisée libre (ex: 'crypto memecoin')")

    p.add_argument("--categories",      nargs="+", metavar="CAT")
    p.add_argument("--mots-cles",       nargs="+", dest="mots_cles", metavar="MOT")
    p.add_argument("--hashtags",        nargs="+", metavar="TAG")

    p.add_argument("--output",          metavar="FICHIER.csv")
    p.add_argument("--min-score",       type=int, dest="min_score", metavar="N")

    p.add_argument("--from-hashtags",   dest="from_hashtags",
                   action=argparse.BooleanOptionalAction)
    p.add_argument("--from-search",     dest="from_search",
                   action=argparse.BooleanOptionalAction)

    return p.parse_args()


def afficher_pays(country_targets: dict) -> None:
    print("\nPays disponibles dans countries.yaml :")
    print("─" * 70)
    for code, t in country_targets.items():
        nb_h  = len(t.get("hashtags", []))
        nb_kw = len(t.get("local_keywords", []))
        nb_vp = len(t.get("viral_search_phrases", []))
        print(
            f"  {code:<6} | {t.get('langue_code','?'):<4} | "
            f"{t.get('tiktok_locale','?'):<12} | "
            f"{nb_h} hashtags | {nb_kw} keywords | {nb_vp} viral phrases"
        )
    print()


def afficher_categories() -> None:
    print("\nCatégories disponibles :")
    print("─" * 55)
    for cat in CATEGORIES_DISPONIBLES:
        kws = ", ".join(CATEGORIES_KEYWORDS[cat][:4])
        print(f"  {cat:<20} → {kws}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# POINT D'ENTRÉE
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = parse_args()
    country_targets = charger_pays(args.countries_file)

    if args.list_pays:
        afficher_pays(country_targets)
        sys.exit(0)

    if args.list_categories:
        afficher_categories()
        sys.exit(0)

    # Mode interactif si aucun paramètre de ciblage fourni
    mode_cli = any([
        getattr(args, "pays", None),
        getattr(args, "config", None),
        getattr(args, "min", None) is not None,
        getattr(args, "max", None) is not None,
        getattr(args, "profils", None),
    ])

    if not mode_cli:
        config = mode_interactif(country_targets)
    else:
        base = Config.from_yaml(args.config) if args.config else None
        config = Config.from_args(args, base=base)

    TikTokScraper(config, country_targets).run()