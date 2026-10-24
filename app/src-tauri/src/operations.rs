//! Ce que l'installateur fait sur la TV : état, sauvegarde, désencombrement, accueil,
//! vérification, restauration. Mêmes règles que cli/lolos.py.

use crate::adb;
use crate::profil::Profil;
use base64::Engine;
use serde::Serialize;
use std::collections::{HashMap, HashSet};
use std::io::Cursor;
use std::path::{Path, PathBuf};
use std::thread;
use std::time::Duration;

/// Dépôt GitHub d'où viennent les fonds d'écran (fonds.zip de la dernière version).
pub const DEPOT: &str = "lxwiq/lolos-bbox-4k";
const APK: &[u8] = include_bytes!("../../../android/Accueil.apk");
/// Numéro de version de l'APK embarqué (écrit par android/compiler.sh).
const APK_VERSION: &str = include_str!("../../../android/Accueil.version");

pub fn version_accueil_embarquee() -> u32 {
    APK_VERSION.trim().parse().unwrap_or(0)
}
const DIRECT: &str = "am start -a android.intent.action.VIEW -d https://media.bbox.fr/channel/live \
                      -n fr.bouyguestelecom.tv.bbui/.ui.live.LiveActivity";

pub struct Tv<'a> {
    pub adb: &'a Path,
    pub serie: &'a str,
}

impl Tv<'_> {
    fn sh(&self, cmd: &str) -> Result<String, String> {
        adb::shell(self.adb, self.serie, cmd)
    }
    fn paquets(&self, option: &str) -> Result<Vec<String>, String> {
        adb::paquets(self.adb, self.serie, option)
    }
    fn reglage(&self, ns: &str, cle: &str) -> String {
        self.sh(&format!("settings get {ns} {cle}")).unwrap_or_default().trim().to_string()
    }
    fn accueil_actuel(&self) -> String {
        self.sh("cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME")
            .unwrap_or_default()
            .lines()
            .last()
            .unwrap_or("")
            .trim()
            .to_string()
    }
    /// Débit entrant Wi-Fi en kbit/s sur 3 s : ≈ 13 000 quand une chaîne passe.
    fn debit_kbits(&self) -> u64 {
        let rx = || -> u64 {
            self.sh("cat /proc/net/dev")
                .unwrap_or_default()
                .lines()
                .find(|l| l.trim_start().starts_with("wlan0:") || l.trim_start().starts_with("eth0:"))
                .and_then(|l| l.split(':').nth(1)?.split_whitespace().next()?.parse().ok())
                .unwrap_or(0)
        };
        let a = rx();
        thread::sleep(Duration::from_secs(3));
        rx().saturating_sub(a) * 8 / 3 / 1000
    }
}

#[derive(Serialize)]
pub struct EtatTv {
    pub modele: String,
    pub android: String,
    pub memoire_dispo_mo: u64,
    pub desactives: usize,
    pub accueil: String,
    pub accueil_installe: bool,
    /// versionCode de l'accueil installé sur la TV, et celui que l'installateur peut installer
    pub accueil_version: Option<u32>,
    pub accueil_disponible: u32,
    pub eligibilite: String,
    pub dns_prive: String,
    pub sml_desactive: bool,
    pub est_bbox: bool,
}

