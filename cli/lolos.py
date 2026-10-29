#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lolos — désencombre une TV « Bbox 4K » de Bouygues Telecom (Android TV 11, Arcadyan)
par ADB et installe un écran d'accueil minimaliste.

Tout est réversible : on ne fait que DÉSACTIVER des applis (jamais de désinstallation,
sauf notre propre lanceur fr.accueil.tv lors d'une restauration) et chaque action est
notée dans ./journal.txt.

Python ≥ 3.8, bibliothèque standard uniquement. Windows, macOS, Linux.
Aide : python3 lolos.py --help
"""

import sys

if sys.version_info < (3, 8):
    sys.exit("lolos demande Python 3.8 ou plus récent (version actuelle : %s)." % sys.version.split()[0])

import argparse
import datetime
import errno
import hashlib
import ipaddress
import json
import os
import queue
import re
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

VERSION = "1.2.0"
DEPOT = "lxwiq/lolos-bbox-4k"  # dépôt GitHub (slug « propriétaire/projet ») pour les téléchargements
# Révision de fonds.zip : à augmenter quand les vidéos changent (caches plus anciens retéléchargés).
# 2 : forêt et nuages ralentis (1.2.0). Même valeur que VERSION_FONDS de l'installateur.
VERSION_FONDS = "2"

URL_RELEASE = "https://github.com/%s/releases/latest/download/" % DEPOT
URL_PROFIL = "https://raw.githubusercontent.com/%s/main/profil/bbox-4k.json" % DEPOT
URL_PLATFORM_TOOLS = "https://dl.google.com/android/repository/platform-tools-latest-%s.zip"
URL_PLATFORM_TOOLS_PAGE = "https://developer.android.com/tools/releases/platform-tools"

ICI = Path(__file__).resolve().parent
RACINE = ICI.parent
# Dossier personnel de l'outil (config, relais, adb téléchargé). LOLOS_DOSSIER permet de le déplacer.
DOSSIER_PERSO = Path(os.environ.get("LOLOS_DOSSIER") or (Path.home() / ".lolos")).expanduser()
FICHIER_CONFIG = DOSSIER_PERSO / "config.json"
FICHIER_RELAIS = DOSSIER_PERSO / "relais.pid"
CACHE = DOSSIER_PERSO / "cache"
JOURNAL = Path("journal.txt")              # relatif au dossier courant
DOSSIER_SAUVEGARDES = Path("sauvegardes")  # relatif au dossier courant

PORT_ADB = 5555
# Port local du relais macOS. Surchargeable (LOLOS_PORT_RELAIS) pour tester sans gêner un relais existant.
PORT_RELAIS = int(os.environ.get("LOLOS_PORT_RELAIS", "15555"))
MODELES_BBOX_SECOURS = ["Bouygtel4K"]

ETAPES_DEBOGAGE = """\
Pour activer le débogage ADB sur la TV (une seule fois) :
  1. Paramètres → Préférences de l'appareil → À propos
  2. Descendez sur « Build » et appuyez 7 fois sur OK (« Vous êtes maintenant développeur »)
  3. Retour → Préférences de l'appareil → Options pour les développeurs
  4. Activez « Débogage USB » (et « Débogage réseau » / « ADB sur le réseau » s'il existe)
  5. Lors de la première connexion, acceptez « Autoriser le débogage ? » sur la TV
     (cochez « Toujours autoriser sur cet ordinateur »)."""

AIDE_RESEAU_LOCAL_MACOS = (
    "macOS bloque l'accès au réseau local pour les programmes non signés par Apple\n"
    "(Python, adb de Homebrew…). Pour l'autoriser : Réglages Système → Confidentialité et\n"
    "sécurité → Réseau local → activez votre terminal. lolos contourne sinon le blocage\n"
    "avec /usr/bin/nc (Apple) et Bonjour (dns-sd).")


# --------------------------------------------------------------------------------------------
# Traduction des messages d'argparse (aide et erreurs en français)
# --------------------------------------------------------------------------------------------

_TRADUCTIONS = {
    "usage: ": "usage : ",
    "positional arguments": "arguments",
    "optional arguments": "options",
    "options": "options",
    "show this help message and exit": "afficher cette aide et quitter",
    "show program's version number and exit": "afficher la version et quitter",
    "%(prog)s: error: %(message)s\n": "%(prog)s : erreur : %(message)s\n",
    "the following arguments are required: %s": "arguments obligatoires manquants : %s",
    "unrecognized arguments: %s": "arguments non reconnus : %s",
    "invalid choice: %(value)r (choose from %(choices)s)": "choix invalide : %(value)r (possibles : %(choices)s)",
    "expected one argument": "une valeur est attendue",
    "argument %(argument_name)s: %(message)s": "argument %(argument_name)s : %(message)s",
    "invalid %(type)s value: %(value)r": "valeur invalide (%(type)s) : %(value)r",
    "not allowed with argument %s": "incompatible avec l'argument %s",
    "ambiguous option: %(option)s could match %(matches)s": "option ambiguë : %(option)s peut correspondre à %(matches)s",
}
argparse._ = lambda s: _TRADUCTIONS.get(s, s)


# --------------------------------------------------------------------------------------------
# Sorties, erreurs, journal
# --------------------------------------------------------------------------------------------

class Erreur(Exception):
    """Erreur attendue : affichée proprement, sans trace Python."""


SORTIE_JSON = False


def info(*morceaux):
    """Message pour l'humain (sur stderr en mode --json pour ne pas polluer le JSON)."""
    print(*morceaux, file=sys.stderr if SORTIE_JSON else sys.stdout, flush=True)


def avertir(message):
    print("⚠️  " + message, file=sys.stderr, flush=True)


def afficher_json(objet):
    print(json.dumps(objet, ensure_ascii=False, indent=2))


def journaliser(message):
    ligne = "%s %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message)
    try:
        with open(JOURNAL, "a", encoding="utf-8") as f:
            f.write(ligne)
    except OSError as e:
        avertir("journal.txt inaccessible (%s)" % e)


def terminal_interactif():
    return sys.stdin.isatty() and not SORTIE_JSON


def demander_oui_non(question, defaut):
    """Question o/n. Fin de l'entrée (pas de terminal) = réponse par défaut."""
    suffixe = " [O/n] " if defaut else " [o/N] "
    while True:
        try:
            print(question + suffixe, end="", file=sys.stderr if SORTIE_JSON else sys.stdout, flush=True)
            reponse = sys.stdin.readline()
        except (EOFError, OSError):
            return defaut
        if not reponse:  # fin de l'entrée
            print(file=sys.stderr if SORTIE_JSON else sys.stdout)
            return defaut
        reponse = reponse.strip().lower()
        if not reponse:
            return defaut
        if reponse in ("o", "oui", "y", "yes"):
            return True
        if reponse in ("n", "non", "no"):
            return False
        info("  Répondez o (oui) ou n (non).")


def confirmer(args, question):
    if getattr(args, "oui", False):
        return
    if not sys.stdin.isatty() or SORTIE_JSON:
        raise Erreur("Confirmation nécessaire : relancez avec --oui pour confirmer sans terminal.")
    if not demander_oui_non(question, False):
        raise Erreur("Annulé, rien n'a été modifié.")


def lister_ids(texte):
    return [x.strip() for x in (texte or "").replace(" ", ",").split(",") if x.strip()]


def lire_config():
    try:
        return json.loads(FICHIER_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def ecrire_config(config):
    DOSSIER_PERSO.mkdir(parents=True, exist_ok=True)
    temporaire = FICHIER_CONFIG.with_suffix(".tmp")
    temporaire.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(temporaire), str(FICHIER_CONFIG))


def valider_ip(ip):
    try:
        return str(ipaddress.IPv4Address(ip.strip()))
    except (ipaddress.AddressValueError, ValueError):
        raise Erreur("Adresse IP invalide : %r (exemple : 192.168.1.20)" % ip)


# --------------------------------------------------------------------------------------------
# Téléchargements
# --------------------------------------------------------------------------------------------

def telecharger(url, destination, description):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partiel = destination.with_name(destination.name + ".part")
    info("Téléchargement : %s…" % description)
    requete = urllib.request.Request(url, headers={"User-Agent": "lolos/" + VERSION})
    try:
        with urllib.request.urlopen(requete, timeout=60) as reponse, open(partiel, "wb") as f:
            total = int(reponse.headers.get("Content-Length") or 0)
            lu = 0
            while True:
                bloc = reponse.read(1 << 16)
                if not bloc:
                    break
                f.write(bloc)
                lu += len(bloc)
                if total and sys.stderr.isatty():
                    print("\r  %.1f / %.1f Mo" % (lu / 1e6, total / 1e6), end="", file=sys.stderr, flush=True)
        if total and sys.stderr.isatty():
            print(file=sys.stderr)
    except urllib.error.HTTPError as e:
        _effacer(partiel)
        raise Erreur("Téléchargement impossible (%s) : erreur HTTP %d." % (url, e.code))
    except (urllib.error.URLError, OSError, socket.timeout) as e:
        _effacer(partiel)
        conseil = ""
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            conseil = ("\nCertificats SSL absents : avec le Python de python.org sur macOS, lancez "
                       "« Install Certificates.command » (dossier Applications/Python 3.x).")
        raise Erreur("Téléchargement impossible (%s) : %s%s" % (url, getattr(e, "reason", e), conseil))
    os.replace(str(partiel), str(destination))
    return destination


def _effacer(chemin):
    try:
        Path(chemin).unlink()
    except OSError:
        pass


def extraire_zip(archive, dossier):
    """Extrait une archive en refusant les chemins qui sortiraient du dossier, en gardant les droits Unix."""
    dossier = Path(dossier).resolve()
    with zipfile.ZipFile(str(archive)) as z:
        for membre in z.infolist():
            cible = (dossier / membre.filename).resolve()
            if dossier != cible and dossier not in cible.parents:
                raise Erreur("Archive suspecte (chemin %r), extraction annulée." % membre.filename)
            z.extract(membre, str(dossier))
            droits = (membre.external_attr >> 16) & 0o777
            if droits and os.name != "nt" and not membre.is_dir():
                os.chmod(str(cible), droits)


# --------------------------------------------------------------------------------------------
# adb : recherche, installation automatique
# --------------------------------------------------------------------------------------------

def nom_executable_adb():
    return "adb.exe" if os.name == "nt" else "adb"


def trouver_adb(option=None):
    """Ordre : --adb, PATH, $ANDROID_HOME/platform-tools, ~/.lolos/platform-tools."""
    if option:
        chemin = Path(option).expanduser()
        if chemin.is_dir():
            chemin = chemin / nom_executable_adb()
        if chemin.is_file():
            return str(chemin)
        raise Erreur("adb introuvable à l'emplacement indiqué par --adb : %s" % option)
    trouve = shutil.which("adb")
    if trouve:
        return trouve
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        racine = os.environ.get(variable)
        if racine:
            candidat = Path(racine) / "platform-tools" / nom_executable_adb()
            if candidat.is_file():
                return str(candidat)
    candidat = DOSSIER_PERSO / "platform-tools" / nom_executable_adb()
    if candidat.is_file():
        return str(candidat)
    return None


def message_adb_absent():
    lignes = ["adb (Android Debug Bridge) est introuvable sur cet ordinateur.",
              "",
              "Installation manuelle :"]
    if sys.platform == "darwin":
        lignes.append("  macOS   : brew install --cask android-platform-tools")
    elif os.name == "nt":
        lignes.append("  Windows : winget install Google.PlatformTools")
        lignes.append("            (ou téléchargez le zip : %s)" % URL_PLATFORM_TOOLS_PAGE)
    else:
        lignes += ["  Debian / Ubuntu : sudo apt install adb",
                   "  Fedora          : sudo dnf install android-tools",
                   "  Arch            : sudo pacman -S android-tools"]
    lignes += ["",
               "Ou installation automatique (téléchargement officiel Google, ~7 Mo, sans droits",
               "administrateur, dans %s) :" % (DOSSIER_PERSO / "platform-tools"),
               "  python3 lolos.py installer-adb"]
    return "\n".join(lignes)


def installer_platform_tools():
    systeme = "windows" if os.name == "nt" else ("darwin" if sys.platform == "darwin" else "linux")
    archive = DOSSIER_PERSO / ("platform-tools-latest-%s.zip" % systeme)
    telecharger(URL_PLATFORM_TOOLS % systeme, archive, "adb (platform-tools Google pour %s)" % systeme)
    ancien = DOSSIER_PERSO / "platform-tools"
    if ancien.exists():
        shutil.rmtree(str(ancien))
    extraire_zip(archive, DOSSIER_PERSO)
    _effacer(archive)
    adb = ancien / nom_executable_adb()
    if not adb.is_file():
        raise Erreur("L'archive téléchargée ne contient pas %s." % adb.name)
    if os.name != "nt":
        for nom in ("adb", "fastboot", "sqlite3", "etc1tool", "hprof-conv", "make_f2fs", "mke2fs"):
            fichier = ancien / nom
            if fichier.is_file():
                os.chmod(str(fichier), 0o755)
    return str(adb)


def adb_requis(args):
    """Renvoie le chemin d'adb ; sinon explique comment l'installer et propose de le faire."""
    chemin = trouver_adb(getattr(args, "adb", None))
    if chemin:
        return chemin
    info(message_adb_absent())
    info("")
    if getattr(args, "oui", False):
        accepte = True
    elif terminal_interactif():
        accepte = demander_oui_non("Installer adb automatiquement maintenant ? (téléchargement officiel "
                                   "Google, ~7 Mo, sans droits administrateur)", True)
    else:
        raise Erreur("adb introuvable (relancez avec --oui pour l'installer automatiquement).")
    if not accepte:
        raise Erreur("adb est nécessaire pour continuer.")
    chemin = installer_platform_tools()
    info("✅ adb installé : %s" % chemin)
    return chemin


def version_adb(chemin):
    try:
        r = subprocess.run([chemin, "version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, timeout=10, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return "?"
    lignes = r.stdout.splitlines()
    m = re.search(r"version ([\d.]+)", lignes[0]) if lignes else None
    v = next((l.split(" ", 1)[1] for l in lignes if l.startswith("Version ")), "")
    return ("%s (%s)" % (m.group(1), v) if v else m.group(1)) if m else "?"


# --------------------------------------------------------------------------------------------
# Accès ADB à la TV
# --------------------------------------------------------------------------------------------

ERREURS_TRANSPORT = ("error: closed", "error: device offline", "error: no devices", "protocol fault",
                     "error: device unauthorized", "connection reset")


def erreur_transport(texte):
    """Vraie si adb a perdu la TV (et non si la commande lancée sur la TV a échoué)."""
    texte = texte.lower()
    return any(e in texte for e in ERREURS_TRANSPORT) or bool(re.search(r"error: device '.*' not found", texte))


class Resultat:
    def __init__(self, sortie, code, erreur):
        self.sortie, self.code, self.erreur = sortie, code, erreur


def adb_brut(chemin, arguments, timeout=60):
    try:
        r = subprocess.run([chemin] + list(arguments), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           stdin=subprocess.DEVNULL, timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise Erreur("adb ne répond pas (adb %s : délai de %d s dépassé)." % (" ".join(arguments[:2]), timeout))
    except OSError as e:
        raise Erreur("Impossible de lancer adb (%s) : %s" % (chemin, e))
    return Resultat(r.stdout.replace("\r", ""), r.returncode, r.stderr.replace("\r", "").strip())


class Adb:
    def __init__(self, chemin, serie):
        self.chemin, self.serie = chemin, serie
        self._identifiant = None

    def lancer(self, arguments, timeout=120):
        return adb_brut(self.chemin, ["-s", self.serie] + list(arguments), timeout)

    def shell(self, commande, timeout=120):
        """Commande shell sur la TV. Un « error: closed » passager (relais) est retenté une fois."""
        for essai in (1, 2):
            r = self.lancer(["shell", commande], timeout)
            if r.code == 0 or not erreur_transport(r.erreur):
                return r
            if essai == 1:
                if ":" in self.serie:
                    adb_brut(self.chemin, ["connect", self.serie], 20)
                time.sleep(1.5)
        raise Erreur("Connexion ADB perdue avec la TV (%s). Relancez : python3 lolos.py connecter" % r.erreur)

    def sh(self, *argv, **options):
        """Commande shell avec arguments protégés (espaces, guillemets…)."""
        return self.shell(" ".join(shlex.quote(str(a)) for a in argv), **options)

    def texte(self, commande, **options):
        return self.shell(commande, **options).sortie.strip()

    def plusieurs(self, commandes):
        """Plusieurs commandes en un seul aller-retour ADB. commandes = [(clé, commande), …]."""
        marque = "@@LOLOS@@"
        script = "; ".join("echo %s%s; { %s; } 2>/dev/null" % (marque, cle, cmd) for cle, cmd in commandes)
        resultats, cle = {}, None
        for ligne in self.shell(script).sortie.splitlines():
            if ligne.startswith(marque):
                cle = ligne[len(marque):]
                resultats[cle] = []
            elif cle is not None:
                resultats[cle].append(ligne)
        return {c: "\n".join(resultats.get(c, [])).strip() for c, _ in commandes}

    # ---- applis ----
    def paquets(self):
        r = self.plusieurs([("tous", "pm list packages"), ("desactives", "pm list packages -d"),
                            ("tiers", "pm list packages -3")])
        def ensemble(t):
            return set(l[8:].strip() for l in t.splitlines() if l.startswith("package:"))
        tous = ensemble(r["tous"])
        if not tous:
            raise Erreur("La TV n'a renvoyé aucune liste d'applis (pm list packages).")
        return tous, ensemble(r["desactives"]), ensemble(r["tiers"])

    def octets(self, commande, timeout=120):
        """Sortie binaire d'une commande sur la TV (exec-out : octets bruts). b"" en cas d'échec."""
        try:
            r = subprocess.run([self.chemin, "-s", self.serie, "exec-out", commande], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=timeout)
        except (subprocess.TimeoutExpired, OSError):
            return b""
        return r.stdout if r.returncode == 0 else b""

    def lanceables(self):
        """Applis de la liste d'applis de la TV : {paquet: (nom, identifiant de ressource du nom ou None ; APK)}.
        Les applis désactivées n'y figurent pas."""
        r = self.plusieurs([(c, "cmd package query-activities -a android.intent.action.MAIN "
                                "-c android.intent.category." + c) for c in ("LEANBACK_LAUNCHER", "LAUNCHER")])
        resultat = {}
        for bloc in (r["LEANBACK_LAUNCHER"] + "\n" + r["LAUNCHER"]).split("Activity #")[1:]:
            activite, _, appli = bloc.partition("ApplicationInfo:")
            pid = re.search(r"packageName=(\S+)", activite)
            if pid and pid.group(1) not in resultat:
                apk = re.search(r"sourceDir=(\S+)", appli)
                # Le nom de l'activité est celui affiché dans la liste d'applis, sinon celui de l'appli.
                resultat[pid.group(1)] = (libelle_brut(activite) or libelle_brut(appli), apk.group(1) if apk else None)
        return resultat

    def utilisation(self):
        """Temps passé dans chaque appli d'après les statistiques d'Android (plus longue période gardée) :
        {paquet: {"secondes", "derniere" (AAAA-MM-JJ hh:mm:ss), "lancements"}}."""
        resultat = {}
        for ligne in self.shell("dumpsys usagestats").sortie.splitlines():
            # package=org.exemple totalTimeUsed="6:59:48" lastTimeUsed="2026-10-27 22:21:26" … appLaunchCount=10
            ligne = ligne.strip()
            total = re.search(r'totalTimeUsed="([\d:]+)"', ligne)
            if not ligne.startswith("package=") or not total:
                continue
            secondes = 0
            for n in total.group(1).split(":"):
                secondes = secondes * 60 + int(n)
            derniere = re.search(r'lastTimeUsed="([^"]*)"', ligne)
            lancements = re.search(r"appLaunchCount=(\d+)", ligne)
            u = resultat.setdefault(ligne.split()[0][8:], {"secondes": 0, "derniere": "", "lancements": 0})
            u["secondes"] = max(u["secondes"], secondes)
            u["derniere"] = max(u["derniere"], derniere.group(1) if derniere else "")
            u["lancements"] = max(u["lancements"], int(lancements.group(1)) if lancements else 0)
        return {pid: u for pid, u in resultat.items() if u["secondes"] > 0}

    def accueil_actuel(self):
        lignes = self.texte("cmd package resolve-activity --brief -a android.intent.action.MAIN "
                            "-c android.intent.category.HOME").splitlines()
        return lignes[-1].strip() if lignes else ""

    def identifiant(self):
        """Empreinte anonyme de la TV (sert à associer sauvegardes et TV, sans stocker le n° de série)."""
        if self._identifiant is None:
            brut = self.texte("getprop ro.serialno") or self.texte("settings get secure android_id")
            self._identifiant = hashlib.sha256(("lolos:" + brut).encode()).hexdigest()[:16]
        return self._identifiant

    def capturer(self, fichier):
        # « adb exec-out screencap -p » est corrompu sur cette box (message de debug du pilote mêlé
        # au PNG) : on passe par un fichier sur la TV, qu'on rapatrie puis efface.
        fichier = Path(fichier)
        if fichier.parent and not fichier.parent.exists():
            fichier.parent.mkdir(parents=True, exist_ok=True)
        self.shell("screencap -p /sdcard/_cap.png")
        r = self.lancer(["pull", "/sdcard/_cap.png", str(fichier)], 60)
        self.shell("rm -f /sdcard/_cap.png")
        if r.code != 0:
            raise Erreur("Capture impossible : %s" % (r.erreur or r.sortie).strip())
        with open(str(fichier), "rb") as f:
            if f.read(8) != b"\x89PNG\r\n\x1a\n":
                raise Erreur("La capture reçue n'est pas une image PNG valide (%s)." % fichier)
        return fichier


def etats_adb(chemin):
    """{série: état} d'après « adb devices » (device, unauthorized, offline…)."""
    r = adb_brut(chemin, ["devices"], 20)
    etats = {}
    for ligne in r.sortie.splitlines()[1:]:
        morceaux = ligne.split()
        if len(morceaux) >= 2:
            etats[morceaux[0]] = morceaux[1]
    return etats


def attendre_appareil(chemin, serie, delai=60, delai_hors_ligne=10):
    """Attend l'état « device ». Si la TV demande l'autorisation, explique quoi faire et patiente."""
    debut, prevenu, etat = time.time(), False, None
    while time.time() - debut < delai:
        etat = etats_adb(chemin).get(serie)
        if etat == "device":
            return etat
        if etat in ("unauthorized", "authorizing") and not prevenu:
            info("📺 Sur la TV, une fenêtre « Autoriser le débogage ? » doit s'afficher :")
            info("   cochez « Toujours autoriser sur cet ordinateur » puis choisissez « OK ».")
            info("   En attente de votre accord (%d s max)…" % delai)
            prevenu = True
        if not prevenu and time.time() - debut > delai_hors_ligne:
            return etat
        time.sleep(1)
    return etat


def adresses_ip_de(chemin, serie):
    r = adb_brut(chemin, ["-s", serie, "shell", "ip -4 addr"], 20)
    return set(re.findall(r"inet (\d+\.\d+\.\d+\.\d+)/", r.sortie))


def port_ouvert_nc(ip, port, delai=2):
    """Test de port via /usr/bin/nc (binaire Apple, autorisé par la protection Réseau local de macOS)."""
    try:
        r = subprocess.run(["/usr/bin/nc", "-z", "-G", str(delai), ip, str(port)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=delai + 3)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def port_ouvert(ip, port, delai=2):
    """Test de port TCP. Renvoie (ouvert, errno)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(delai)
    try:
        s.connect((ip, port))
        return True, None
    except socket.timeout:
        return False, "timeout"
    except OSError as e:
        return False, e.errno
    finally:
        s.close()


def est_ehostunreach(code):
    return code in (errno.EHOSTUNREACH, 65, 113, 10065)


# ---- relais macOS ----
# macOS (« Réseau local ») interdit aux binaires non-Apple (adb de Homebrew, python3…) de joindre
# les IP du réseau local : « No route to host » (errno 65). /usr/bin/nc, signé Apple, y a droit.
# On fait donc écouter nc sur 127.0.0.1:15555 et relayer vers TV:5555, puis « adb connect 127.0.0.1:15555 ».

def relais_lu():
    try:
        r = json.loads(FICHIER_RELAIS.read_text(encoding="utf-8"))
        pid = int(r["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    try:  # le pid peut avoir été réutilisé par un autre programme
        commande = subprocess.run(["ps", "-p", str(pid), "-o", "command="], stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, encoding="utf-8", timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        commande = ""
    return r if "/usr/bin/nc" in commande else None


def arreter_relais():
    r = relais_lu()
    if r:
        try:
            os.killpg(int(r["pid"]), signal.SIGTERM)
        except OSError:
            pass
    _effacer(FICHIER_RELAIS)


def lancer_relais(ip):
    arreter_relais()
    script = ('d=$(mktemp -d); f="$d/f"; mkfifo "$f"; '
              'trap \'rm -rf "$d"\' EXIT; trap \'exit 0\' INT TERM HUP; '
              'while :; do /usr/bin/nc -l 127.0.0.1 %d < "$f" | /usr/bin/nc %s %d > "$f"; sleep 0.3; done'
              % (PORT_RELAIS, ip, PORT_ADB))
    processus = subprocess.Popen(["/bin/sh", "-c", script], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    DOSSIER_PERSO.mkdir(parents=True, exist_ok=True)
    FICHIER_RELAIS.write_text(json.dumps({"pid": processus.pid, "ip": ip, "port": PORT_RELAIS}), encoding="utf-8")
    time.sleep(0.6)


def connecter_via_relais(chemin, ip):
    serie = "127.0.0.1:%d" % PORT_RELAIS
    relais = relais_lu()
    if not (relais and relais.get("ip") == ip and relais.get("port") == PORT_RELAIS):
        if etats_adb(chemin).get(serie) == "device" and ip in adresses_ip_de(chemin, serie):
            return serie  # relais lancé par ailleurs, qui mène bien à cette TV : on le réutilise
        if not port_ouvert_nc(ip, PORT_ADB):
            raise Erreur("La TV %s ne répond pas sur le port ADB %d : débogage désactivé, TV éteinte ou "
                         "mauvaise adresse.\n\n%s" % (ip, PORT_ADB, ETAPES_DEBOGAGE))
        info("macOS bloque l'accès direct d'adb au réseau local : passage par un relais /usr/bin/nc "
             "(127.0.0.1:%d → %s:%d)." % (PORT_RELAIS, ip, PORT_ADB))
        lancer_relais(ip)
    elif etats_adb(chemin).get(serie) == "device":
        return serie
    for _ in range(5):
        sortie = adb_brut(chemin, ["connect", serie], 20).sortie.lower()
        if "connected to" in sortie:
            break
        time.sleep(1)
    etat = attendre_appareil(chemin, serie)
    if etat == "device":
        if ip not in adresses_ip_de(chemin, serie):
            raise Erreur("Le port %d est occupé par un relais qui mène à une autre TV. Arrêtez-le "
                         "(ou choisissez un autre port avec LOLOS_PORT_RELAIS)." % PORT_RELAIS)
        return serie
    if etat == "unauthorized":
        raise Erreur("La TV n'a pas autorisé cet ordinateur. Relancez « connecter » et acceptez la "
                     "fenêtre « Autoriser le débogage ? » sur la TV.")
    raise Erreur("Connexion via le relais impossible (état : %s).\n%s" % (etat or "aucun", AIDE_RESEAU_LOCAL_MACOS))


def connecter_ip(chemin, ip):
    ip = valider_ip(ip)
    serie = "%s:%d" % (ip, PORT_ADB)
    if etats_adb(chemin).get(serie) == "device":
        return serie
    relais = relais_lu() if sys.platform == "darwin" else None
    serie_relais = "127.0.0.1:%d" % PORT_RELAIS
    if relais and relais.get("ip") == ip and etats_adb(chemin).get(serie_relais) == "device":
        return serie_relais
    sortie = adb_brut(chemin, ["connect", serie], 30)
    texte = (sortie.sortie + " " + sortie.erreur).lower()
    if "connected to" in texte or "failed to authenticate" in texte:
        etat = attendre_appareil(chemin, serie)
        if etat == "device":
            return serie
        if etat in ("unauthorized", "authorizing"):
            raise Erreur("La TV n'a pas autorisé cet ordinateur (délai dépassé). Relancez « connecter » et "
                         "acceptez « Autoriser le débogage ? » sur la TV.")
        adb_brut(chemin, ["disconnect", serie], 10)
        if sys.platform != "darwin":
            raise Erreur("La TV %s accepte la connexion mais ne répond pas (état : %s). Redémarrez la TV puis "
                         "réessayez." % (ip, etat or "inconnu"))
    if "refused" in texte:
        raise Erreur("La TV %s refuse la connexion ADB : le débogage n'est pas activé.\n\n%s" % (ip, ETAPES_DEBOGAGE))
    if sys.platform == "darwin":
        return connecter_via_relais(chemin, ip)
    raise Erreur("Impossible de joindre la TV %s en ADB : %s\nVérifiez que la TV est allumée, sur le même "
                 "réseau, et que le débogage est actif.\n\n%s" % (ip, (sortie.sortie or sortie.erreur).strip(),
                                                                 ETAPES_DEBOGAGE))


def memoriser_connexion(ip, serie):
    config = lire_config()
    config["ip"], config["serie"] = ip, serie
    ecrire_config(config)


def appareil(args):
    """Connexion à la TV : --ip, sinon $ANDROID_SERIAL, sinon la TV mémorisée par « connecter »."""
    chemin = adb_requis(args)
    if getattr(args, "ip", None):
        ip = valider_ip(args.ip)
        serie = connecter_ip(chemin, ip)
        memoriser_connexion(ip, serie)
        return Adb(chemin, serie)
    etats = etats_adb(chemin)
    env = os.environ.get("ANDROID_SERIAL")
    if env and etats.get(env) == "device":
        return Adb(chemin, env)
    config = lire_config()
    if config.get("serie") and etats.get(config["serie"]) == "device":
        return Adb(chemin, config["serie"])
    if config.get("ip"):
        serie = connecter_ip(chemin, config["ip"])
        memoriser_connexion(config["ip"], serie)
        return Adb(chemin, serie)
    raise Erreur("Aucune TV configurée. Lancez d'abord :\n  python3 lolos.py chercher\n"
                 "  python3 lolos.py connecter <IP de la TV>")


# --------------------------------------------------------------------------------------------
# Profil
# --------------------------------------------------------------------------------------------

class Profil:
    def __init__(self, donnees, source):
        self.source = source
        self.donnees = donnees
        self.version = donnees.get("version")
        self.categories = donnees.get("categories", [])
        self.cat = {c["id"]: c for c in self.categories}
        self.paquets = donnees.get("paquets", [])
        self.par_id = {p["id"]: p for p in self.paquets}
        self.proteges = set(donnees.get("proteges", {}).get("paquets", []))
        self.reglages = donnees.get("reglages", [])
        self.reglages_sauvegardes = donnees.get("reglages_sauvegardes", [])
        self.accueil = donnees.get("accueil", {})
        self.avertissements = donnees.get("avertissements", [])
        self.categorie_decouverte = donnees.get("decouverte", {}).get("categorie", "autres")
        self.remplacements = donnees.get("remplacements", [])
        self.modeles = donnees.get("detection", {}).get("modeles") or MODELES_BBOX_SECOURS
        self.parent = {}
        for p in self.paquets:
            for lie in p.get("lies", []):
                self.parent[lie] = p["id"]

    def nom(self, pid):
        return self.par_id.get(pid, {}).get("nom", pid)

    def titre_categorie(self, cid):
        return self.cat.get(cid, {}).get("titre", cid)

    def visibles(self):
        return [p for p in self.paquets if not p.get("cache")]

    def ordonnes(self):
        """Paquets triés par catégorie (ordre du profil), les liés juste après leur parent."""
        rang = {c["id"]: i for i, c in enumerate(self.categories)}
        resultat = []
        for p in sorted(self.visibles(), key=lambda p: rang.get(p["categorie"], 99)):
            resultat.append(p)
            for lie in p.get("lies", []):
                if lie in self.par_id:
                    resultat.append(self.par_id[lie])
        vus = set(x["id"] for x in resultat)
        resultat += [p for p in self.paquets if p["id"] not in vus]
        return resultat


def charger_profil(option=None):
    candidats = [Path(option)] if option else [RACINE / "profil" / "bbox-4k.json", ICI / "bbox-4k.json",
                                               CACHE / "bbox-4k.json"]
    for chemin in candidats:
        if chemin.is_file():
            try:
                return Profil(json.loads(chemin.read_text(encoding="utf-8")), str(chemin))
            except ValueError as e:
                raise Erreur("Profil illisible (%s) : %s" % (chemin, e))
    if option:
        raise Erreur("Profil introuvable : %s" % option)
    chemin = telecharger(URL_PROFIL, CACHE / "bbox-4k.json", "profil Bbox 4K")
    return Profil(json.loads(chemin.read_text(encoding="utf-8")), str(chemin))


def est_bbox(profil, modele):
    return any(m.lower() in (modele or "").lower() for m in profil.modeles)


def avertir_si_pas_bbox(adb, profil):
    modele = adb.texte("getprop ro.product.model")
    if not est_bbox(profil, modele):
        avertir("Cet appareil (%s) n'est pas une Bbox 4K : le profil est prévu pour elle, les applis "
                "absentes seront ignorées." % (modele or "modèle inconnu"))
    return modele


# --------------------------------------------------------------------------------------------
# Sauvegardes
# --------------------------------------------------------------------------------------------

FICHIERS_SAUVEGARDE = [
    ("packages-tous.txt", "pm list packages -f"),  # avec le chemin de chaque APK
    ("packages-actives.txt", "pm list packages -e"),
    ("packages-desactives.txt", "pm list packages -d"),
    ("packages-systeme.txt", "pm list packages -s"),
    ("packages-tiers.txt", "pm list packages -3"),
    ("settings-global.txt", "settings list global"),
    ("settings-secure.txt", "settings list secure"),
    ("settings-system.txt", "settings list system"),
    ("home-par-defaut.txt", "cmd package resolve-activity --brief -a android.intent.action.MAIN "
                            "-c android.intent.category.HOME"),
    ("home-candidats.txt", "cmd package query-activities -a android.intent.action.MAIN "
                           "-c android.intent.category.HOME"),
    ("applis-lanceables-tv.txt", "cmd package query-activities -a android.intent.action.MAIN "
                                 "-c android.intent.category.LEANBACK_LAUNCHER"),
    ("preferred-activities.txt", "dumpsys package preferred"),
    ("deviceidle-whitelist.txt", "dumpsys deviceidle whitelist"),
    ("getprop.txt", "getprop"),
    ("dumpsys-package-complet.txt", "dumpsys package"),
]


def est_sauvegarde(dossier):
    return (dossier / "packages-desactives.txt").is_file() and (dossier / "settings-global.txt").is_file()


def infos_sauvegarde(dossier):
    try:
        return json.loads((dossier / "infos.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def date_sauvegarde(dossier):
    infos = infos_sauvegarde(dossier)
    if infos.get("date"):
        return infos["date"]
    horodatage = (dossier / "packages-desactives.txt").stat().st_mtime
    return datetime.datetime.fromtimestamp(horodatage).isoformat(timespec="seconds")


def lister_sauvegardes():
    """Sauvegardes du dossier ./sauvegardes et celles mémorisées dans ~/.lolos/config.json, de la plus récente à la plus ancienne."""
    trouvees = {}
    if DOSSIER_SAUVEGARDES.is_dir():
        for d in DOSSIER_SAUVEGARDES.iterdir():
            if d.is_dir() and est_sauvegarde(d):
                trouvees[str(d.resolve())] = d.resolve()
    for chemin in lire_config().get("sauvegardes", []):
        d = Path(chemin)
        if d.is_dir() and est_sauvegarde(d):
            trouvees[str(d.resolve())] = d.resolve()
    return sorted(trouvees.values(), key=date_sauvegarde, reverse=True)


def sauvegardes_de(adb):
    """Sauvegardes de cette TV (ou d'origine inconnue, ex. faites avec les anciens scripts)."""
    ident = adb.identifiant()
    return [d for d in lister_sauvegardes() if infos_sauvegarde(d).get("appareil") in (None, ident)]


def exiger_sauvegarde(adb, args):
    if getattr(args, "sans_sauvegarde", False):
        avertir("Aucune sauvegarde exigée (--sans-sauvegarde) : la restauration exacte sera impossible.")
        return None
    liste = sauvegardes_de(adb)
    if not liste:
        raise Erreur("Aucune sauvegarde de cette TV trouvée (dans ./sauvegardes ou ~/.lolos/config.json).\n"
                     "Faites-en une d'abord :  python3 lolos.py sauvegarder\n"
                     "(ou ajoutez --sans-sauvegarde, déconseillé).")
    return liste[0]


def lire_settings_sauvegarde(dossier, espace):
    valeurs = {}
    chemin = dossier / ("settings-%s.txt" % espace)
    if chemin.is_file():
        for ligne in chemin.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in ligne:
                cle, valeur = ligne.split("=", 1)
                valeurs[cle] = valeur
    return valeurs


def lire_paquets_sauvegarde(dossier, nom):
    chemin = dossier / nom
    if not chemin.is_file():
        return set()
    # « package:nom » ou, avec -f, « package:/chemin/base.apk=nom »
    return set(l[8:].strip().rsplit("=", 1)[-1] for l in chemin.read_text(encoding="utf-8", errors="replace").splitlines()
               if l.startswith("package:"))


def versions_depuis_dumpsys(dumpsys):
    lignes, vus, courant = [], set(), None
    for ligne in dumpsys.splitlines():
        m = re.match(r"^  Package \[([^\]]+)\]", ligne)
        if m:
            courant = m.group(1)
            continue
        m = re.match(r"^\s+versionName=(.*)$", ligne)
        if m and courant:
            if courant not in vus:
                lignes.append("%s     versionName=%s" % (courant, m.group(1).strip()))
                vus.add(courant)
            courant = None
    return "\n".join(lignes) + "\n"


# --------------------------------------------------------------------------------------------
# Commandes
# --------------------------------------------------------------------------------------------

def cmd_installer_adb(args):
    existant = trouver_adb(getattr(args, "adb", None))
    if existant and not args.forcer:
        info("adb est déjà disponible : %s (version %s)" % (existant, version_adb(existant)))
        info("(--forcer pour télécharger quand même la dernière version dans %s)" % (DOSSIER_PERSO / "platform-tools"))
        return 0
    chemin = installer_platform_tools()
    info("✅ adb installé : %s" % chemin)
    if sys.platform == "darwin":
        info("Note macOS : cet adb n'est pas signé Apple ; si l'accès au réseau local lui est refusé, "
             "lolos passera automatiquement par un relais /usr/bin/nc.")
    return 0


# ---- chercher ----

PORTS_TV = {8009: "Cast", 6466: "télécommande", 5555: "ADB"}


def reseau_a_sonder(option):
    if option:
        try:
            reseau = ipaddress.IPv4Network(option, strict=False)
        except ValueError:
            raise Erreur("Réseau invalide : %r (exemple : 192.168.1.0/24)" % option)
        if reseau.num_addresses > 1024:
            raise Erreur("Réseau trop grand (%d adresses) : 1024 au maximum." % reseau.num_addresses)
        return reseau
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # aucun paquet envoyé : sert juste à connaître l'IP locale
        ip_locale = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return ipaddress.IPv4Network(ip_locale + "/24", strict=False)


def nom_par_cast(ip):
    try:
        url = "http://%s:8008/setup/eureka_info?params=name,device_info" % ip
        with urllib.request.urlopen(url, timeout=1.5) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return d.get("name"), (d.get("device_info") or {}).get("model_name")
    except Exception:  # appareil non Cast, délai, JSON… : pas grave
        return None, None


def nom_par_dns(ip):
    try:
        return socket.gethostbyaddr(ip)[0]
    except (OSError, socket.herror):
        return None


def chercher_tcp(reseau):
    cibles = [(str(h), p) for h in reseau.hosts() for p in PORTS_TV]
    with ThreadPoolExecutor(max_workers=96) as pool:  # macOS : 256 descripteurs de fichiers par défaut
        resultats = list(pool.map(lambda c: (c, port_ouvert(c[0], c[1], 0.8)), cibles))
    hotes, injoignables = {}, 0
    for (ip, port), (ouvert, code) in resultats:
        if ouvert:
            hotes.setdefault(ip, set()).add(port)
        elif est_ehostunreach(code):
            injoignables += 1
    bloque = not hotes and injoignables >= 0.9 * len(cibles)

    def decrire(ip):
        nom, modele = nom_par_cast(ip) if 8009 in hotes[ip] else (None, None)
        return {"ip": ip, "nom": nom or nom_par_dns(ip) or "?", "modele": modele,
                "adb": PORT_ADB in hotes[ip], "ports": sorted(hotes[ip]), "source": "tcp"}
    with ThreadPoolExecutor(max_workers=16) as pool:
        tv = list(pool.map(decrire, sorted(hotes, key=lambda x: tuple(int(o) for o in x.split(".")))))
    return tv, bloque


def dnssd(arguments, duree=3.0, arret=None):
    """Lance dns-sd (qui ne s'arrête jamais seul), lit sa sortie pendant « duree » secondes puis le tue."""
    try:
        p = subprocess.Popen(["dns-sd"] + arguments, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
    except OSError:
        return []
    file_lignes = queue.Queue()

    def lire():
        for l in p.stdout:
            file_lignes.put(l.rstrip("\n"))
        file_lignes.put(None)
    threading.Thread(target=lire, daemon=True).start()
    lignes, fin = [], time.time() + duree
    while True:
        reste = fin - time.time()
        if reste <= 0:
            break
        try:
            ligne = file_lignes.get(timeout=reste)
        except queue.Empty:
            break
        if ligne is None:
            break
        lignes.append(ligne)
        if arret and arret(ligne):
            break
    p.kill()
    p.wait()
    return lignes


def chercher_bonjour():
    """Recherche Bonjour/mDNS via dns-sd (binaire Apple, non bloqué par la protection Réseau local)."""
    services = ["_androidtvremote2._tcp", "_googlecast._tcp"]
    instances = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for service, lignes in zip(services, pool.map(lambda s: dnssd(["-B", s, "local."], 3.0), services)):
            for ligne in lignes:
                m = ligne.split(None, 6)
                if len(m) == 7 and m[1] == "Add" and (service, m[6]) not in instances:
                    instances.append((service, m[6]))

    def resoudre(instance):
        service, nom = instance
        lignes = dnssd(["-L", nom, service, "local."], 3.0, lambda l: "can be reached at" in l)
        for l in lignes:
            m = re.search(r"can be reached at (\S+?)\.?:(\d+)", l)
            if m:
                hote = m.group(1)
                adresses = dnssd(["-G", "v4", hote], 3.0, lambda l: re.search(r"\s\d+\.\d+\.\d+\.\d+\s", l))
                for a in adresses:
                    ip = re.search(r"\s(\d+\.\d+\.\d+\.\d+)\s", a + " ")
                    if ip and " Add " in a:
                        return nom, ip.group(1)
        return nom, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        resolus = list(pool.map(resoudre, instances))
    tv = {}
    for nom, ip in resolus:
        if not ip:
            continue
        nom = re.sub(r"-[0-9a-f]{32}$", "", nom)  # les instances Cast finissent par un identifiant
        if ip not in tv:
            tv[ip] = {"ip": ip, "nom": nom, "modele": None, "adb": None, "ports": [], "source": "bonjour"}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for ip, ouvert in zip(list(tv), pool.map(lambda ip: port_ouvert_nc(ip, PORT_ADB), list(tv))):
            tv[ip]["adb"] = ouvert
    return list(tv.values())


def cmd_chercher(args):
    try:
        profil = charger_profil(getattr(args, "profil", None))
        modeles = profil.modeles
    except Erreur:
        modeles = MODELES_BBOX_SECOURS
    reseau = reseau_a_sonder(args.reseau)
    tv, bloque = [], False
    if reseau:
        info("Recherche des TV sur %s (ports 8009 Cast, 6466 télécommande, 5555 ADB)…" % reseau)
        tv, bloque = chercher_tcp(reseau)
    if not tv and sys.platform == "darwin":
        if bloque:
            info("macOS bloque l'accès de Python au réseau local (« No route to host ») :")
        info("recherche via Bonjour (dns-sd)…")
        tv = chercher_bonjour()
    for t in tv:
        t["bbox"] = any(m.lower() in ("%s %s" % (t["nom"], t.get("modele") or "")).lower() for m in modeles)
    tv.sort(key=lambda t: (not t["bbox"], not t["adb"]))
    if SORTIE_JSON:
        afficher_json({"tv": tv, "reseau": str(reseau) if reseau else None, "python_bloque": bloque})
        return 0 if tv else 1
    if not tv:
        info("Aucune TV trouvée. Vérifiez que la TV est allumée et sur le même réseau que cet ordinateur.")
        if sys.platform == "darwin":
            info(AIDE_RESEAU_LOCAL_MACOS)
        info("Vous pouvez aussi lire l'adresse IP sur la TV : Paramètres → Réseau et Internet → (votre réseau).")
        return 1
    info("TV trouvée(s) :")
    for t in tv:
        etiquette = " (Bbox 4K)" if t["bbox"] else ""
        adb = "ouvert ✅" if t["adb"] else "fermé ❌"
        info("  • %-28s %-15s ADB : %s" % (t["nom"] + etiquette, t["ip"], adb))
    meilleure = tv[0]
    if meilleure["adb"]:
        info("\nÉtape suivante :  python3 lolos.py connecter %s" % meilleure["ip"])
    else:
        info("\nLe port ADB de %s est fermé.\n%s" % (meilleure["ip"], ETAPES_DEBOGAGE))
        info("\nPuis :  python3 lolos.py connecter %s" % meilleure["ip"])
    return 0


# ---- connecter ----

def cmd_connecter(args):
    chemin = adb_requis(args)
    ip = args.adresse or getattr(args, "ip", None) or lire_config().get("ip")
    if not ip:
        raise Erreur("Indiquez l'adresse IP de la TV :  python3 lolos.py connecter 192.168.1.x\n"
                     "(trouvez-la avec :  python3 lolos.py chercher)")
    ip = valider_ip(ip)
    info("Connexion à %s…" % ip)
    serie = connecter_ip(chemin, ip)
    memoriser_connexion(ip, serie)
    adb = Adb(chemin, serie)
    modele = adb.texte("getprop ro.product.model")
    if SORTIE_JSON:
        afficher_json({"ip": ip, "serie": serie, "modele": modele, "relais": serie.startswith("127.0.0.1:")})
        return 0
    via = " via le relais macOS %s" % serie if serie.startswith("127.0.0.1:") else ""
    info("✅ Connecté à %s (%s)%s. Adresse mémorisée dans %s" % (modele or "la TV", ip, via, FICHIER_CONFIG))
    info("Étape suivante :  python3 lolos.py sauvegarder")
    return 0


# ---- etat ----

def analyser_eligibilite(brut):
    valeurs = {}
    for morceau in (brut or "").split(","):
        if "=" in morceau:
            k, v = morceau.split("=", 1)
            valeurs[k.strip()] = v.strip()
    ok = valeurs.get("HD") == "true" or valeurs.get("SD") == "true"
    return valeurs, ok


def lire_etat(adb, profil):
    paquet = shlex.quote(profil.accueil.get("paquet", "fr.accueil.tv"))
    r = adb.plusieurs([
        ("modele", "getprop ro.product.model"),
        ("fabricant", "getprop ro.product.manufacturer"),
        ("android", "getprop ro.build.version.release"),
        ("firmware", "getprop ro.build.version.incremental"),
        ("memoire", "grep -E '^(MemTotal|MemAvailable):' /proc/meminfo"),
        ("uptime", "cat /proc/uptime"),
        ("desactives", "pm list packages -d"),
        ("accueil", "cmd package resolve-activity --brief -a android.intent.action.MAIN "
                    "-c android.intent.category.HOME | tail -n 1"),
        ("eligibilite", "settings get secure current_eligibility"),
        ("dns_mode", "settings get global private_dns_mode"),
        ("dns_hote", "settings get global private_dns_specifier"),
        ("lanceur", "pm path %s" % paquet),
        ("lanceur_code", "dumpsys package %s | grep -m1 versionCode" % paquet),
        ("lanceur_nom", "dumpsys package %s | grep -m1 versionName" % paquet),
        ("veille_actif", "settings get global lowpower_control_enabled"),
        ("veille_delai", "settings get global lowpower_control_delay"),
    ])
    memoire = dict((m.group(1), int(m.group(2)) // 1024) for m in re.finditer(r"(\w+):\s+(\d+) kB", r["memoire"]))
    desactives = sorted(l[8:] for l in r["desactives"].splitlines() if l.startswith("package:"))
    eligibilite, tv_ok = analyser_eligibilite(r["eligibilite"] if r["eligibilite"] != "null" else "")
    dns_mode = r["dns_mode"] if r["dns_mode"] not in ("", "null") else None
    dns_hote = r["dns_hote"] if r["dns_hote"] not in ("", "null") else None
    try:
        uptime = int(float(r["uptime"].split()[0]))
    except (ValueError, IndexError):
        uptime = None
    lanceur = profil.accueil.get("activite", "fr.accueil.tv/.MainActivity")
    installe = r["lanceur"].startswith("package:")
    code = re.search(r"versionCode=(\d+)", r["lanceur_code"]) if installe else None
    nom_version = re.search(r"versionName=(\S+)", r["lanceur_nom"]) if installe else None
    veille_actif = r["veille_actif"] if r["veille_actif"] not in ("", "null") else None
    veille_delai = r["veille_delai"] if r["veille_delai"] not in ("", "null") else None
    return {
        "modele": r["modele"], "fabricant": r["fabricant"], "android": r["android"], "firmware": r["firmware"],
        "bbox": est_bbox(profil, r["modele"]),
        "memoire_totale_mo": memoire.get("MemTotal"), "memoire_libre_mo": memoire.get("MemAvailable"),
        "allumee_depuis_s": uptime,
        "nb_desactives": len(desactives), "desactives": desactives,
        "accueil": r["accueil"], "accueil_est_lanceur": r["accueil"] == lanceur,
        "lanceur_installe": installe,
        "lanceur_version_code": int(code.group(1)) if code else None,
        "lanceur_version_nom": nom_version.group(1) if nom_version else None,
        "veille_profonde": choix_veille(veille_actif, veille_delai),
        "veille_profonde_detail": {"lowpower_control_enabled": veille_actif, "lowpower_control_delay": veille_delai,
                                   "libelle": libelle_veille(veille_actif, veille_delai)},
        "eligibilite": r["eligibilite"], "eligibilite_detail": eligibilite, "tv_eligible": tv_ok,
        "dns_prive_mode": dns_mode, "dns_prive_hote": dns_hote, "dns_prive_ok": dns_mode != "hostname",
        "sml_desactive": "fr.bouyguestelecom.tv.sml" in desactives,
    }


def libelle_dns(mode, hote):
    if mode == "hostname":
        return "❌ DNS privé « %s » : à retirer (l'interface Bbox ne peut plus s'authentifier)" % (hote or "?")
    if mode == "off":
        return "désactivé ✅"
    if mode == "opportunistic":
        return "automatique ✅"
    return "non défini (automatique) ✅"


def cmd_etat(args):
    profil = charger_profil(getattr(args, "profil", None))
    adb = appareil(args)
    e = lire_etat(adb, profil)
    e["adb"] = {"chemin": adb.chemin, "version": version_adb(adb.chemin), "serie": adb.serie}
    if SORTIE_JSON:
        afficher_json(e)
        return 0
    def duree(s):
        if s is None:
            return "?"
        j, s = divmod(s, 86400)
        h, s = divmod(s, 3600)
        return ("%d j %d h" % (j, h)) if j else ("%d h %02d min" % (h, s // 60))
    lignes = [
        ("adb", "%s (version %s), connexion %s" % (e["adb"]["chemin"], e["adb"]["version"], adb.serie)),
        ("Modèle", "%s (%s), Android %s, firmware %s%s" % (e["modele"], e["fabricant"], e["android"], e["firmware"],
                                                         "" if e["bbox"] else "  ⚠️ pas une Bbox 4K")),
        ("Mémoire libre", "%s Mo sur %s Mo" % (e["memoire_libre_mo"], e["memoire_totale_mo"])),
        ("Allumée depuis", duree(e["allumee_depuis_s"])),
        ("Applis désactivées", str(e["nb_desactives"])),
        ("SML (SpyObject)", "désactivé ✅" if e["sml_desactive"] else "actif — à désactiver en premier"),
        ("Accueil actuel", e["accueil"] + ("  (lanceur Accueil)" if e["accueil_est_lanceur"] else "")),
        ("Lanceur Accueil", ("installé (versionCode %s%s)" % (e["lanceur_version_code"] or "?",
                                                            ", version %s" % e["lanceur_version_nom"]
                                                            if e["lanceur_version_nom"] else ""))
                            if e["lanceur_installe"] else "non installé"),
        ("Veille profonde", e["veille_profonde_detail"]["libelle"]),
        ("Éligibilité TV", ("%s %s" % ("✅" if e["tv_eligible"] else "❌", e["eligibilite"] or "inconnue"))),
        ("DNS privé", libelle_dns(e["dns_prive_mode"], e["dns_prive_hote"])),
    ]
    largeur = max(len(t) for t, _ in lignes)
    for titre, valeur in lignes:
        info("%s : %s" % (titre.ljust(largeur), valeur))
    return 0


# ---- applis de la TV hors profil, vrais noms, utilisation ----

FICHIER_NOMS = CACHE / "noms-applis.json"
FAVORI_SECONDES = 60  # au-delà, l'appli est marquée ♥ et n'est jamais désactivée d'office


def libelle_brut(texte):
    """« labelRes=0x7f080000 nonLocalizedLabel=null icon=… » → nom en clair, identifiant de ressource, ou None."""
    m = re.search(r"labelRes=0x([0-9a-fA-F]+) nonLocalizedLabel=(.*?) icon=", texte)
    if not m:
        return None
    if m.group(2) not in ("null", ""):
        return m.group(2)
    return int(m.group(1), 16) or None


def libelle_arsc(d, ref, profondeur=0):
    """Texte de la ressource chaîne ref (0xPPTTEEEE) lu dans un resources.arsc : en français si l'APK le
    contient, sinon la version par défaut. Même logique que app/src-tauri/src/arsc.rs."""
    def u16(o):
        return struct.unpack_from("<H", d, o)[0]

    def u32(o):
        return struct.unpack_from("<I", d, o)[0]

    try:
        if len(d) < 12 or u16(0) != 0x0002 or profondeur > 3:
            return None
        pid, tid, eid = ref >> 24, (ref >> 16) & 0xFF, ref & 0xFFFF
        pool, valeurs = None, []  # valeurs : (langue de la configuration, type de valeur, valeur)
        pos, fin = u16(2), min(len(d), u32(4))
        while pos + 8 <= fin:
            genre, entete, taille = u16(pos), u16(pos + 2), u32(pos + 4)
            if taille < 8 or pos + taille > fin:
                break
            if genre == 0x0001 and pool is None:
                pool = pos
            elif genre == 0x0200 and u32(pos + 8) == pid:
                sous = pos + entete
                while sous + 8 <= pos + taille:
                    g, h, t = u16(sous), u16(sous + 2), u32(sous + 4)
                    if t < 8:
                        break
                    if g == 0x0201 and d[sous + 8] == tid:
                        drapeaux, nombre, debut = d[sous + 9], u32(sous + 12), u32(sous + 16)
                        decalage = None
                        if drapeaux & 0x01:  # creux : paires (index, décalage / 4)
                            for i in range(nombre):
                                if u16(sous + h + 4 * i) == eid:
                                    decalage = u16(sous + h + 4 * i + 2) * 4
                                    break
                        elif eid < nombre:
                            if drapeaux & 0x02:
                                o = u16(sous + h + 2 * eid)
                                decalage = None if o == 0xFFFF else o * 4
                            else:
                                o = u32(sous + h + 4 * eid)
                                decalage = None if o == 0xFFFFFFFF else o
                        if decalage is not None:
                            e = sous + debut + decalage
                            drapeaux_entree, valeur = u16(e + 2), None
                            if drapeaux_entree & 0x08:  # entrée compacte
                                valeur = (drapeaux_entree >> 8, u32(e + 4))
                            elif not drapeaux_entree & 0x01:  # pas une entrée complexe
                                v = e + u16(e)
                                valeur = (d[v + 3], u32(v + 4))
                            if valeur and valeur[0] in (0x01, 0x03):
                                valeurs.append((bytes(d[sous + 28:sous + 30]),) + valeur)
                    sous += t
            pos += taille
        if not valeurs:
            return None
        rang = {b"fr": 0, b"\0\0": 1, b"en": 2}
        _, genre, valeur = min(valeurs, key=lambda v: rang.get(v[0], 3))
        if genre == 0x01:  # référence vers une autre ressource
            return libelle_arsc(d, valeur, profondeur + 1)
        if pool is None or valeur >= u32(pool + 8):
            return None
        o = pool + u32(pool + 20) + u32(pool + u16(pool + 2) + 4 * valeur)
        if u32(pool + 16) & 0x100:  # UTF-8 : longueur UTF-16 (ignorée) puis longueur en octets
            o += 2 if d[o] & 0x80 else 1
            n = d[o]
            o += 1
            if n & 0x80:
                n = ((n & 0x7F) << 8) | d[o]
                o += 1
            return d[o:o + n].decode("utf-8", "replace")
        n = u16(o)
        o += 2
        if n & 0x8000:
            n = ((n & 0x7FFF) << 16) | u16(o)
            o += 2
        return d[o:o + 2 * n].decode("utf-16-le", "replace")
    except (struct.error, IndexError):
        return None


def nom_appli(adb, pid, lanceable, cache):
    """Vrai nom de l'appli, lu dans le resources.arsc de son APK (extrait sur la TV) ; cache : {APK#ressource: nom}."""
    libelle, apk = lanceable or (None, None)
    if isinstance(libelle, str):
        return libelle
    if not libelle or not apk:
        return pid
    cle = "%s#%x" % (apk, libelle)  # le chemin de l'APK change à chaque mise à jour de l'appli
    if not cache.get(cle):  # un échec (relais lent, lecture vide) n'est pas mémorisé : on réessaiera
        cache[cle] = libelle_arsc(adb.octets("unzip -p %s resources.arsc" % shlex.quote(apk)), libelle)
    return (cache[cle] or "").strip() or pid


def inventaire(adb, profil, tiers):
    """Applis de la TV absentes du profil (liste d'applis de la TV + applis installées en plus, hors paquets
    protégés, Lol'OS et accueil actuel) avec leur vrai nom, et temps d'utilisation de toutes les applis.
    Renvoie ({paquet: {id, nom, categorie, systeme}}, {paquet: utilisation})."""
    lanceables = adb.lanceables()
    exclus = set(profil.par_id) | profil.proteges | {profil.accueil.get("paquet"), adb.accueil_actuel().split("/")[0]}
    try:
        anciens = json.loads(FICHIER_NOMS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        anciens = {}
    noms, trouvees = {}, {}
    for pid in sorted((set(lanceables) | set(tiers)) - exclus):
        libelle, apk = lanceables.get(pid) or (None, None)
        cle = "%s#%x" % (apk, libelle) if isinstance(libelle, int) and apk else None
        if anciens.get(cle):
            noms[cle] = anciens[cle]
        trouvees[pid] = {"id": pid, "nom": nom_appli(adb, pid, lanceables.get(pid), noms),
                         "categorie": profil.categorie_decouverte, "systeme": pid not in tiers}
    if noms != anciens:
        try:
            FICHIER_NOMS.parent.mkdir(parents=True, exist_ok=True)
            FICHIER_NOMS.write_text(json.dumps(noms, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
    # Seules les applis qu'on ouvre depuis la liste d'applis comptent (pas l'écran de veille, par exemple).
    return trouvees, {pid: u for pid, u in adb.utilisation().items() if pid in lanceables}


def est_favori(usage, pid):
    return usage.get(pid, {}).get("secondes", 0) >= FAVORI_SECONDES


def texte_utilisation(u):
    """« utilisée 7 h 05, la dernière fois le 27/10/2026 »"""
    h, m = u["secondes"] // 3600, int(round(u["secondes"] % 3600 / 60.0))
    duree = ("%d h%s" % (h, " %02d" % m if m else "")) if h else "%d min" % max(m, 1)
    date = u.get("derniere", "")[:10].split("-")
    quand = ", la dernière fois le %s/%s/%s" % (date[2], date[1], date[0]) if len(date) == 3 and date[0] != "1970" else ""
    return "utilisée %s%s" % (duree, quand)


# ---- lister ----

LIBELLES_ETAT = {"active": "active", "desactivee": "désactivée", "absente": "absente"}


def etat_paquet(pid, tous, desactives):
    if pid not in tous:
        return "absente"
    return "desactivee" if pid in desactives else "active"


def tableau(entetes, lignes):
    largeurs = [max(len(str(x)) for x in colonne) for colonne in zip(entetes, *lignes)]
    info("  ".join(str(h).ljust(l) for h, l in zip(entetes, largeurs)).rstrip())
    info("  ".join("-" * l for l in largeurs))
    for ligne in lignes:
        info("  ".join(str(c).ljust(l) for c, l in zip(ligne, largeurs)).rstrip())


def cmd_lister(args):
    profil = charger_profil(getattr(args, "profil", None))
    adb = appareil(args)
    tous, desactives, tiers = adb.paquets()
    trouvees, usage = inventaire(adb, profil, tiers)
    paquets = []
    for p in profil.ordonnes():
        etat = etat_paquet(p["id"], tous, desactives)
        if etat == "absente" and not args.tous:
            continue
        cat = profil.cat.get(p["categorie"], {})
        paquets.append({
            "id": p["id"], "nom": p["nom"], "categorie": p["categorie"], "categorie_titre": cat.get("titre"),
            "demander": bool(cat.get("demander")), "etat": etat, "defaut": p.get("defaut"),
            "cache": bool(p.get("cache")), "lie_a": profil.parent.get(p["id"]), "lies": p.get("lies", []),
            "priorite": p.get("priorite"), "note": p.get("note"), "trouvee": False,
            "favori": est_favori(usage, p["id"]), "utilisation": usage.get(p["id"]),
        })
    for t in sorted(trouvees.values(), key=lambda t: t["nom"].lower()):
        cat = profil.cat.get(t["categorie"], {})
        paquets.append({
            "id": t["id"], "nom": t["nom"], "categorie": t["categorie"], "categorie_titre": cat.get("titre"),
            "demander": bool(cat.get("demander")), "etat": etat_paquet(t["id"], tous, desactives), "defaut": "garder",
            "cache": False, "lie_a": None, "lies": [], "priorite": None, "note": None, "trouvee": True,
            "systeme": t["systeme"], "favori": est_favori(usage, t["id"]), "utilisation": usage.get(t["id"]),
        })
    # Les applis trouvées rejoignent leur catégorie (tri stable : l'ordre du profil est gardé).
    rang = {c["id"]: i for i, c in enumerate(profil.categories)}
    paquets.sort(key=lambda p: rang.get(p["categorie"], 99))
    hors_profil = sorted(desactives - set(profil.par_id) - set(trouvees))
    if SORTIE_JSON:
        afficher_json({"paquets": paquets, "hors_profil_desactives": hors_profil,
                       "categories": profil.categories, "proteges": sorted(profil.proteges),
                       "remplacements": [dict(r, installe=r["paquet"] in tous) for r in profil.remplacements
                                         if r["remplace"] in tous]})
        return 0
    lignes = []
    for p in paquets:
        nom = ("  ↳ " + p["nom"]) if p["lie_a"] else p["nom"]
        if p["favori"]:
            nom += " ♥"
        lignes.append([nom, p["categorie_titre"] or p["categorie"], LIBELLES_ETAT[p["etat"]],
                       {"desactiver": "désactiver", "garder": "garder"}.get(p["defaut"], p["defaut"]), p["id"]])
    tableau(["Appli", "Catégorie", "État", "Par défaut", "Paquet"], lignes)
    n_des = sum(1 for p in paquets if p["etat"] == "desactivee")
    info("\n%d applis sur cette TV, dont %d désactivées et %d trouvées hors profil (catégorie « %s »)."
         % (len(paquets), n_des, len(trouvees), profil.titre_categorie(profil.categorie_decouverte)))
    if hors_profil:
        info("Autres applis désactivées (hors profil) : %s" % ", ".join(hors_profil))
    info("↳ = appli liée, suit automatiquement l'appli au-dessus.")
    info("♥ = vous l'utilisez (statistiques de la TV) : jamais désactivée d'office.")
    for r in profil.remplacements:
        if r["remplace"] in tous and r["paquet"] not in tous:
            info("Proposé : %s à la place de %s →  python3 lolos.py remplacer %s"
                 % (r["nom"], profil.nom(r["remplace"]), r["nom"].lower()))
    return 0


# ---- appliquer ----

def questions_interactives(profil, tous, desactives, trouvees, usage):
    choix = {}
    info("Pour chaque appli, répondez o si vous l'utilisez (elle reste active), n sinon (elle est désactivée).")
    info("Rien n'est appliqué avant la confirmation finale.")
    for cat in profil.categories:
        if not cat.get("demander"):
            continue
        paquets = [p for p in profil.visibles() if p["categorie"] == cat["id"] and p["id"] in tous]
        paquets += sorted((t for t in trouvees.values() if t["categorie"] == cat["id"]), key=lambda t: t["nom"].lower())
        if not paquets:
            continue
        info("\n== %s ==" % cat["titre"])
        if cat.get("description"):
            info("   " + cat["description"])
        for p in paquets:
            deja = p["id"] in desactives
            favori = est_favori(usage, p["id"])
            defaut = False if deja else favori or p.get("defaut") != "desactiver"
            if p.get("note"):
                info("   Note : %s" % p["note"])
            garder = demander_oui_non("  Utilisez-vous %s ?%s%s" % (
                p["nom"], " ♥ (%s)" % texte_utilisation(usage[p["id"]]) if favori else "",
                " (actuellement désactivée)" if deja else ""), defaut)
            choix[p["id"]] = "activer" if garder else "desactiver"
    return choix


def calculer_plan(profil, args, tous, desactives, trouvees, usage, choix_interactifs, valeurs_reglages):
    """Renvoie (actions, conservees, infos). Chaque action : dict(type, id, nom, …).
    trouvees : applis de la TV hors profil (inventaire) ; usage : temps d'utilisation par appli."""
    choix, raisons, utilisees = {}, {}, []
    for p in profil.visibles():
        cat = profil.cat.get(p["categorie"], {})
        if not cat.get("demander") or args.defauts:
            desactiver = p.get("defaut") == "desactiver"
            # Une appli que vous utilisez n'est pas désactivée d'office (sauf télémétrie et pubs).
            if desactiver and cat.get("demander") and est_favori(usage, p["id"]) and p["id"] in tous:
                desactiver = False
                utilisees.append(p["id"])
            choix[p["id"]] = "desactiver" if desactiver else "laisser"
            raisons[p["id"]] = "profil"
        else:
            choix[p["id"]] = "laisser"
    choix.update(choix_interactifs)
    brut_desactiver, brut_garder = lister_ids(args.desactiver), lister_ids(args.garder)
    conflit = set(brut_desactiver) & set(brut_garder)
    if conflit:
        raise Erreur("À la fois dans --desactiver et --garder : %s" % ", ".join(sorted(conflit)))
    # Les catégories d'abord, puis les paquets nommés : « --desactiver adulte --garder com.bbox.vixen » marche.
    explicites = {}
    for ids, action in ((brut_desactiver, "desactiver"), (brut_garder, "activer")):
        for i in ids:
            if i in profil.cat:
                for p in list(profil.visibles()) + list(trouvees.values()):
                    if p["categorie"] == i:
                        explicites[p["id"]] = action
    for ids, action in ((brut_desactiver, "desactiver"), (brut_garder, "activer")):
        for i in ids:
            if i not in profil.cat:
                explicites[i] = action
    hors_profil = []
    for pid, action in list(explicites.items()):
        if action == "desactiver" and pid in profil.proteges:
            avertir("%s est protégé (TV, guide, DRM, mises à jour…) : ignoré." % pid)
            del explicites[pid]
            continue
        if pid not in profil.par_id:
            if pid not in tous:
                raise Erreur("Paquet inconnu du profil et absent de la TV : %s" % pid)
            if action == "desactiver":
                if pid not in trouvees:
                    raise Erreur("%s ne fait partie ni du profil ni de la liste d'applis de la TV : refusé par "
                                 "prudence." % pid)
                avertir("%s (%s) ne fait pas partie du profil : elle sera désactivée." % (trouvees[pid]["nom"], pid))
                hors_profil.append(pid)
    choix.update(explicites)
    # Les applis liées (module Netflix, webapp Canal+…) suivent leur appli principale.
    for p in profil.paquets:
        for lie in p.get("lies", []):
            if p["id"] in choix and lie not in explicites:
                choix[lie] = choix[p["id"]]
    # Les paquets protégés ne sont jamais touchés, même si le profil était mal écrit.
    for pid in list(choix):
        if pid in profil.proteges and choix[pid] == "desactiver":
            avertir("%s est protégé : ignoré." % pid)
            choix[pid] = "laisser"

    ordre = {p["id"]: i for i, p in enumerate(profil.paquets)}
    # SML d'abord (priorite 0) : son « SpyObject » réactive toute appli TV désactivée tant qu'il tourne.
    cles = sorted(choix, key=lambda pid: (profil.par_id.get(pid, {}).get("priorite", 1), ordre.get(pid, 10 ** 6)))
    actions, conservees, absentes, deja = [], [], [], []
    for pid in cles:
        action, nom = choix[pid], trouvees[pid]["nom"] if pid in trouvees else profil.nom(pid)
        if pid not in tous:
            if action != "laisser":
                absentes.append(pid)
            continue
        if action == "desactiver":
            if pid in desactives:
                deja.append(pid)
            else:
                actions.append({"type": "desactiver", "id": pid, "nom": nom,
                                "priorite": profil.par_id.get(pid, {}).get("priorite") == 0})
        elif action == "activer":
            if pid in desactives:
                actions.append({"type": "activer", "id": pid, "nom": nom})
            else:
                conservees.append(pid)
        elif pid in profil.par_id and not profil.par_id[pid].get("cache"):
            if pid not in desactives:
                conservees.append(pid)
    if choix.get("fr.bouyguestelecom.tv.sml") in ("laisser", "activer") and "fr.bouyguestelecom.tv.sml" in tous \
            and "fr.bouyguestelecom.tv.sml" not in desactives:
        avertir("SML reste actif : son « SpyObject » risque de réactiver les applis désactivées.")
    for r in profil.reglages:
        actuel = valeurs_reglages.get("%s/%s" % (r["ns"], r["cle"]))
        if actuel != r["valeur"]:
            actions.append({"type": "reglage", "id": "%s/%s" % (r["ns"], r["cle"]), "ns": r["ns"], "cle": r["cle"],
                            "valeur": r["valeur"], "avant": actuel, "nom": r.get("pourquoi", "")})
    return actions, conservees, {"absentes": absentes, "deja_desactivees": deja, "hors_profil": hors_profil,
                                 "gardees_car_utilisees": utilisees}


def decrire_action(a):
    if a["type"] == "desactiver":
        return "Désactiver  %s (%s)%s" % (a["nom"], a["id"], "  ← en premier (SpyObject)" if a.get("priorite") else "")
    if a["type"] == "activer":
        return "Réactiver   %s (%s)" % (a["nom"], a["id"])
    if a["type"] == "reglage":
        return "Réglage     %s %s : %s → %s (%s)" % (a["ns"], a["cle"], a["avant"] or "non défini",
                                                    a["valeur"] if a["valeur"] is not None else "non défini", a["nom"])
    if a["type"] == "desinstaller":
        return "Désinstaller %s (%s)" % (a["nom"], a["id"])
    if a["type"] == "accueil":
        return "Accueil     %s" % a["id"]
    return str(a)


def executer_action(adb, a):
    """Exécute une action, la note dans journal.txt, renvoie (ok, détail)."""
    if a["type"] == "desactiver":
        adb.sh("am", "force-stop", a["id"])
        journaliser("adb shell am force-stop %s" % a["id"])
        r = adb.sh("pm", "disable-user", "--user", "0", a["id"])
        journaliser("adb shell pm disable-user --user 0 %s" % a["id"])
        sortie = (r.sortie + r.erreur).strip()
        return "disabled-user" in sortie, sortie
    if a["type"] == "activer":
        r = adb.sh("pm", "enable", a["id"])
        journaliser("adb shell pm enable %s" % a["id"])
        sortie = (r.sortie + r.erreur).strip()
        return "enabled" in sortie and "disabled" not in sortie, sortie
    if a["type"] == "reglage":
        if a.get("valeur") is None:
            r = adb.sh("settings", "delete", a["ns"], a["cle"])
            journaliser("adb shell settings delete %s %s" % (a["ns"], a["cle"]))
        else:
            r = adb.sh("settings", "put", a["ns"], a["cle"], a["valeur"])
            journaliser("adb shell settings put %s %s %s" % (a["ns"], a["cle"], shlex.quote(a["valeur"])))
        return r.code == 0, (r.sortie + r.erreur).strip()
    if a["type"] == "desinstaller":
        r = adb.lancer(["uninstall", a["id"]], 120)
        journaliser("adb uninstall %s" % a["id"])
        return "Success" in r.sortie, (r.sortie + r.erreur).strip()
    if a["type"] == "accueil":
        r = adb.sh("cmd", "package", "set-home-activity", a["id"])
        journaliser("adb shell cmd package set-home-activity %s" % a["id"])
        return r.code == 0 and "error" not in r.sortie.lower(), (r.sortie + r.erreur).strip()
    raise Erreur("Action inconnue : %r" % a)


def executer_plan(adb, actions):
    resultats = []
    for i, a in enumerate(actions, 1):
        ok, detail = executer_action(adb, a)
        resultats.append(dict(a, ok=ok, detail=detail))
        info("  %s %d/%d %s%s" % ("✅" if ok else "❌", i, len(actions), decrire_action(a),
                                  "" if ok else "  → " + (detail or "échec")))
    return resultats


def lire_reglages(adb, cles):
    if not cles:
        return {}
    r = adb.plusieurs([(c, "settings get %s %s" % (shlex.quote(c.split("/")[0]), shlex.quote(c.split("/", 1)[1])))
                       for c in cles])
    return {c: (None if v in ("null", "") else v) for c, v in r.items()}


def memoriser_desactivations(adb, resultats):
    """Mémorise ce que lolos a désactivé sur cette TV (sert à « verifier »)."""
    config = lire_config()
    ident = adb.identifiant()
    memo = set(config.setdefault("desactives_par_lolos", {}).get(ident, []))
    memo |= set(a["id"] for a in resultats if a["type"] == "desactiver" and a["ok"])
    memo -= set(a["id"] for a in resultats if a["type"] == "activer" and a["ok"])
    config["desactives_par_lolos"][ident] = sorted(memo)
    ecrire_config(config)


def cmd_appliquer(args):
    profil = charger_profil(getattr(args, "profil", None))
    adb = appareil(args)
    avertir_si_pas_bbox(adb, profil)
    tous, desactives, tiers = adb.paquets()
    trouvees, usage = inventaire(adb, profil, tiers)
    sauvegarde = None
    if not args.simulation:
        sauvegarde = exiger_sauvegarde(adb, args)
    choix_interactifs = {}
    if args.interactif:
        if SORTIE_JSON:
            raise Erreur("--interactif et --json sont incompatibles.")
        choix_interactifs = questions_interactives(profil, tous, desactives, trouvees, usage)
    valeurs = lire_reglages(adb, ["%s/%s" % (r["ns"], r["cle"]) for r in profil.reglages])
    actions, conservees, details = calculer_plan(profil, args, tous, desactives, trouvees, usage, choix_interactifs,
                                                 valeurs)

    def nom(pid):
        return trouvees[pid]["nom"] if pid in trouvees else profil.nom(pid)

    if not SORTIE_JSON:
        info("\nPlan%s :" % (" (simulation, rien ne sera modifié)" if args.simulation else ""))
        if not actions:
            info("  Rien à faire : la TV est déjà dans l'état demandé.")
        for i, a in enumerate(actions, 1):
            info("  %2d. %s" % (i, decrire_action(a)))
        if conservees:
            info("\nRestent actives : %s" % ", ".join(nom(p) for p in conservees))
        if details["gardees_car_utilisees"]:
            info("Gardées car vous les utilisez ♥ : %s" % ", ".join(nom(p) for p in details["gardees_car_utilisees"]))
        if details["deja_desactivees"]:
            info("Déjà désactivées : %d" % len(details["deja_desactivees"]))
        if details["absentes"]:
            info("Absentes de cette TV (ignorées) : %s" % ", ".join(details["absentes"]))
        if sauvegarde:
            info("Sauvegarde de référence : %s" % sauvegarde)
        elif args.simulation and not sauvegardes_de(adb):
            info("⚠️  Aucune sauvegarde de cette TV : lancez « sauvegarder » avant d'appliquer pour de vrai.")

    if args.simulation or not actions:
        if SORTIE_JSON:
            afficher_json({"simulation": args.simulation, "actions": actions, "restent_actives": conservees,
                           "details": details, "sauvegarde": str(sauvegarde) if sauvegarde else None})
        return 0
    confirmer(args, "\nAppliquer ces %d changements ?" % len(actions))
    info("\nApplication…")
    journaliser("== lolos appliquer (%d actions) sur %s" % (len(actions), adb.serie))
    resultats = executer_plan(adb, actions)

    # Contrôle : une appli réactivée toute seule trahirait un SpyObject encore actif.
    time.sleep(3)
    _, desactives_apres, _ = adb.paquets()
    reactivees = [a["id"] for a in resultats if a["type"] == "desactiver" and a["ok"] and a["id"] not in desactives_apres]
    if reactivees:
        avertir("Réactivées toutes seules juste après : %s (SML/SpyObject encore actif ? Redémarrez la TV puis "
                "relancez « appliquer »)." % ", ".join(reactivees))
    memoriser_desactivations(adb, resultats)

    echecs = [a for a in resultats if not a["ok"]]
    if SORTIE_JSON:
        afficher_json({"simulation": False, "resultats": resultats, "reactivees": reactivees,
                       "nb_desactives": len(desactives_apres)})
    else:
        info("\nTerminé : %d actions, %d échecs. %d applis désactivées sur la TV. Journal : %s"
             % (len(resultats), len(echecs), len(desactives_apres), JOURNAL.resolve()))
        for a in profil.avertissements:
            info("• " + a)
        info("Annuler à tout moment :  python3 lolos.py restaurer")
    return 1 if echecs else 0


# ---- remplacer (ex. YouTube → SmartTube) ----

def apk_github(depot, prefixe, abi):
    """APK de la dernière version publiée sur GitHub pour ce processeur : (version, nom du fichier, URL)."""
    url = "https://api.github.com/repos/%s/releases/latest" % depot
    requete = urllib.request.Request(url, headers={"User-Agent": "lolos/" + VERSION,
                                                   "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(requete, timeout=30) as reponse:
            version = json.loads(reponse.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Erreur("Impossible de lire la dernière version de %s sur GitHub : %s" % (depot, getattr(e, "reason", e)))
    fichiers = version.get("assets") or []
    for suffixe in ("_%s.apk" % abi, "_universal.apk"):
        for f in fichiers:
            if f.get("name", "").startswith(prefixe) and f["name"].endswith(suffixe):
                return version.get("tag_name", ""), f["name"], f["browser_download_url"]
    raise Erreur("Pas d'APK %s… pour ce processeur (%s) dans la dernière version de %s." % (prefixe, abi, depot))


def trouver_remplacement(profil, quoi):
    q = quoi.strip().lower()
    for r in profil.remplacements:
        if q in (r["paquet"].lower(), r["nom"].lower(), r["remplace"].lower(), profil.nom(r["remplace"]).lower()):
            return r
    raise Erreur("Remplacement inconnu : %s. Disponibles : %s" % (quoi, ", ".join(
        "%s (à la place de %s)" % (r["nom"], profil.nom(r["remplace"])) for r in profil.remplacements) or "aucun"))


def cmd_remplacer(args):
    profil = charger_profil(getattr(args, "profil", None))
    adb = appareil(args)
    tous, desactives, _ = adb.paquets()
    if not args.quoi:
        liste = [{"paquet": r["paquet"], "nom": r["nom"], "remplace": r["remplace"], "remplace_nom": profil.nom(r["remplace"]),
                  "installe": r["paquet"] in tous, "remplace_present": r["remplace"] in tous,
                  "remplace_desactive": r["remplace"] in desactives, "description": r.get("description"),
                  "avertissement": r.get("avertissement")} for r in profil.remplacements]
        if SORTIE_JSON:
            afficher_json({"remplacements": liste})
            return 0
        for r in liste:
            info("%s à la place de %s — %s" % (r["nom"], r["remplace_nom"], "déjà installé" if r["installe"] else "non installé"))
            info("   " + (r["description"] or ""))
        info("\nPour remplacer :  python3 lolos.py remplacer %s" % (liste[0]["nom"].lower() if liste else "NOM"))
        return 0

    r = trouver_remplacement(profil, args.quoi)
    ancienne, nom_ancienne = r["remplace"], profil.nom(r["remplace"])
    # Installé mais désactivé : réinstallé puis réactivé, sinon il ne resterait aucune appli YouTube utilisable.
    installer = r["paquet"] not in tous or r["paquet"] in desactives
    desactiver = not args.garder_ancienne and ancienne in tous and ancienne not in desactives
    if desactiver and "fr.bouyguestelecom.tv.sml" in tous and "fr.bouyguestelecom.tv.sml" not in desactives:
        raise Erreur("SML (SpyObject) est encore actif : il réactiverait %s aussitôt. Lancez d'abord  "
                     "python3 lolos.py appliquer  (il désactive SML en premier), puis cette commande." % nom_ancienne)
    abi = adb.texte("getprop ro.product.cpu.abi")
    if not SORTIE_JSON:
        info("Plan%s :" % (" (simulation, rien ne sera modifié)" if args.simulation else ""))
        if installer:
            info("  1. %s %s (dernière version publiée sur github.com/%s, APK %s)" % (
                "Réinstaller et réactiver" if r["paquet"] in desactives else "Installer", r["nom"], r["depot"], abi))
        else:
            info("  ✓ %s est déjà installé." % r["nom"])
        if desactiver:
            info("  %d. Désactiver %s (%s) — pas désinstallé" % (2 if installer else 1, nom_ancienne, ancienne))
    if args.simulation or not (installer or desactiver):
        if SORTIE_JSON:
            afficher_json({"simulation": args.simulation, "installer": installer, "desactiver": desactiver})
        return 0
    if desactiver:
        exiger_sauvegarde(adb, args)
    confirmer(args, "\nContinuer ?")
    journaliser("== lolos remplacer %s → %s sur %s" % (ancienne, r["paquet"], adb.serie))
    if installer:
        version, fichier, url = apk_github(r["depot"], r["fichier"], abi)
        apk = telecharger(url, CACHE / fichier, "%s %s" % (r["nom"], version))
        info("Installation de %s…" % r["nom"])
        res = adb.lancer(["install", "-r", str(apk)], 300)
        journaliser("adb install -r %s" % apk.name)
        if "Success" not in res.sortie:
            raise Erreur("Installation de %s impossible : %s\n%s n'a pas été désactivé." % (
                r["nom"], (res.sortie + res.erreur).strip(), nom_ancienne))
        _effacer(apk)
        if r["paquet"] in desactives:
            adb.shell("pm enable %s" % r["paquet"])
        info("✅ %s installé." % r["nom"])
    resultats = []
    if desactiver:
        resultats = executer_plan(adb, [{"type": "desactiver", "id": ancienne, "nom": nom_ancienne}])
        memoriser_desactivations(adb, resultats)
    if SORTIE_JSON:
        afficher_json({"simulation": False, "installe": installer, "resultats": resultats})
    else:
        if r.get("avertissement"):
            info("• " + r["avertissement"])
        info("Revenir en arrière :  python3 lolos.py appliquer --garder %s" % ancienne)
    return 1 if any(not a["ok"] for a in resultats) else 0


# ---- sauvegarder ----

def cmd_sauvegarder(args):
    profil = charger_profil(getattr(args, "profil", None))
    adb = appareil(args)
    maintenant = datetime.datetime.now()
    if args.dossier:
        dossier = Path(args.dossier).expanduser()
    else:
        dossier = DOSSIER_SAUVEGARDES / maintenant.strftime("%Y-%m-%d_%H%M%S")
    if dossier.exists() and any(dossier.iterdir()):
        raise Erreur("Le dossier %s existe déjà et n'est pas vide." % dossier)
    dossier.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(str(dossier), 0o700)  # contient des identifiants personnels (réseau Wi-Fi, comptes…)
    if not args.dossier:
        garde = DOSSIER_SAUVEGARDES / ".gitignore"
        if not garde.exists():
            garde.write_text("# Sauvegardes personnelles de la TV : ne jamais les publier.\n*\n", encoding="utf-8")
    info("Sauvegarde de la TV dans %s …" % dossier)
    for nom, commande in FICHIERS_SAUVEGARDE:
        r = adb.shell(commande, timeout=300)
        (dossier / nom).write_text(r.sortie, encoding="utf-8")
        if nom == "dumpsys-package-complet.txt":
            (dossier / "versions.txt").write_text(versions_depuis_dumpsys(r.sortie), encoding="utf-8")
    for nom in ("packages-tous.txt", "settings-global.txt", "settings-secure.txt"):
        if not (dossier / nom).read_text(encoding="utf-8").strip():
            raise Erreur("Sauvegarde incomplète : %s est vide. Rien n'a été modifié sur la TV." % nom)
    try:
        adb.capturer(dossier / "capture-00-avant.png")
    except Erreur as e:
        avertir("Capture d'écran non sauvegardée : %s" % e)
    r = adb.plusieurs([("modele", "getprop ro.product.model"), ("fabricant", "getprop ro.product.manufacturer"),
                       ("android", "getprop ro.build.version.release"),
                       ("firmware", "getprop ro.build.version.incremental")])
    infos = {"outil": "lolos", "version_outil": VERSION, "date": maintenant.isoformat(timespec="seconds"),
             "appareil": adb.identifiant(), "profil_version": profil.version}
    infos.update(r)
    (dossier / "infos.json").write_text(json.dumps(infos, indent=2, ensure_ascii=False), encoding="utf-8")
    config = lire_config()
    liste = config.setdefault("sauvegardes", [])
    chemin = str(dossier.resolve())
    if chemin not in liste:
        liste.append(chemin)
    ecrire_config(config)
    nb = len(lire_paquets_sauvegarde(dossier, "packages-desactives.txt"))
    if SORTIE_JSON:
        afficher_json({"dossier": chemin, "fichiers": sorted(p.name for p in dossier.iterdir()), "nb_desactives": nb})
        return 0
    info("✅ Sauvegarde terminée : %d fichiers, %d applis désactivées au moment de la sauvegarde." %
         (len(list(dossier.iterdir())), nb))
    info("   Elle contient des informations personnelles (Wi-Fi, comptes) : ne la partagez pas.")
    info("Étape suivante :  python3 lolos.py lister   puis   python3 lolos.py appliquer --simulation …")
    return 0


# ---- accueil ----

def trouver_apk(option):
    if option:
        chemin = Path(option).expanduser()
        if not chemin.is_file():
            raise Erreur("APK introuvable : %s" % option)
        return chemin
    for candidat in (RACINE / "android" / "Accueil.apk", RACINE / "android" / "build" / "Accueil.apk"):
        if candidat.is_file():
            return candidat
    return telecharger(URL_RELEASE + "Accueil.apk", CACHE / "Accueil.apk", "le lanceur Accueil (dernière version)")


def trouver_fonds(option):
    if option:
        dossier = Path(option).expanduser()
        if not dossier.is_dir():
            raise Erreur("Dossier de fonds introuvable : %s" % option)
        return sorted(dossier.glob("*.mp4"))
    locaux = sorted((RACINE / "fonds").glob("*.mp4"))
    if locaux:
        return locaux
    cache = CACHE / "fonds"
    marque = cache / "version"
    try:
        a_jour = marque.read_text(encoding="utf-8").strip() == VERSION_FONDS
    except OSError:
        a_jour = False
    anciens = sorted(cache.glob("*.mp4"))
    if a_jour and anciens:
        return anciens
    try:
        archive = telecharger(URL_RELEASE + "fonds.zip", CACHE / "fonds.zip", "les fonds animés (fonds.zip)")
    except Erreur as e:
        if anciens:
            avertir("Nouveaux fonds animés indisponibles (%s) : anciens gardés." % e)
            return anciens
        avertir("Fonds animés indisponibles (%s) : le lanceur utilisera son fond par défaut." % e)
        return []
    cache.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(str(archive)) as z:
            for ancien in anciens:
                _effacer(ancien)
            for membre in z.infolist():
                nom = Path(membre.filename).name  # aplatit et neutralise tout chemin
                if nom.lower().endswith(".mp4") and not nom.startswith("."):
                    with z.open(membre) as source, open(str(cache / nom), "wb") as cible:
                        shutil.copyfileobj(source, cible)
        marque.write_text(VERSION_FONDS, encoding="utf-8")
    except zipfile.BadZipFile:
        avertir("fonds.zip est corrompu : fonds ignorés.")
        return anciens
    finally:
        _effacer(archive)
    return sorted(cache.glob("*.mp4"))


def cmd_accueil(args):
    profil = charger_profil(getattr(args, "profil", None))
    acc = profil.accueil
    paquet, activite = acc.get("paquet", "fr.accueil.tv"), acc.get("activite", "fr.accueil.tv/.MainActivity")
    dossier_fonds = acc.get("dossier_fonds", "/sdcard/Android/data/%s/files/fonds" % paquet)
    adb = appareil(args)
    avertir_si_pas_bbox(adb, profil)
    if not args.simulation:
        exiger_sauvegarde(adb, args)
    apk = trouver_apk(args.apk)
    fonds = trouver_fonds(args.fonds)
    etapes = ["adb install -r %s" % apk.name]
    etapes += ["appops set %s %s allow" % (paquet, op) for op in acc.get("appops", [])]
    etapes += ["envoi de %d fonds animés vers %s" % (len(fonds), dossier_fonds)] if fonds else []
    etapes += ["cmd package set-home-activity %s" % activite]
    if acc.get("ecran_veille"):
        etapes += ["écran de veille : %s" % acc["ecran_veille"]]
    if args.simulation:
        info("Installation du lanceur (simulation, rien ne sera modifié) :")
        for e in etapes:
            info("  • " + e)
        return 0
    journaliser("== lolos accueil sur %s" % adb.serie)
    info("Installation du lanceur Accueil (%s)…" % apk)
    r = adb.lancer(["install", "-r", str(apk)], 300)
    journaliser("adb install -r %s" % apk.name)
    if "UPDATE_INCOMPATIBLE" in r.sortie + r.erreur or "signatures do not match" in r.sortie + r.erreur:
        # Version déjà installée signée par une autre clé (compilation locale ou ancienne version) :
        # on retire NOTRE lanceur seulement, puis on le réinstalle (les fonds sont renvoyés ensuite).
        info("  Ancienne version signée différemment : réinstallation du lanceur…")
        adb.lancer(["uninstall", paquet], 120)
        journaliser("adb uninstall %s (signature différente)" % paquet)
        r = adb.lancer(["install", "-r", str(apk)], 300)
        journaliser("adb install -r %s" % apk.name)
    if "Success" not in r.sortie:
        raise Erreur("Installation de l'APK refusée : %s" % (r.sortie + r.erreur).strip())
    info("  ✅ APK installé")
    # SYSTEM_ALERT_WINDOW : permet à la tuile TV de relancer la demande de direct pendant que
    # l'interface Bbox démarre (sinon le premier appui n'affiche parfois rien).
    for op in acc.get("appops", []):
        adb.sh("appops", "set", paquet, op, "allow")
        journaliser("adb shell appops set %s %s allow" % (paquet, op))
    if fonds:
        adb.sh("mkdir", "-p", dossier_fonds)
        for f in fonds:
            distant = "%s/%s" % (dossier_fonds, f.name)
            taille = adb.sh("stat", "-c", "%s", distant).sortie.strip()
            if taille == str(f.stat().st_size):
                info("  · %s déjà présent" % f.name)
                continue
            info("  → envoi de %s (%.0f Mo)…" % (f.name, f.stat().st_size / 1e6))
            r = adb.lancer(["push", str(f), distant], 900)
            journaliser("adb push %s %s" % (f.name, distant))
            if r.code != 0:
                avertir("Envoi de %s impossible : %s" % (f.name, (r.erreur or r.sortie).strip()))
    ok, detail = executer_action(adb, {"type": "accueil", "id": activite})
    if not ok:
        raise Erreur("Impossible de définir l'écran d'accueil : %s" % detail)
    info("  ✅ écran d'accueil : %s" % activite)
    if acc.get("ecran_veille"):
        adb.sh("settings", "put", "secure", "screensaver_components", acc["ecran_veille"])
        adb.sh("settings", "put", "secure", "screensaver_enabled", "1")
        journaliser("adb shell settings put secure screensaver_components %s ; screensaver_enabled 1" % acc["ecran_veille"])
        info("  ✅ écran de veille : %s" % acc["ecran_veille"])
    adb.sh("input", "keyevent", "KEYCODE_HOME")
    info("Terminé. Le nouvel accueil s'affiche sur la TV. Vérifiez avec :  python3 lolos.py verifier")
    return 0


# ---- capture ----

def cmd_capture(args):
    adb = appareil(args)
    fichier = adb.capturer(args.fichier)
    if SORTIE_JSON:
        afficher_json({"fichier": str(Path(fichier).resolve())})
    else:
        info(str(fichier))
        info("(Le direct TV apparaît noir sur les captures : la vidéo est sur un plan matériel, c'est normal.)")
    return 0


# ---- personnaliser / fonds / veille / redemarrer ----
# Format des fichiers lus par le lanceur : docs/personnalisation.md. Tout est facultatif.

# Clés de personnalisation.json : (clé, valeur par défaut du lanceur, libellé).
OPTIONS_LOLOS = [
    ("salutation", "", "Message d'accueil"),
    ("demarrage", True, "Écran de démarrage"),
    ("message_demarrage", "", "Message de démarrage"),
    ("duree_demarrage_ms", 2500, "Durée du démarrage"),
    ("nettoyage_memoire", False, "Nettoyage mémoire"),
    ("infos_systeme", False, "Tuile Infos système"),
    ("mode_nuit", False, "Mode nuit (21 h – 7 h)"),
    ("fonds_lolos", True, "Paysages Lol'OS"),
    ("meteo", None, "Météo"),
]
DEFAUTS_LOLOS = {cle: defaut for cle, defaut, _ in OPTIONS_LOLOS}
LIBELLES_LOLOS = {cle: libelle for cle, _, libelle in OPTIONS_LOLOS}
OUI_NON = ["oui", "non"]
URL_GEOCODAGE = "https://geocoding-api.open-meteo.com/v1/search?name=%s&count=1&language=fr&format=json"
EXTENSIONS_FONDS = {".mp4": "vidéo", ".jpg": "photo", ".jpeg": "photo", ".png": "photo"}
LIMITE_VIDEO_MO = 100
VEILLE_PROFONDE = {  # docs/personnalisation.md, « Réglage système : veille profonde »
    "1h": [("lowpower_control_enabled", "1"), ("lowpower_control_delay", "3600000")],
    "24h": [("lowpower_control_enabled", "1"), ("lowpower_control_delay", "86400000")],
    "jamais": [("lowpower_control_enabled", "0")],
}


def emplacements_lolos(profil):
    """(paquet du lanceur, dossier de ses fichiers sur la TV, dossier des fonds)."""
    paquet = profil.accueil.get("paquet", "fr.accueil.tv")
    distant = "/sdcard/Android/data/%s/files" % paquet
    return paquet, distant, profil.accueil.get("dossier_fonds", distant + "/fonds")


def lire_personnalisation(adb, distant):
    """personnalisation.json actuel sur la TV : (dict, "ok" | "absent" | "illisible")."""
    r = adb.sh("cat", distant + "/personnalisation.json")
    if r.code != 0:
        return {}, ("absent" if "no such file" in (r.sortie + r.erreur).lower() else "illisible")
    texte = r.sortie.strip().lstrip("﻿")
    if not texte:
        return {}, "absent"
    try:
        donnees = json.loads(texte)
    except ValueError:
        return {}, "illisible"
    return (donnees, "ok") if isinstance(donnees, dict) else ({}, "illisible")


def decrire_option(cle, valeur):
    if cle == "meteo":
        if not isinstance(valeur, dict):
            return "aucune"
        return "%s (%s, %s)" % (valeur.get("ville") or "?", valeur.get("latitude"), valeur.get("longitude"))
    if cle == "duree_demarrage_ms" and isinstance(valeur, (int, float)) and not isinstance(valeur, bool):
        return ("%.1f s" % (valeur / 1000)).replace(".", ",")
    if isinstance(valeur, bool):
        return "oui" if valeur else "non"
    if isinstance(valeur, str):
        return "« %s »" % valeur if valeur else "(aucun)"
    return json.dumps(valeur, ensure_ascii=False)


def geocoder_ville(ville):
    """Coordonnées d'une ville (géocodage Open-Meteo, sans compte) : (valeur de « meteo », détails)."""
    nom = (ville or "").strip()
    if len(nom) < 2:
        raise Erreur("Nom de ville trop court : %r" % ville)
    requete = urllib.request.Request(URL_GEOCODAGE % urllib.parse.quote(nom),
                                     headers={"User-Agent": "lolos/" + VERSION})
    try:
        with urllib.request.urlopen(requete, timeout=20) as reponse:
            donnees = json.loads(reponse.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise Erreur("Recherche de la ville impossible (Open-Meteo) : erreur HTTP %d." % e.code)
    except (urllib.error.URLError, OSError, socket.timeout) as e:
        conseil = ""
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            conseil = ("\nCertificats SSL absents : avec le Python de python.org sur macOS, lancez "
                       "« Install Certificates.command » (dossier Applications/Python 3.x).")
        raise Erreur("Recherche de la ville impossible (Open-Meteo) : %s%s" % (getattr(e, "reason", e), conseil))
    except ValueError:
        raise Erreur("Réponse illisible du service de géocodage Open-Meteo.")
    resultats = donnees.get("results") if isinstance(donnees, dict) else None
    if not resultats:
        raise Erreur("Aucune ville trouvée pour « %s » (Open-Meteo). Vérifiez l'orthographe, ou essayez le code "
                     "postal ou une ville voisine." % nom)
    premier = resultats[0]
    try:
        latitude, longitude = round(float(premier["latitude"]), 4), round(float(premier["longitude"]), 4)
    except (KeyError, TypeError, ValueError):
        raise Erreur("Réponse incomplète d'Open-Meteo pour « %s » (pas de coordonnées)." % nom)
    meteo = {"ville": premier.get("name") or nom, "latitude": latitude, "longitude": longitude}
    return meteo, {"pays": premier.get("country"), "region": premier.get("admin1")}


def fichiers_fonds(adb, dossier):
    """{nom: taille en octets} des fichiers du dossier des fonds sur la TV ({} s'il n'existe pas)."""
    sortie = adb.shell("stat -c '%%s %%n' %s/* 2>/dev/null" % shlex.quote(dossier)).sortie
    fichiers = {}
    for ligne in sortie.splitlines():
        taille, _, chemin = ligne.partition(" ")
        if taille.isdigit() and chemin.startswith(dossier + "/"):
            nom = chemin[len(dossier) + 1:]
            if nom and "/" not in nom:
                fichiers[nom] = int(taille)
    return fichiers


def fonds_perso(fichiers):
    """Seulement VOS fonds (perso-*) : les paysages Lol'OS (0*.mp4) n'en font jamais partie."""
    return {n: t for n, t in fichiers.items() if n.startswith("perso-")}


def cmd_personnaliser(args):
    """Options de Lol'OS (personnalisation.json + logo-demarrage.png), lues par le lanceur sur la TV."""
    profil = charger_profil(getattr(args, "profil", None))
    paquet, distant, dossier_fonds = emplacements_lolos(profil)
    fichier_distant = distant + "/personnalisation.json"
    changements = {}
    if args.message is not None:
        changements["salutation"] = args.message.strip()
    if args.message_demarrage is not None:
        changements["message_demarrage"] = args.message_demarrage.strip()
    if args.sans_demarrage and args.demarrage == "oui":
        raise Erreur("--demarrage oui et --sans-demarrage sont contradictoires.")
    if args.sans_demarrage or args.demarrage:
        changements["demarrage"] = not args.sans_demarrage and args.demarrage == "oui"
    for option, cle in (("nettoyage_memoire", "nettoyage_memoire"), ("infos_systeme", "infos_systeme"),
                        ("mode_nuit", "mode_nuit"), ("paysages_lolos", "fonds_lolos")):
        if getattr(args, option):
            changements[cle] = getattr(args, option) == "oui"
    if args.sans_meteo:
        changements["meteo"] = None
    logo = Path(args.logo).expanduser() if args.logo else None
    if logo:
        if not logo.is_file():
            raise Erreur("Image introuvable : %s" % logo)
        if logo.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            raise Erreur("Image PNG, JPEG ou WebP attendue (exportez d'abord un SVG en PNG).")
        if logo.stat().st_size > 3e6:
            info("⚠️ Image lourde (%.1f Mo) : une image de moins de 1000 px suffit." % (logo.stat().st_size / 1e6))
    demande = bool(changements or args.meteo or logo or args.logo_lolos or args.apercu)
    if args.afficher and demande:
        raise Erreur("--afficher s'utilise seul : il montre la configuration actuelle sans rien modifier.")
    if not demande:
        return afficher_personnalisation(appareil(args), distant, dossier_fonds, rappel=not args.afficher)

    meteo_trouvee = None
    if args.meteo:  # avant tout accès à la TV : une ville introuvable n'a rien modifié
        changements["meteo"], details = geocoder_ville(args.meteo)
        lieu = ", ".join(x for x in (details.get("region"), details.get("pays")) if x)
        meteo_trouvee = dict(changements["meteo"], **details)
        info("Météo : ville trouvée = %s%s (%s, %s)." % (changements["meteo"]["ville"], " — %s" % lieu if lieu else "",
                                                         changements["meteo"]["latitude"],
                                                         changements["meteo"]["longitude"]))
        info("        Vérifiez que c'est la bonne ; sinon précisez (code postal, ville voisine…).")
    adb = None
    try:
        adb = appareil(args)
    except Erreur as e:
        if not args.simulation:
            raise
        avertir("TV injoignable (%s) : simulation sans la configuration actuelle de la TV."
                % re.split(r"\.\s|\n", str(e))[0].rstrip(" .:"))
    existant, etat_fichier = lire_personnalisation(adb, distant) if adb else ({}, "inconnu")
    if etat_fichier == "illisible" and changements:
        avertir("personnalisation.json est illisible sur la TV : il sera réécrit avec les seules options indiquées.")
    # Fusion : seules les clés demandées changent, les autres (et les clés inconnues) sont gardées telles quelles.
    modifiees = [c for c, v in changements.items() if existant.get(c, DEFAUTS_LOLOS.get(c)) != v]
    fusion = dict(existant)
    fusion.update(changements)

    info("Personnalisation%s :" % (" (simulation, rien ne sera modifié)" if args.simulation else ""))
    for cle in modifiees:
        avant = existant.get(cle, DEFAUTS_LOLOS.get(cle))
        info("  • %s : %s → %s" % (LIBELLES_LOLOS.get(cle, cle), decrire_option(cle, avant),
                                   decrire_option(cle, changements[cle])))
    for cle in changements:
        if cle not in modifiees:
            info("  · %s : déjà %s" % (LIBELLES_LOLOS.get(cle, cle), decrire_option(cle, changements[cle])))
    if logo:
        info("  • %s/logo-demarrage.png ← %s" % (distant, logo))
    elif args.logo_lolos:
        info("  • suppression de %s/logo-demarrage.png (retour au logo Lol'OS)" % distant)
    if args.apercu:
        info("  • aperçu de l'écran de démarrage sur la TV")
    if changements.get("fonds_lolos") is False and adb and not fonds_perso(fichiers_fonds(adb, dossier_fonds)):
        avertir("Aucun de vos fonds n'est sur la TV : les paysages Lol'OS restent affichés tant que vous n'en "
                "ajoutez pas (python3 lolos.py fonds --ajouter …).")
    resultat = {"simulation": args.simulation, "fichier": fichier_distant, "etat_fichier": etat_fichier,
                "avant": existant, "apres": fusion, "modifiees": modifiees, "meteo_trouvee": meteo_trouvee,
                "logo": str(logo) if logo else None, "logo_lolos": bool(args.logo_lolos and not logo)}
    if args.simulation:
        if SORTIE_JSON:
            afficher_json(resultat)
        return 0

    adb.sh("mkdir", "-p", distant)
    if modifiees:
        with tempfile.TemporaryDirectory() as dossier:  # dossier privé, pas un fichier fixe dans /tmp
            fichier = Path(dossier) / "personnalisation.json"
            fichier.write_text(json.dumps(fusion, ensure_ascii=False, indent=2), encoding="utf-8")
            r = adb.lancer(["push", str(fichier), fichier_distant], 60)
            if r.code != 0:
                raise Erreur("Envoi impossible : %s" % (r.sortie + r.erreur).strip())
        journaliser("personnalisation (%s) : %s" % (adb.serie, json.dumps({c: fusion[c] for c in modifiees},
                                                                         ensure_ascii=False)))
    if logo:
        r = adb.lancer(["push", str(logo), distant + "/logo-demarrage.png"], 120)
        if r.code != 0:
            raise Erreur("Envoi de l'image impossible : %s" % (r.sortie + r.erreur).strip())
        journaliser("logo de démarrage : %s" % logo.name)
    elif args.logo_lolos:
        adb.sh("rm", "-f", distant + "/logo-demarrage.png")
        journaliser("logo de démarrage : retour au logo Lol'OS")
    if modifiees or logo or args.logo_lolos:
        info("✅ Personnalisation envoyée (appliquée au prochain retour à l'accueil).")
    elif changements:
        info("Rien à changer : la TV a déjà ces réglages.")
    if args.apercu:
        adb.sh("am", "start", "-n", profil.accueil.get("activite", paquet + "/.MainActivity"),
               "--ez", "fr.accueil.tv.APERCU_DEMARRAGE", "true")
        info("  Aperçu de l'écran de démarrage affiché sur la TV.")
    if SORTIE_JSON:
        afficher_json(resultat)
    return 0


def afficher_personnalisation(adb, distant, dossier_fonds, rappel=False):
    config, etat_fichier = lire_personnalisation(adb, distant)
    logo = adb.sh("ls", distant + "/logo-demarrage.png").code == 0
    perso = sorted(fonds_perso(fichiers_fonds(adb, dossier_fonds)))
    effective = {cle: config.get(cle, defaut) for cle, defaut, _ in OPTIONS_LOLOS}
    if SORTIE_JSON:
        afficher_json({"fichier": distant + "/personnalisation.json", "etat_fichier": etat_fichier,
                       "configuration": config, "effective": effective, "logo_personnalise": logo,
                       "fonds_perso": perso})
        return 0
    titre = {"ok": "", "absent": " (fichier absent : valeurs par défaut)",
             "illisible": " (fichier illisible : valeurs par défaut)"}[etat_fichier]
    info("Personnalisation de Lol'OS sur la TV%s :" % titre)
    lignes = [(libelle, decrire_option(cle, effective[cle]) + ("" if cle in config else "  (défaut)"))
              for cle, _, libelle in OPTIONS_LOLOS]
    lignes.append(("Logo de démarrage", "personnalisé (logo-demarrage.png)" if logo else "logo Lol'OS"))
    lignes.append(("Vos fonds", ("%d (python3 lolos.py fonds --lister)" % len(perso)) if perso else "aucun"))
    autres = sorted(set(config) - set(DEFAUTS_LOLOS))
    if autres:
        lignes.append(("Autres clés", ", ".join(autres)))
    largeur = max(len(t) for t, _ in lignes)
    for titre_ligne, valeur in lignes:
        info("  %s : %s" % (titre_ligne.ljust(largeur), valeur))
    if rappel:
        info("\nPour changer une option :  python3 lolos.py personnaliser --help")
    return 0


# ---- fonds ----

LIGATURES = {"œ": "oe", "Œ": "oe", "æ": "ae", "Æ": "ae", "ß": "ss"}


def nettoyer_nom(texte):
    """Minuscules, sans accents, espaces et caractères spéciaux → « - »."""
    texte = "".join(LIGATURES.get(c, c) for c in texte)
    texte = "".join(c for c in unicodedata.normalize("NFKD", texte) if not unicodedata.combining(c)).lower()
    texte = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9._-]+", "-", texte)).strip("-._")
    return texte[len("perso-"):] if texte.startswith("perso-") else texte


def nom_fond(chemin):
    """Nom du fond sur la TV : perso-<nom nettoyé>.<extension en minuscules>."""
    p = Path(chemin)
    return "perso-%s%s" % (nettoyer_nom(p.stem) or "fond", p.suffix.lower())


def type_fond(nom):
    return EXTENSIONS_FONDS.get(Path(nom).suffix.lower(), "?")


def taille_lisible(octets):
    return ("%.1f Mo" % (octets / 1e6)) if octets >= 1e6 else ("%d Ko" % max(1, round(octets / 1e3)))


def espace_libre(adb, chemin="/sdcard"):
    """Octets libres sur le stockage de la TV, ou None si illisible."""
    for ligne in reversed(adb.texte("df -k %s" % shlex.quote(chemin)).splitlines()):
        champs = ligne.split()
        if len(champs) >= 6 and champs[3].isdigit():
            return int(champs[3]) * 1024
    return None


def cmd_fonds(args):
    """Vos fonds d'écran (perso-*) dans le dossier des fonds de Lol'OS sur la TV."""
    profil = charger_profil(getattr(args, "profil", None))
    paquet, distant, dossier = emplacements_lolos(profil)
    if args.ajouter:
        return ajouter_fonds(args, paquet, dossier)
    if args.retirer or args.retirer_tout:
        return retirer_fonds(args, distant, dossier)
    adb = appareil(args)
    fichiers = fichiers_fonds(adb, dossier)
    perso = sorted(fonds_perso(fichiers).items())
    paysages = [n for n in fichiers if not n.startswith("perso-") and n.lower().endswith(".mp4")]
    if SORTIE_JSON:
        afficher_json({"dossier": dossier, "paysages_lolos": len(paysages),
                       "fonds_perso": [{"nom": n, "type": type_fond(n), "taille_octets": t} for n, t in perso]})
        return 0
    if perso:
        info("Vos fonds sur la TV (%s) :" % dossier)
        tableau(["Fond", "Type", "Taille"], [[n, type_fond(n), taille_lisible(t)] for n, t in perso])
        info("\nTotal : %d fond(s), %.0f Mo." % (len(perso), sum(t for _, t in perso) / 1e6))
    else:
        info("Aucun de vos fonds sur la TV. Pour en ajouter :  python3 lolos.py fonds --ajouter photo.jpg video.mp4")
    info("Paysages Lol'OS : %d vidéo(s) (jamais supprimées par cette commande ; pour ne plus les afficher : "
         "python3 lolos.py personnaliser --paysages-lolos non)." % len(paysages))
    return 0


def ajouter_fonds(args, paquet, dossier):
    envois, origines = [], {}
    for brut in args.ajouter:  # tout est vérifié avant le premier envoi
        f = Path(brut).expanduser()
        if not f.is_file():
            raise Erreur("Fichier introuvable : %s" % brut)
        if f.suffix.lower() not in EXTENSIONS_FONDS:
            raise Erreur("%s : format non pris en charge. Vidéos .mp4 (H.264 ou H.265) ou photos .jpg, .jpeg, "
                         ".png." % f.name)
        nom = nom_fond(f)
        if nom in origines:
            if origines[nom] == f.resolve():
                continue
            raise Erreur("%s et %s auraient le même nom sur la TV (%s) : renommez l'un des deux."
                         % (origines[nom].name, f.name, nom))
        origines[nom] = f.resolve()
        taille = f.stat().st_size
        if type_fond(nom) == "vidéo" and taille > LIMITE_VIDEO_MO * 1e6:
            avertir("%s pèse %.0f Mo : au-delà de %d Mo la box (peu de stockage) peine. Conseil : 1080p, H.264 ou "
                    "H.265, 30 à 60 s en boucle." % (f.name, taille / 1e6, LIMITE_VIDEO_MO))
        elif type_fond(nom) == "photo" and taille > 20e6:
            avertir("%s est une photo lourde (%.0f Mo) : 1920×1080 suffit." % (f.name, taille / 1e6))
        envois.append((f, nom, taille))
    adb = appareil(args)
    presents = fichiers_fonds(adb, dossier)
    a_envoyer = [(f, nom, t) for f, nom, t in envois if presents.get(nom) != t]
    deja = [nom for _, nom, t in envois if presents.get(nom) == t]
    total = sum(t for _, _, t in a_envoyer)
    libre = espace_libre(adb) if a_envoyer else None
    if libre is not None and total > libre - 200e6:  # on laisse de la marge à la box
        raise Erreur("Pas assez de place sur la TV : %.0f Mo à envoyer, %.0f Mo libres (200 Mo gardés pour la box)."
                     % (total / 1e6, libre / 1e6))
    lanceur = adb.sh("pm", "path", paquet).sortie.strip().startswith("package:")
    if not lanceur:
        avertir("Le lanceur Lol'OS n'est pas installé : vos fonds serviront après « python3 lolos.py accueil ».")
    info("Vos fonds%s :" % (" (simulation, rien ne sera modifié)" if args.simulation else ""))
    for f, nom, t in a_envoyer:
        info("  • %s %s → %s (%s, %s)" % ("remplacer" if nom in presents else "envoyer", f.name, nom,
                                          type_fond(nom), taille_lisible(t)))
    for nom in deja:
        info("  · %s déjà sur la TV (même taille)" % nom)
    if any(type_fond(n) == "vidéo" for _, n, _ in a_envoyer):
        info("  (Vidéos : H.264 ou H.265 en 1080p conseillé, son ignoré ; le format n'est pas vérifié ici.)")
    resultat = {"simulation": args.simulation, "dossier": dossier, "deja_presents": deja,
                "a_envoyer": [{"fichier": str(f), "nom": nom, "taille_octets": t} for f, nom, t in a_envoyer]}
    if args.simulation or not a_envoyer:
        if not a_envoyer:
            info("Rien à envoyer.")
        if SORTIE_JSON:
            afficher_json(resultat)
        return 0
    adb.sh("mkdir", "-p", dossier)
    journaliser("== lolos fonds --ajouter (%d fichier(s)) sur %s" % (len(a_envoyer), adb.serie))
    echecs = []
    for f, nom, t in a_envoyer:
        info("  → envoi de %s (%s)…" % (nom, taille_lisible(t)))
        r = adb.lancer(["push", str(f), "%s/%s" % (dossier, nom)], 900)
        journaliser("adb push %s %s/%s" % (f.name, dossier, nom))
        if r.code != 0:
            echecs.append(nom)
            avertir("Envoi de %s impossible : %s" % (f.name, (r.erreur or r.sortie).strip()))
    resultat["echecs"] = echecs
    if SORTIE_JSON:
        afficher_json(resultat)
    else:
        info("✅ %d fond(s) envoyé(s) : visibles au prochain retour à l'accueil." % (len(a_envoyer) - len(echecs)))
        info("   Pour n'afficher que vos fonds :  python3 lolos.py personnaliser --paysages-lolos non")
    return 1 if echecs else 0


def trouver_fonds_perso(brut, perso):
    """Fonds perso désignés par NOM : nom sur la TV, nom du fichier d'origine, avec ou sans extension."""
    nom = Path(brut).name
    if nom in perso:
        return [nom]
    p = Path(nom)
    if p.suffix.lower() in EXTENSIONS_FONDS:
        candidat = nom_fond(p)
        return [candidat] if candidat in perso else []
    base = "perso-%s" % (nettoyer_nom(nom) or "fond")
    return sorted(n for n in perso if Path(n).stem == base)


def retirer_fonds(args, distant, dossier):
    adb = appareil(args)
    perso = fonds_perso(fichiers_fonds(adb, dossier))
    if args.retirer_tout:
        cibles = sorted(perso)
    else:
        cibles, inconnus = [], []
        for brut in args.retirer:
            trouves = trouver_fonds_perso(brut, perso)
            if not trouves:
                inconnus.append(brut)
            cibles += [n for n in trouves if n not in cibles]
        if inconnus:
            raise Erreur("Introuvable parmi vos fonds sur la TV : %s\nVos fonds : %s\n(les paysages Lol'OS ne "
                         "peuvent pas être retirés)" % (", ".join(inconnus), ", ".join(sorted(perso)) or "aucun"))
    # Garde-fou : jamais autre chose qu'un fichier perso-* du dossier des fonds.
    cibles = [n for n in cibles if n.startswith("perso-") and "/" not in n and n in perso]
    resultat = {"simulation": args.simulation, "dossier": dossier, "a_retirer": cibles}
    if not cibles:
        info("Aucun de vos fonds sur la TV : rien à retirer.")
        if SORTIE_JSON:
            afficher_json(resultat)
        return 0
    info("Fonds à retirer de la TV%s :" % (" (simulation, rien ne sera modifié)" if args.simulation else ""))
    for n in cibles:
        info("  • %s (%s)" % (n, taille_lisible(perso[n])))
    if args.simulation:
        if SORTIE_JSON:
            afficher_json(resultat)
        return 0
    confirmer(args, "\nRetirer ces %d fond(s) de la TV ? (gardez vos fichiers d'origine : ils ne sont pas "
                    "récupérables depuis la TV)" % len(cibles))
    journaliser("== lolos fonds --retirer (%d fichier(s)) sur %s" % (len(cibles), adb.serie))
    for n in cibles:
        adb.sh("rm", "-f", "%s/%s" % (dossier, n))
        journaliser("adb shell rm -f %s/%s" % (dossier, n))
    restants = fonds_perso(fichiers_fonds(adb, dossier))
    resultat["echecs"] = [n for n in cibles if n in restants]
    if SORTIE_JSON:
        afficher_json(resultat)
    else:
        info("✅ %d fond(s) retiré(s). Il vous en reste %d." % (len(cibles) - len(resultat["echecs"]), len(restants)))
        if not restants and lire_personnalisation(adb, distant)[0].get("fonds_lolos") is False:
            info("   Plus aucun de vos fonds : les paysages Lol'OS reviennent (option « paysages Lol'OS » : non).")
    return 1 if resultat["echecs"] else 0


# ---- veille ----

def choix_veille(actif, delai):
    if actif == "0":
        return "jamais"
    if actif == "1":
        return {"3600000": "1h", "86400000": "24h"}.get(delai, "autre")
    return None


def libelle_veille(actif, delai):
    choix = choix_veille(actif, delai)
    if choix == "jamais":
        return "jamais (la box reste en veille, rallumage immédiat)"
    if choix == "1h":
        return "après 1 h de veille (réglage Bouygues)"
    if choix == "24h":
        return "après 24 h de veille"
    if choix == "autre":
        try:
            minutes = int(delai) // 60000
            return "après %s de veille" % ("%d h" % (minutes // 60) if minutes % 60 == 0 else "%d min" % minutes)
        except (TypeError, ValueError):
            return "activée, délai inconnu (%s)" % delai
    if actif is None:
        return "non défini (comportement d'origine de la box)"
    return "inconnu (lowpower_control_enabled = %s)" % actif


def lire_veille(adb):
    v = lire_reglages(adb, ["global/lowpower_control_enabled", "global/lowpower_control_delay"])
    actif, delai = v["global/lowpower_control_enabled"], v["global/lowpower_control_delay"]
    return {"profonde": choix_veille(actif, delai), "libelle": libelle_veille(actif, delai),
            "lowpower_control_enabled": actif, "lowpower_control_delay": delai}


def cmd_veille(args):
    adb = appareil(args)
    avant = lire_veille(adb)
    if not args.profonde:
        if SORTIE_JSON:
            afficher_json(avant)
            return 0
        info("Veille profonde : %s" % avant["libelle"])
        info("Au-delà de ce délai en veille, la Bbox s'éteint vraiment (rallumage = démarrage complet, ≈1 min).")
        info("Changer :  python3 lolos.py veille --profonde 1h|24h|jamais   (1h = réglage Bouygues)")
        return 0
    actions = [{"type": "reglage", "id": "global/" + cle, "ns": "global", "cle": cle, "valeur": valeur,
                "avant": avant[cle], "nom": "veille profonde : %s" % args.profonde}
               for cle, valeur in VEILLE_PROFONDE[args.profonde] if avant[cle] != valeur]
    sauvegarde = None
    if actions and not args.simulation:
        sauvegarde = exiger_sauvegarde(adb, args)
    if not SORTIE_JSON:
        info("Veille profonde%s : %s" % (" (simulation, rien ne sera modifié)" if args.simulation else "",
                                          avant["libelle"]))
        if not actions:
            info("  Rien à faire : déjà réglée sur « %s »." % args.profonde)
        for i, a in enumerate(actions, 1):
            info("  %2d. %s" % (i, decrire_action(a)))
        if args.simulation and actions and not sauvegardes_de(adb):
            info("⚠️  Aucune sauvegarde de cette TV : lancez « sauvegarder » avant de changer ce réglage.")
    if args.simulation or not actions:
        if SORTIE_JSON:
            afficher_json({"simulation": args.simulation, "avant": avant, "actions": actions})
        return 0
    journaliser("== lolos veille --profonde %s sur %s (sauvegarde : %s)" % (args.profonde, adb.serie, sauvegarde))
    resultats = executer_plan(adb, actions)
    apres = lire_veille(adb)
    echec = any(not a["ok"] for a in resultats) or apres["profonde"] != args.profonde
    if SORTIE_JSON:
        afficher_json({"simulation": False, "avant": avant, "resultats": resultats, "apres": apres})
    else:
        info("%s Veille profonde : %s" % ("❌" if echec else "✅", apres["libelle"]))
        info("   Revenir au réglage Bouygues :  python3 lolos.py veille --profonde 1h   (ou « restaurer »)")
    return 1 if echec else 0


def cmd_redemarrer(args):
    adb = appareil(args)
    confirmer(args, "Redémarrer la box maintenant ?")
    journaliser("adb reboot (%s)" % adb.serie)
    try:
        subprocess.run([adb.chemin, "-s", adb.serie, "reboot"], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
    except subprocess.TimeoutExpired:
        pass  # adb attend que la TV disparaisse du réseau : elle redémarre déjà
    info("✅ La box redémarre (environ 1 minute). Ensuite :  python3 lolos.py connecter")
    return 0


# ---- verifier ----

def mesurer_debit(adb, duree=3):
    """Débit reçu (Mbit/s) de l'interface réseau la plus active, mesuré sur la TV elle-même."""
    sortie = adb.texte("cat /proc/net/dev; sleep %d; echo @@; cat /proc/net/dev" % duree, timeout=duree + 30)
    avant, _, apres = sortie.partition("@@")

    def octets(texte):
        valeurs = {}
        for ligne in texte.splitlines():
            if ":" in ligne:
                nom, reste = ligne.split(":", 1)
                champs = reste.split()
                if champs and champs[0].isdigit():
                    valeurs[nom.strip()] = int(champs[0])
        return valeurs
    a, b = octets(avant), octets(apres)
    debits = {i: (b[i] - a[i]) * 8 / duree / 1e6 for i in b if i in a and i != "lo"}
    if not debits:
        return None, None
    interface = max(debits, key=debits.get)
    return interface, debits[interface]


def cmd_verifier(args):
    profil = charger_profil(getattr(args, "profil", None))
    adb = appareil(args)
    e = lire_etat(adb, profil)
    tous, desactives, _ = adb.paquets()
    controles = []

    def controle(cle, ok, titre, detail="", conseil=""):
        controles.append({"id": cle, "ok": bool(ok), "titre": titre, "detail": detail, "conseil": conseil})

    controle("sml", e["sml_desactive"] or "fr.bouyguestelecom.tv.sml" not in tous, "SML (SpyObject) désactivé",
             "" if e["sml_desactive"] else "SML est actif : il réactive les applis désactivées.",
             "python3 lolos.py appliquer  (SML est désactivé en premier)")
    attendus = set(p["id"] for p in profil.paquets
                   if not profil.cat.get(p["categorie"], {}).get("demander") and p.get("defaut") == "desactiver")
    attendus |= set(lire_config().get("desactives_par_lolos", {}).get(adb.identifiant(), []))
    attendus = set(p for p in attendus if p in tous)
    manquants = sorted(attendus - desactives)
    controle("paquets", not manquants, "Applis prévues désactivées (%d/%d)" % (len(attendus) - len(manquants), len(attendus)),
             ("Encore actives : " + ", ".join(manquants)) if manquants else "",
             "python3 lolos.py appliquer  (puis redémarrez la TV si elles se réactivent)" if manquants else "")
    lanceur = profil.accueil.get("activite", "fr.accueil.tv/.MainActivity")
    controle("accueil", e["accueil_est_lanceur"], "Écran d'accueil = lanceur Accueil",
             "Accueil actuel : %s" % e["accueil"],
             "" if e["accueil_est_lanceur"] else ("python3 lolos.py accueil" if not e["lanceur_installe"] else
                                                  "adb shell cmd package set-home-activity %s" % lanceur))
    # Ne JAMAIS mettre de DNS privé : l'interface Bbox s'authentifie via mabbox.bytel.fr, qu'un DNS
    # public ne résout pas → « Authentification requise » et plus de chaînes.
    controle("dns", e["dns_prive_ok"], "Pas de DNS privé",
             "" if e["dns_prive_ok"] else "DNS privé « %s » : l'interface Bbox ne peut plus s'authentifier "
                                          "(mabbox.bytel.fr n'est résolu que par le DNS de la box)." % e["dns_prive_hote"],
             "" if e["dns_prive_ok"] else "adb shell settings delete global private_dns_mode ; adb shell settings delete "
                                          "global private_dns_specifier ; puis redémarrez la TV (adb reboot)")
    controle("eligibilite", e["tv_eligible"], "TV éligible (SD ou HD)", e["eligibilite"] or "valeur absente",
             "" if e["tv_eligible"] else "Vérifiez qu'aucun DNS privé n'est défini, que la TV est bien reliée à la "
                                         "Bbox, puis redémarrez la TV.")
    if args.direct:
        info("Lancement du direct TV pour mesurer le débit (environ 15 s)…")
        adb.sh("am", "start", "-a", "android.intent.action.VIEW", "-d", "https://media.bbox.fr/channel/live",
               "-n", "fr.bouyguestelecom.tv.bbui/.ui.live.LiveActivity")
        time.sleep(args.attente)
        interface, debit = mesurer_debit(adb, 3)
        adb.sh("input", "keyevent", "KEYCODE_HOME")
        ok = debit is not None and debit >= 2.0
        controle("direct", ok, "Le direct TV reçoit un flux vidéo",
                 "Débit reçu : %s Mbit/s sur %s (≈13 Mbit/s attendus quand une chaîne joue)"
                 % ("%.1f" % debit if debit is not None else "?", interface or "?"),
                 "" if ok else "Regardez la TV : message « Authentification requise » ? Retirez le DNS privé et "
                               "redémarrez. (Une capture du direct est noire : c'est normal, la vidéo est sur un plan "
                               "matériel.)")
    tout_ok = all(c["ok"] for c in controles)
    if SORTIE_JSON:
        afficher_json({"ok": tout_ok, "controles": controles})
    else:
        for c in controles:
            info("%s %s" % ("✅" if c["ok"] else "❌", c["titre"]))
            if c["detail"] and (not c["ok"] or c["id"] in ("direct", "eligibilite")):
                info("     %s" % c["detail"])
            if c["conseil"] and not c["ok"]:
                info("     → %s" % c["conseil"])
        info("\n%s" % ("Tout est en ordre." if tout_ok else "Des points sont à corriger (voir ci-dessus)."))
    return 0 if tout_ok else 2


# ---- restaurer ----

def choisir_sauvegarde(adb, option):
    if option:
        dossier = Path(option).expanduser()
        if not (dossier.is_dir() and est_sauvegarde(dossier)):
            raise Erreur("%s n'est pas une sauvegarde lolos (packages-desactives.txt et settings-global.txt "
                         "attendus)." % option)
        ident = infos_sauvegarde(dossier).get("appareil")
        if ident and ident != adb.identifiant():
            avertir("Cette sauvegarde a été faite sur une autre TV.")
        return dossier
    liste = sauvegardes_de(adb)
    if not liste:
        raise Erreur("Aucune sauvegarde de cette TV trouvée. Indiquez-la avec --sauvegarde DOSSIER.")
    return liste[0]


def cmd_restaurer(args):
    profil = charger_profil(getattr(args, "profil", None))
    adb = appareil(args)
    dossier = choisir_sauvegarde(adb, args.sauvegarde)
    acc = profil.accueil
    paquet_lanceur = acc.get("paquet", "fr.accueil.tv")
    avant = lire_paquets_sauvegarde(dossier, "packages-desactives.txt")
    tous, maintenant, _ = adb.paquets()
    actions = []
    # 1. Applis : réactiver ce qui est désactivé maintenant mais ne l'était pas (tout, avec --usine),
    #    puis re-désactiver ce qui l'était dans la sauvegarde.
    for pid in sorted(maintenant):
        if args.usine or pid not in avant:
            actions.append({"type": "activer", "id": pid, "nom": profil.nom(pid)})
    if not args.usine:
        for pid in sorted(avant):
            if pid not in maintenant and pid in tous:
                actions.append({"type": "desactiver", "id": pid, "nom": profil.nom(pid)})
    # 2. Réglages : la valeur de la sauvegarde, ou suppression si elle n'y figurait pas.
    valeurs_sauvees = {ns: lire_settings_sauvegarde(dossier, ns) for ns in ("global", "secure", "system")}
    actuels = lire_reglages(adb, profil.reglages_sauvegardes)
    for cle in profil.reglages_sauvegardes:
        ns, nom = cle.split("/", 1)
        voulu = valeurs_sauvees.get(ns, {}).get(nom)
        voulu = None if voulu in (None, "null") else voulu
        if actuels.get(cle) != voulu:
            actions.append({"type": "reglage", "id": cle, "ns": ns, "cle": nom, "valeur": voulu,
                            "avant": actuels.get(cle), "nom": "valeur de la sauvegarde" if voulu is not None else "supprimé"})
    # 3. Écran d'accueil : celui de la sauvegarde (sinon l'accueil Bbox d'origine), lanceur désinstallé.
    accueil = acc.get("accueil_origine", "fr.bouyguestelecom.tv.bbui/.ui.live.LiveActivity")
    fichier_home = dossier / "home-par-defaut.txt"
    if fichier_home.is_file():
        lignes = [l.strip() for l in fichier_home.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        if lignes and "/" in lignes[-1] and not lignes[-1].startswith(paquet_lanceur + "/"):
            accueil = lignes[-1]
    if paquet_lanceur in tous and not args.garder_accueil:
        actions.append({"type": "desinstaller", "id": paquet_lanceur, "nom": "lanceur Accueil"})
    if adb.accueil_actuel() != accueil or (paquet_lanceur in tous and not args.garder_accueil):
        actions.append({"type": "accueil", "id": accueil})

    if not SORTIE_JSON:
        info("Restauration depuis %s (%s)%s :" % (dossier, date_sauvegarde(dossier),
                                                   " — simulation, rien ne sera modifié" if args.simulation else ""))
        if not actions:
            info("  Rien à faire : la TV correspond déjà à la sauvegarde.")
        for i, a in enumerate(actions, 1):
            info("  %2d. %s" % (i, decrire_action(a)))
    if args.simulation or not actions:
        if SORTIE_JSON:
            afficher_json({"simulation": args.simulation, "sauvegarde": str(dossier), "actions": actions})
        return 0
    confirmer(args, "\nRestaurer (%d actions) ?" % len(actions))
    journaliser("== lolos restaurer depuis %s sur %s" % (dossier, adb.serie))
    resultats = executer_plan(adb, actions)
    _, desactives_apres, _ = adb.paquets()
    config = lire_config()
    config.get("desactives_par_lolos", {}).pop(adb.identifiant(), None)
    ecrire_config(config)
    adb.sh("input", "keyevent", "KEYCODE_HOME")
    echecs = [a for a in resultats if not a["ok"]]
    attendu = 0 if args.usine else len(avant & tous)
    if SORTIE_JSON:
        afficher_json({"simulation": False, "resultats": resultats, "nb_desactives": len(desactives_apres),
                       "nb_desactives_attendu": attendu, "accueil": adb.accueil_actuel()})
    else:
        info("\nApplis désactivées : %d (attendu : %d). Accueil : %s" % (len(desactives_apres), attendu, adb.accueil_actuel()))
        info("Restauration terminée%s. Un redémarrage de la TV est conseillé : adb reboot" %
             (" avec %d échecs" % len(echecs) if echecs else ""))
    return 1 if echecs else 0


# --------------------------------------------------------------------------------------------
# Ligne de commande
# --------------------------------------------------------------------------------------------

def options_communes(parseur, defauts):
    """Options globales, acceptées avant OU après la sous-commande."""
    d = (lambda v: v) if defauts else (lambda v: argparse.SUPPRESS)
    g = parseur.add_argument_group("options communes")
    g.add_argument("--ip", default=d(None), metavar="IP", help="adresse IP de la TV (sinon celle mémorisée)")
    g.add_argument("--adb", default=d(None), metavar="CHEMIN", help="chemin de l'exécutable adb à utiliser")
    g.add_argument("--json", action="store_true", default=d(False),
                   help="sortie JSON lisible par un programme (etat, lister, verifier, chercher…)")
    g.add_argument("--profil", default=d(None), metavar="FICHIER", help="profil JSON (défaut : profil/bbox-4k.json)")
    g.add_argument("--oui", action="store_true", default=d(False),
                   help="répondre oui aux confirmations (et installer adb s'il manque)")


def construire_parseur():
    p = argparse.ArgumentParser(
        prog="lolos",
        description="Désencombre une TV Bbox 4K (Android TV) par ADB, de façon réversible, et installe un "
                    "écran d'accueil minimaliste.",
        epilog="Parcours conseillé : chercher → connecter → sauvegarder → lister → appliquer --simulation → "
               "appliquer → accueil → verifier.  Annuler : restaurer.",
        add_help=False)
    p.add_argument("-h", "--help", action="help", help="afficher cette aide et quitter")
    p.add_argument("--version", action="version", version="lolos " + VERSION, help="afficher la version et quitter")
    options_communes(p, True)
    sous = p.add_subparsers(dest="commande", metavar="COMMANDE", title="commandes")

    def commande(nom, aide, description=None):
        sp = sous.add_parser(nom, help=aide, description=description or aide, add_help=False)
        sp.add_argument("-h", "--help", action="help", help="afficher cette aide et quitter")
        options_communes(sp, False)
        return sp

    sp = commande("installer-adb", "télécharger adb (platform-tools officiels Google) dans ~/.lolos")
    sp.add_argument("--forcer", action="store_true", help="télécharger même si adb est déjà disponible")
    sp.set_defaults(fonction=cmd_installer_adb)

    sp = commande("chercher", "trouver la TV sur le réseau local",
                  "Sonde le réseau local (ports 8009 Cast, 6466 télécommande, 5555 ADB). Sur macOS, si Python "
                  "est bloqué par la protection « Réseau local », passe par Bonjour (dns-sd).")
    sp.add_argument("--reseau", metavar="CIDR", help="réseau à sonder (défaut : le /24 de cet ordinateur)")
    sp.set_defaults(fonction=cmd_chercher)

    sp = commande("connecter", "se connecter à la TV en ADB et mémoriser son adresse")
    sp.add_argument("adresse", nargs="?", metavar="IP", help="adresse IP de la TV (ex. 192.168.1.20)")
    sp.set_defaults(fonction=cmd_connecter)

    sp = commande("etat", "afficher l'état de la TV (modèle, mémoire, accueil, version du lanceur, DNS, "
                          "éligibilité, veille profonde…)")
    sp.set_defaults(fonction=cmd_etat)

    sp = commande("sauvegarder", "sauvegarder l'état de la TV (à faire AVANT toute modification)")
    sp.add_argument("--dossier", metavar="D", help="dossier de destination (défaut : ./sauvegardes/AAAA-MM-JJ_HHMMSS)")
    sp.set_defaults(fonction=cmd_sauvegarder)

    sp = commande("lister", "lister les applis de la TV (celles du profil et toutes les autres trouvées)")
    sp.add_argument("--tous", action="store_true", help="inclure aussi les applis absentes de cette TV")
    sp.set_defaults(fonction=cmd_lister)

    sp = commande("appliquer", "désactiver les applis choisies et appliquer les réglages",
                  "Désactive (sans jamais désinstaller) les applis choisies. Télémétrie et pubs : réglages du "
                  "profil. Autres catégories : laissées telles quelles sauf --defauts, --interactif, --desactiver. "
                  "SML est toujours désactivé en premier. Identifiants : paquets ou catégories (ex. adulte,jeux).")
    sp.add_argument("--desactiver", metavar="ID,ID", help="paquets ou catégories à désactiver")
    sp.add_argument("--garder", metavar="ID,ID", help="paquets ou catégories à garder (réactivés si besoin)")
    sp.add_argument("--defauts", action="store_true", help="utiliser les choix par défaut du profil pour tout")
    sp.add_argument("--interactif", action="store_true", help="poser la question appli par appli")
    sp.add_argument("--simulation", action="store_true", help="afficher le plan sans rien modifier")
    sp.add_argument("--sans-sauvegarde", action="store_true", help="ne pas exiger de sauvegarde préalable (déconseillé)")
    sp.set_defaults(fonction=cmd_appliquer)

    sp = commande("remplacer", "remplacer une appli par une meilleure (ex. YouTube → SmartTube, sans pubs)",
                  "Sans argument : liste les remplacements proposés. Avec un nom : télécharge la dernière version "
                  "publiée sur GitHub, l'installe, puis désactive l'appli remplacée (sans la désinstaller).")
    sp.add_argument("quoi", nargs="?", metavar="NOM", help="remplacement à faire (ex. smarttube ou youtube)")
    sp.add_argument("--garder-ancienne", action="store_true", help="installer sans désactiver l'appli remplacée")
    sp.add_argument("--simulation", action="store_true", help="afficher le plan sans rien modifier")
    sp.add_argument("--sans-sauvegarde", action="store_true", help="ne pas exiger de sauvegarde préalable (déconseillé)")
    sp.set_defaults(fonction=cmd_remplacer)

    sp = commande("accueil", "installer le lanceur « Accueil » minimaliste et ses fonds animés")
    sp.add_argument("--apk", metavar="CHEMIN", help="APK du lanceur (défaut : android/Accueil.apk, sinon téléchargé)")
    sp.add_argument("--fonds", metavar="DOSSIER", help="dossier de vidéos .mp4 (défaut : fonds/, sinon fonds.zip)")
    sp.add_argument("--simulation", action="store_true", help="afficher les étapes sans rien modifier")
    sp.add_argument("--sans-sauvegarde", action="store_true", help="ne pas exiger de sauvegarde préalable (déconseillé)")
    sp.set_defaults(fonction=cmd_accueil)

    sp = commande("capture", "faire une capture d'écran de la TV (PNG)")
    sp.add_argument("fichier", metavar="FICHIER.png")
    sp.set_defaults(fonction=cmd_capture)

    sp = commande("verifier", "vérifier que tout est en ordre (code de sortie 2 si un contrôle échoue)")
    sp.add_argument("--direct", action="store_true",
                    help="lancer le direct TV, mesurer le débit reçu puis revenir à l'accueil (visible sur la TV)")
    sp.add_argument("--attente", type=int, default=10, metavar="S", help="secondes d'attente avant la mesure (défaut : 10)")
    sp.set_defaults(fonction=cmd_verifier)

    sp = commande("personnaliser", "options de Lol'OS : message, écran de démarrage, météo, mode nuit…",
                  "Toutes les options sont facultatives : seules celles indiquées changent, les autres gardent "
                  "leur valeur actuelle sur la TV (personnalisation.json, relu puis complété). Sans option, ou "
                  "avec --afficher : montre la configuration actuelle. {moment} devient Bonjour / Bon "
                  "après-midi / Bonsoir. Rien n'est installé : des fichiers sont déposés dans le dossier de "
                  "Lol'OS sur la TV, pris en compte au prochain retour à l'accueil.")
    sp.add_argument("--afficher", action="store_true", help="afficher la configuration actuelle (rien n'est modifié)")
    g = sp.add_argument_group("accueil et écran de démarrage")
    g.add_argument("--message", metavar="TEXTE", help="message d'accueil, ex. \"{moment} Lolo\" (vide = aucun)")
    g.add_argument("--message-demarrage", metavar="TEXTE", help="message de l'écran de démarrage (vide = aucun)")
    g.add_argument("--demarrage", choices=OUI_NON, help="écran de démarrage Lol'OS à l'allumage (défaut : oui)")
    g.add_argument("--sans-demarrage", action="store_true", help="= --demarrage non")
    g.add_argument("--logo", metavar="IMAGE", help="image PNG/JPEG/WebP de l'écran de démarrage")
    g.add_argument("--logo-lolos", action="store_true", help="revenir au logo Lol'OS")
    g = sp.add_argument_group("options facultatives (désactivées par défaut, sauf les paysages)")
    g.add_argument("--nettoyage-memoire", choices=OUI_NON,
                   help="au retour à l'accueil, fermer les applis restées en arrière-plan (sauf TV en direct, "
                        "Chromecast, YouTube)")
    g.add_argument("--infos-systeme", choices=OUI_NON,
                   help="tuile « Infos système » (mémoire, stockage, réseau, température, versions)")
    g.add_argument("--mode-nuit", choices=OUI_NON, help="de 21 h à 7 h : accueil assombri et paysages calmes")
    g.add_argument("--paysages-lolos", choices=OUI_NON,
                   help="afficher les paysages Lol'OS (non : seulement vos fonds, voir la commande « fonds »)")
    m = g.add_mutually_exclusive_group()
    m.add_argument("--meteo", metavar="VILLE",
                   help="météo du jour sous la date (Open-Meteo, sans compte) ; la ville trouvée est affichée "
                        "pour vérification")
    m.add_argument("--sans-meteo", action="store_true", help="retirer la météo")
    sp.add_argument("--apercu", action="store_true", help="montrer tout de suite l'écran de démarrage sur la TV")
    sp.add_argument("--simulation", action="store_true", help="afficher ce qui serait envoyé sans rien modifier")
    sp.set_defaults(fonction=cmd_personnaliser)

    sp = commande("fonds", "vos fonds d'écran (vidéos .mp4, photos .jpg/.png) sur l'accueil Lol'OS",
                  "Ajoute, liste ou retire VOS fonds (fichiers perso-* du dossier des fonds de Lol'OS sur la TV). "
                  "Les paysages Lol'OS ne sont jamais supprimés (pour ne plus les voir : personnaliser "
                  "--paysages-lolos non). Vidéos : H.264 ou H.265, 1080p conseillé, son ignoré. Photos : "
                  "1920×1080 conseillé, animées par un lent zoom. Sans option : liste vos fonds.")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--ajouter", nargs="+", metavar="FICHIER",
                   help="envoyer ces fichiers (renommés perso-<nom>.<ext> ; ignorés s'ils y sont déjà)")
    g.add_argument("--lister", action="store_true", help="lister vos fonds présents sur la TV (par défaut)")
    g.add_argument("--retirer", nargs="+", metavar="NOM",
                   help="retirer ces fonds (nom sur la TV, ex. perso-vacances.jpg, ou nom du fichier d'origine)")
    g.add_argument("--retirer-tout", action="store_true", help="retirer tous vos fonds (perso-*)")
    sp.add_argument("--simulation", action="store_true", help="afficher ce qui serait fait sans rien modifier")
    sp.set_defaults(fonction=cmd_fonds)

    sp = commande("veille", "veille profonde : délai avant que la box s'éteigne vraiment",
                  "Au-delà d'un certain temps en veille, la Bbox s'éteint vraiment (rallumage = démarrage "
                  "complet, ≈1 min). 1h = réglage Bouygues. Sans option : affiche le réglage actuel. Ce réglage "
                  "fait partie de la sauvegarde et « restaurer » le remet.")
    sp.add_argument("--profonde", choices=sorted(VEILLE_PROFONDE),
                    help="s'éteindre vraiment après 1 h (réglage Bouygues), 24 h, ou jamais")
    sp.add_argument("--simulation", action="store_true", help="afficher le plan sans rien modifier")
    sp.add_argument("--sans-sauvegarde", action="store_true", help="ne pas exiger de sauvegarde préalable (déconseillé)")
    sp.set_defaults(fonction=cmd_veille)

    sp = commande("redemarrer", "redémarrer la box (demande confirmation, ou --oui)")
    sp.set_defaults(fonction=cmd_redemarrer)

    sp = commande("restaurer", "remettre la TV dans l'état d'une sauvegarde",
                  "Réactive les applis désactivées depuis la sauvegarde, remet les réglages sauvegardés, "
                  "désinstalle le lanceur Accueil et remet l'accueil Bbox.")
    sp.add_argument("--sauvegarde", metavar="D", help="dossier de sauvegarde (défaut : la plus récente de cette TV)")
    sp.add_argument("--usine", action="store_true", help="réactiver TOUTES les applis désactivées, même celles "
                                                         "désactivées dans la sauvegarde")
    sp.add_argument("--garder-accueil", action="store_true", help="laisser le lanceur installé (l'accueil Bbox "
                                                                  "redevient l'accueil par défaut)")
    sp.add_argument("--simulation", action="store_true", help="afficher le plan sans rien modifier")
    sp.set_defaults(fonction=cmd_restaurer)
    return p


def main(argv=None):
    global SORTIE_JSON
    if os.name == "nt":  # consoles Windows : accents et symboles sans plantage
        for flux in (sys.stdout, sys.stderr):
            try:
                flux.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass
    parseur = construire_parseur()
    args = parseur.parse_args(argv)
    SORTIE_JSON = bool(getattr(args, "json", False))
    if not getattr(args, "commande", None):
        parseur.print_help()
        return 1
    try:
        return args.fonction(args) or 0
    except Erreur as e:
        if SORTIE_JSON:
            afficher_json({"ok": False, "erreur": str(e)})
        else:
            print("❌ " + str(e), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrompu.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