pub fn etat(tv: &Tv, profil: &Profil) -> Result<EtatTv, String> {
    let props = tv.sh("getprop ro.product.model; getprop ro.build.version.release; getprop ro.product.manufacturer")?;
    let mut l = props.lines().map(str::trim);
    let modele = l.next().unwrap_or("").to_string();
    let android = l.next().unwrap_or("").to_string();
    let fabricant = l.next().unwrap_or("").to_string();
    let memoire = tv
        .sh("grep MemAvailable /proc/meminfo")?
        .split_whitespace()
        .nth(1)
        .and_then(|k| k.parse::<u64>().ok())
        .unwrap_or(0)
        / 1024;
    let desactives = tv.paquets("-d")?;
    let installes = tv.paquets("")?;
    Ok(EtatTv {
        est_bbox: modele.contains("Bouygtel") || fabricant.eq_ignore_ascii_case("Arcadyan"),
        modele,
        android,
        memoire_dispo_mo: memoire,
        sml_desactive: desactives.iter().any(|p| p == "fr.bouyguestelecom.tv.sml"),
        desactives: desactives.len(),
        accueil: tv.accueil_actuel(),
        accueil_installe: installes.contains(&profil.accueil.paquet),
        accueil_version: tv
            .sh(&format!("dumpsys package {} | grep -m1 versionCode", profil.accueil.paquet))
            .ok()
            .and_then(|l| l.split_whitespace().find_map(|m| m.strip_prefix("versionCode=")?.parse().ok())),
        accueil_disponible: version_accueil_embarquee(),
        eligibilite: tv.reglage("secure", "current_eligibility"),
        dns_prive: tv.reglage("global", "private_dns_mode"),
    })
}

/// Copie de l'état de la TV avant toute modification (même format que cli/lolos.py).
pub fn sauvegarder(tv: &Tv, dossier_racine: &Path) -> Result<PathBuf, String> {
    let dossier = dossier_racine.join(chrono::Local::now().format("%Y-%m-%d_%H%M%S").to_string());
    std::fs::create_dir_all(&dossier).map_err(|e| e.to_string())?;
    let fichiers: [(&str, &str); 12] = [
        ("getprop.txt", "getprop"),
        ("packages-tous.txt", "pm list packages -u -f"),
        ("packages-desactives.txt", "pm list packages -d"),
        ("packages-actives.txt", "pm list packages -e"),
        ("packages-systeme.txt", "pm list packages -s"),
        ("packages-tiers.txt", "pm list packages -3"),
        ("settings-global.txt", "settings list global"),
        ("settings-secure.txt", "settings list secure"),
        ("settings-system.txt", "settings list system"),
        ("home-par-defaut.txt", "cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME"),
        ("home-candidats.txt", "cmd package query-activities --brief -a android.intent.action.MAIN -c android.intent.category.HOME"),
        ("applis-lanceables-tv.txt", "cmd package query-activities --brief -a android.intent.action.MAIN -c android.intent.category.LEANBACK_LAUNCHER"),
    ];
    for (nom, cmd) in fichiers {
        std::fs::write(dossier.join(nom), tv.sh(cmd)?).map_err(|e| e.to_string())?;
    }
    let _ = capture_png(tv, &dossier.join("capture-avant.png"));
    Ok(dossier)
}

pub fn sauvegardes(dossier_racine: &Path) -> Vec<String> {
    let mut v: Vec<String> = std::fs::read_dir(dossier_racine)
        .map(|it| {
            it.flatten()
                .filter(|e| e.path().join("packages-desactives.txt").is_file())
                .map(|e| e.path().to_string_lossy().to_string())
                .collect()
        })
        .unwrap_or_default();
    v.sort();
    v.reverse();
    v
}

#[derive(Serialize)]
pub struct PaquetTv {
    pub id: String,
    pub nom: String,
    pub categorie: String,
    pub defaut: String,
    pub note: Option<String>,
    pub cache: bool,
    pub present: bool,
    pub desactive: bool,
}

pub fn paquets(tv: &Tv, profil: &Profil) -> Result<Vec<PaquetTv>, String> {
    let tous: HashSet<String> = tv.paquets("-u")?.into_iter().collect();
    let off: HashSet<String> = tv.paquets("-d")?.into_iter().collect();
    Ok(profil
        .paquets
        .iter()
        .map(|p| PaquetTv {
            id: p.id.clone(),
            nom: p.nom.clone(),
            categorie: p.categorie.clone(),
            defaut: p.defaut.clone(),
            note: p.note.clone(),
            cache: p.cache,
            present: tous.contains(&p.id),
            desactive: off.contains(&p.id),
        })
        .collect())
}

/// Désactive (sans désinstaller) les paquets demandés, `sml` en premier, puis applique les réglages.
pub fn appliquer(
    tv: &Tv,
    profil: &Profil,
    demandes: &[String],
    simulation: bool,
    journal: &dyn Fn(String),
) -> Result<usize, String> {
    let proteges: HashSet<&str> = profil.proteges.paquets.iter().map(String::as_str).collect();
    let mut liste: Vec<String> = Vec::new();
    for id in demandes {
        if !liste.contains(id) {
            liste.push(id.clone());
        }
        if let Some(p) = profil.paquets.iter().find(|p| &p.id == id) {
            for l in &p.lies {
                if !liste.contains(l) {
                    liste.push(l.clone());
                }
            }
        }
    }
    // SpyObject (dans sml) réactive les applis désactivées : il passe en premier.
    let priorite = |id: &String| profil.paquets.iter().find(|p| &p.id == id).and_then(|p| p.priorite).unwrap_or(9);
    liste.sort_by_key(|id| priorite(id));

    let installes: HashSet<String> = tv.paquets("-u")?.into_iter().collect();
    let off: HashSet<String> = tv.paquets("-d")?.into_iter().collect();
    let mut faits = 0;
    for id in &liste {
        if proteges.contains(id.as_str()) {
            journal(format!("⚠️ {id} est protégé (chaînes, système) : ignoré"));
            continue;
        }
        if !installes.contains(id) || off.contains(id) {
            continue;
        }
        if simulation {
            journal(format!("(simulation) désactiver {id}"));
        } else {
            let _ = tv.sh(&format!("am force-stop {id}"));
            tv.sh(&format!("pm disable-user --user 0 {id}"))?;
            journal(format!("✓ désactivé : {id}"));
        }
        faits += 1;
    }
    for r in &profil.reglages {
        if simulation {
            journal(format!("(simulation) settings put {} {} {}", r.ns, r.cle, r.valeur));
        } else {
            tv.sh(&format!("settings put {} {} {}", r.ns, r.cle, r.valeur))?;
            journal(format!("✓ réglage {} = {}", r.cle, r.valeur));
        }
    }
    Ok(faits)
}

/// Installe l'écran d'accueil, envoie les paysages, en fait l'accueil et l'écran de veille par défaut.
pub fn installer_accueil(
    tv: &Tv,
    profil: &Profil,
    dossier_appli: &Path,
    avec_fonds: bool,
    journal: &dyn Fn(String),
) -> Result<(), String> {
    let a = &profil.accueil;
    // Dossier privé de l'appli, pas le /tmp partagé : personne d'autre ne peut remplacer l'APK.
    let apk = dossier_appli.join("Accueil.apk");
    std::fs::write(&apk, APK).map_err(|e| e.to_string())?;
    journal("Installation de l'écran d'accueil…".into());
    let installer = || adb::executer(tv.adb, &["-s", tv.serie, "install", "-r", &apk.to_string_lossy()]);
    if let Err(e) = installer() {
        if !(e.contains("UPDATE_INCOMPATIBLE") || e.contains("signatures do not match")) {
            return Err(e);
        }
        // Version déjà installée signée par une autre clé : on retire NOTRE lanceur seulement,
        // puis on le réinstalle (les paysages sont renvoyés juste après).
        journal("Ancienne version signée différemment : réinstallation de l'accueil…".into());
        adb::executer(tv.adb, &["-s", tv.serie, "uninstall", &a.paquet])?;
        installer()?;
    }
    for op in &a.appops {
        // SYSTEM_ALERT_WINDOW : permet à la tuile TV de relancer le direct pendant que la Bbox démarre
        tv.sh(&format!("appops set {} {op} allow", a.paquet))?;
    }
    if avec_fonds {
        match fonds_locaux(dossier_appli, journal) {
            Ok(videos) => {
                tv.sh(&format!("mkdir -p {}", a.dossier_fonds))?;
                for v in videos {
                    let nom = v.file_name().unwrap().to_string_lossy().to_string();
                    let distant = format!("{}/{nom}", a.dossier_fonds);
                    let taille = std::fs::metadata(&v).map(|m| m.len().to_string()).unwrap_or_default();
                    if tv.sh(&format!("stat -c %s {distant}")).unwrap_or_default().trim() == taille {
                        continue;
                    }
                    journal(format!("Envoi du paysage {nom}…"));
                    adb::executer(tv.adb, &["-s", tv.serie, "push", &v.to_string_lossy(), &distant])?;
                }
            }
            Err(e) => journal(format!("⚠️ Paysages indisponibles ({e}) : l'accueil utilisera un dégradé.")),
        }
    }
    tv.sh(&format!("cmd package set-home-activity {}", a.activite))?;
    tv.sh(&format!("settings put secure screensaver_components {}", a.ecran_veille))?;
    tv.sh("settings put secure screensaver_enabled 1")?;
    tv.sh("input keyevent KEYCODE_HOME")?;
    journal("✓ Nouvel écran d'accueil installé".into());
    Ok(())
}

/// Vidéos en cache dans le dossier de l'appli, sinon téléchargées depuis la dernière version GitHub.
fn fonds_locaux(dossier_appli: &Path, journal: &dyn Fn(String)) -> Result<Vec<PathBuf>, String> {
    let cache = dossier_appli.join("fonds");
    let lister = || -> Vec<PathBuf> {
        let mut v: Vec<PathBuf> = std::fs::read_dir(&cache)
            .map(|it| it.flatten().map(|e| e.path()).filter(|p| p.extension().is_some_and(|x| x == "mp4")).collect())
            .unwrap_or_default();
        v.sort();
        v
    };
    if lister().is_empty() {
        journal("Téléchargement des paysages (≈ 110 Mo)…".into());
        let url = format!("https://github.com/{DEPOT}/releases/latest/download/fonds.zip");
        let octets = adb::telecharger_octets(&url)?;
        std::fs::create_dir_all(&cache).map_err(|e| e.to_string())?;
        let mut archive = zip::ZipArchive::new(Cursor::new(octets)).map_err(|e| e.to_string())?;
        archive.extract(&cache).map_err(|e| e.to_string())?;
    }
    let videos = lister();
    if videos.is_empty() {
        Err("archive vide".into())
    } else {
        Ok(videos)
    }
}

/// `exec-out screencap` est pollué par un message du pilote Marvell : on passe par /sdcard.
pub fn capture_png(tv: &Tv, destination: &Path) -> Result<Vec<u8>, String> {
    tv.sh("screencap -p /sdcard/_cap.png")?;
    adb::executer(tv.adb, &["-s", tv.serie, "pull", "/sdcard/_cap.png", &destination.to_string_lossy()])?;
    let _ = tv.sh("rm -f /sdcard/_cap.png");
    std::fs::read(destination).map_err(|e| e.to_string())
}

pub fn capture_data_url(tv: &Tv, dossier_appli: &Path) -> Result<String, String> {
    let png = capture_png(tv, &dossier_appli.join("capture.png"))?;
    Ok(format!("data:image/png;base64,{}", base64::engine::general_purpose::STANDARD.encode(png)))
}

/// Ouvre le direct, mesure le flux, puis revient à l'accueil.
pub fn tester_direct(tv: &Tv) -> Result<u64, String> {
    tv.sh(DIRECT)?;
    thread::sleep(Duration::from_secs(8));
    tv.sh(DIRECT)?; // l'interface Bbox ignore la demande pendant son démarrage
    thread::sleep(Duration::from_secs(6));
    let debit = tv.debit_kbits();
    tv.sh("input keyevent KEYCODE_HOME")?;
    Ok(debit)
}

fn lire_reglages(fichier: &Path) -> HashMap<String, String> {
    std::fs::read_to_string(fichier)
        .unwrap_or_default()
        .lines()
        .filter_map(|l| l.split_once('=').map(|(k, v)| (k.to_string(), v.to_string())))
        .collect()
}

/// Remet la TV dans l'état d'une sauvegarde.
pub fn restaurer(
    tv: &Tv,
    profil: &Profil,
    sauvegarde: &Path,
    usine: bool,
    garder_accueil: bool,
    journal: &dyn Fn(String),
) -> Result<(), String> {
    let avant: HashSet<String> = std::fs::read_to_string(sauvegarde.join("packages-desactives.txt"))
        .map_err(|e| format!("sauvegarde illisible : {e}"))?
        .lines()
        .filter_map(|l| l.trim().strip_prefix("package:").map(str::to_string))
        .collect();
    let maintenant: HashSet<String> = tv.paquets("-d")?.into_iter().collect();
    for p in &maintenant {
        if usine || !avant.contains(p) {
            tv.sh(&format!("pm enable {p}"))?;
            journal(format!("✓ réactivé : {p}"));
        }
    }
    if !usine {
        for p in avant.difference(&maintenant) {
            tv.sh(&format!("pm disable-user --user 0 {p}"))?;
            journal(format!("✓ redésactivé (comme avant) : {p}"));
        }
    }
    let mut par_ns: HashMap<String, HashMap<String, String>> = HashMap::new();
    for cle in &profil.reglages_sauvegardes {
        let Some((ns, nom)) = cle.split_once('/') else { continue };
        let valeurs = par_ns
            .entry(ns.to_string())
            .or_insert_with(|| lire_reglages(&sauvegarde.join(format!("settings-{ns}.txt"))));
        match valeurs.get(nom) {
            Some(v) => tv.sh(&format!("settings put {ns} {nom} '{}'", v.replace('\'', "")))?,
            None => tv.sh(&format!("settings delete {ns} {nom}"))?,
        };
    }
    journal("✓ réglages d'origine remis".into());
    if !garder_accueil && tv.paquets("")?.contains(&profil.accueil.paquet) {
        adb::executer(tv.adb, &["-s", tv.serie, "uninstall", &profil.accueil.paquet])?;
        journal("✓ écran d'accueil « Accueil » retiré".into());
    }
    tv.sh(&format!("cmd package set-home-activity {}", profil.accueil.accueil_origine))?;
    tv.sh("input keyevent KEYCODE_HOME")?;
    journal("✓ accueil Bbox d'origine rétabli — un redémarrage de la TV est conseillé".into());
    Ok(())
}

fn dossier_lolos(profil: &Profil) -> String {
    profil.accueil.dossier_fonds.trim_end_matches("/fonds").to_string()
}

/// Configuration actuelle de Lol'OS sur la TV (objet vide si aucune).
pub fn lire_personnalisation(tv: &Tv, profil: &Profil) -> serde_json::Value {
    tv.sh(&format!("cat {}/personnalisation.json 2>/dev/null", dossier_lolos(profil)))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .filter(|v: &serde_json::Value| v.is_object())
        .unwrap_or_else(|| serde_json::json!({}))
}

/// Dépose personnalisation.json (+ logo) — format dans docs/personnalisation.md.
/// `logo` : None = inchangé, Some(vide) = revenir au logo Lol'OS, Some(png) = nouvelle image.
pub fn personnaliser(
    tv: &Tv,
    profil: &Profil,
    dossier_appli: &Path,
    config: &serde_json::Value,
    logo: Option<Vec<u8>>,
    apercu: bool,
    journal: &dyn Fn(String),
) -> Result<(), String> {
    if !config.is_object() {
        return Err("configuration invalide".into());
    }
    let distant = dossier_lolos(profil);
    let local = dossier_appli.join("personnalisation.json");
    std::fs::write(&local, serde_json::to_vec_pretty(config).unwrap()).map_err(|e| e.to_string())?;
    tv.sh(&format!("mkdir -p {distant}"))?;
    adb::executer(tv.adb, &["-s", tv.serie, "push", &local.to_string_lossy(), &format!("{distant}/personnalisation.json")])?;
    match logo {
        Some(png) if png.is_empty() => {
            tv.sh(&format!("rm -f {distant}/logo-demarrage.png"))?;
        }
        Some(png) => {
            let fichier = dossier_appli.join("logo-demarrage.png");
            std::fs::write(&fichier, png).map_err(|e| e.to_string())?;
            adb::executer(tv.adb, &["-s", tv.serie, "push", &fichier.to_string_lossy(), &format!("{distant}/logo-demarrage.png")])?;
        }
        None => {}
    }
    journal("✓ Personnalisation envoyée à la TV (appliquée au retour à l'accueil)".into());
    if apercu {
        tv.sh(&format!("am start -n {} --ez fr.accueil.tv.APERCU_DEMARRAGE true", profil.accueil.activite))?;
        journal("Aperçu de l'écran de démarrage sur la TV".into());
    }
    Ok(())
}

#[derive(Serialize)]
pub struct Fond {
    pub nom: String,
    pub taille_mo: f64,
}

/// Vos fonds (perso-*) présents sur la TV.
pub fn lister_fonds(tv: &Tv, profil: &Profil) -> Result<Vec<Fond>, String> {
    let sortie = tv.sh(&format!("ls -l {}/ 2>/dev/null", profil.accueil.dossier_fonds))?;
    Ok(sortie
        .lines()
        .filter_map(|l| {
            let champs: Vec<&str> = l.split_whitespace().collect();
            let nom = champs.last()?;
            if !nom.starts_with("perso-") {
                return None;
            }
            let taille: f64 = champs.get(4)?.parse().ok()?;
            Some(Fond { nom: nom.to_string(), taille_mo: (taille / 1_048_576.0 * 10.0).round() / 10.0 })
        })
        .collect())
}

/// Nom de fichier sur la TV : perso-<nom sans accents ni espaces>.<ext>
fn nom_fond(chemin: &Path) -> Option<String> {
    let ext = chemin.extension()?.to_string_lossy().to_lowercase();
    if !["mp4", "jpg", "jpeg", "png"].contains(&ext.as_str()) {
        return None;
    }
    let base: String = chemin
        .file_stem()?
        .to_string_lossy()
        .chars()
        .map(|c| match c {
            'à' | 'â' | 'ä' | 'À' | 'Â' => 'a',
            'é' | 'è' | 'ê' | 'ë' | 'É' | 'È' => 'e',
            'î' | 'ï' => 'i',
            'ô' | 'ö' => 'o',
            'ù' | 'û' | 'ü' => 'u',
            'ç' => 'c',
            c if c.is_ascii_alphanumeric() => c.to_ascii_lowercase(),
            _ => '-',
        })
        .collect();
    Some(format!("perso-{}.{ext}", base.trim_matches('-')))
}

pub fn envoyer_fonds(tv: &Tv, profil: &Profil, chemins: &[PathBuf], journal: &dyn Fn(String)) -> Result<usize, String> {
    let dossier = &profil.accueil.dossier_fonds;
    tv.sh(&format!("mkdir -p {dossier}"))?;
    let mut envoyes = 0;
    for chemin in chemins {
        let Some(nom) = nom_fond(chemin) else {
            journal(format!("⚠️ Ignoré (format non pris en charge) : {}", chemin.display()));
            continue;
        };
        journal(format!("Envoi de {nom}…"));
        adb::executer(tv.adb, &["-s", tv.serie, "push", &chemin.to_string_lossy(), &format!("{dossier}/{nom}")])?;
        envoyes += 1;
    }
    journal(format!("✓ {envoyes} fond(s) envoyé(s)"));
    Ok(envoyes)
}

pub fn retirer_fonds(tv: &Tv, profil: &Profil, noms: &[String]) -> Result<(), String> {
    for nom in noms {
        // Seulement vos fonds, et seulement la forme produite par nom_fond() :
        // perso-[a-z0-9._-]+.(mp4|jpg|jpeg|png) — rien qui puisse sortir de la commande shell.
        let valide = nom.strip_prefix("perso-").and_then(|reste| reste.rsplit_once('.')).is_some_and(|(base, ext)| {
            !base.is_empty()
                && base.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || "-_.".contains(c))
                && ["mp4", "jpg", "jpeg", "png"].contains(&ext)
        });
        if !valide {
            return Err(format!("nom de fond refusé : {nom}"));
        }
        tv.sh(&format!("rm -f -- {}/{nom}", profil.accueil.dossier_fonds))?;
    }
    Ok(())
}

/// Veille profonde : « 1h » (Bouygues), « 24h » ou « jamais ».
pub fn regler_veille(tv: &Tv, mode: &str, journal: &dyn Fn(String)) -> Result<(), String> {
    match mode {
        "1h" | "24h" => {
            let delai = if mode == "1h" { 3_600_000 } else { 86_400_000 };
            tv.sh("settings put global lowpower_control_enabled 1")?;
            tv.sh(&format!("settings put global lowpower_control_delay {delai}"))?;
        }
        "jamais" => {
            tv.sh("settings put global lowpower_control_enabled 0")?;
        }
        _ => return Err(format!("mode de veille inconnu : {mode}")),
    }
    journal(format!("✓ Veille profonde : {mode}"));
    Ok(())
}

pub fn lire_veille(tv: &Tv) -> String {
    let actif = tv.sh("settings get global lowpower_control_enabled").unwrap_or_default();
    let delai = tv.sh("settings get global lowpower_control_delay").unwrap_or_default();
    match (actif.trim(), delai.trim()) {
        ("0", _) => "jamais".into(),
        (_, "86400000") => "24h".into(),
        _ => "1h".into(),
    }
}

/// Coordonnées d'une ville (géocodage Open-Meteo, sans compte).
pub fn geocoder(ville: &str) -> Result<serde_json::Value, String> {
    let encode: String = ville
        .trim()
        .bytes()
        .map(|b| if b.is_ascii_alphanumeric() { (b as char).to_string() } else { format!("%{b:02X}") })
        .collect();
    let url = format!("https://geocoding-api.open-meteo.com/v1/search?name={encode}&count=1&language=fr&format=json");
    let octets = adb::telecharger_octets(&url)?;
    let v: serde_json::Value = serde_json::from_slice(&octets).map_err(|e| e.to_string())?;
    let r = v["results"].get(0).ok_or(format!("ville introuvable : {ville}"))?;
    Ok(serde_json::json!({
        "ville": r["name"],
        "pays": r["country"],
        "region": r["admin1"],
        "latitude": r["latitude"],
        "longitude": r["longitude"],
    }))
}

/// Redémarre la box. `adb reboot` attend que la TV disparaisse (et bloque parfois à travers
/// le relais) : on le lance, puis on ne l'attend pas plus de quelques secondes.
pub fn redemarrer(tv: &Tv) -> Result<(), String> {
    adb::tracer("cmd", format!("adb -s {} reboot", tv.serie));
    let mut enfant = std::process::Command::new(tv.adb)
        .args(["-s", tv.serie, "reboot"])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|e| e.to_string())?;
    for _ in 0..16 {
        if let Ok(Some(_)) = enfant.try_wait() {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(500));
    }
    let _ = enfant.kill();
    Ok(())
}
