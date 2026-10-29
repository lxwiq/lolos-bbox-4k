//! Localisation, téléchargement et exécution d'adb.

use std::io::{Cursor, Read};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::OnceLock;

/// Traceur du journal en direct (défini par l'appli au démarrage) : niveau + texte.
type Traceur = Box<dyn Fn(&str, String) + Send + Sync>;
static TRACEUR: OnceLock<Traceur> = OnceLock::new();

pub fn definir_traceur(f: Traceur) {
    let _ = TRACEUR.set(f);
}

pub fn tracer(niveau: &str, texte: impl Into<String>) {
    if let Some(f) = TRACEUR.get() {
        f(niveau, texte.into());
    }
}

/// Sortie raccourcie pour le journal.
fn extrait(texte: &str) -> String {
    let t = texte.trim();
    if t.chars().count() > 800 {
        format!("{}… ({} lignes)", t.chars().take(800).collect::<String>(), t.lines().count())
    } else {
        t.to_string()
    }
}

const EXE: &str = if cfg!(windows) { "adb.exe" } else { "adb" };

fn platform_tools_url() -> &'static str {
    if cfg!(target_os = "windows") {
        "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
    } else if cfg!(target_os = "macos") {
        "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip"
    } else {
        "https://dl.google.com/android/repository/platform-tools-latest-linux.zip"
    }
}

/// Cherche adb : copie téléchargée par l'appli, PATH, puis emplacements habituels du SDK.
pub fn trouver(dossier_appli: &Path) -> Option<PathBuf> {
    let mut candidats = vec![dossier_appli.join("platform-tools").join(EXE)];
    if let Some(path) = std::env::var_os("PATH") {
        candidats.extend(std::env::split_paths(&path).map(|d| d.join(EXE)));
    }
    for var in ["ANDROID_HOME", "ANDROID_SDK_ROOT"] {
        if let Some(sdk) = std::env::var_os(var) {
            candidats.push(PathBuf::from(sdk).join("platform-tools").join(EXE));
        }
    }
    if let Some(home) = std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE")) {
        let home = PathBuf::from(home);
        candidats.push(home.join("Library/Android/sdk/platform-tools").join(EXE));
        candidats.push(home.join("Android/Sdk/platform-tools").join(EXE));
        candidats.push(home.join("AppData/Local/Android/Sdk/platform-tools").join(EXE));
    }
    candidats.push(PathBuf::from("/opt/homebrew/bin").join(EXE));
    candidats.push(PathBuf::from("/usr/local/bin").join(EXE));
    candidats.into_iter().find(|p| p.is_file())
}

/// Télécharge les platform-tools officiels de Google dans le dossier de l'appli.
pub fn telecharger(dossier_appli: &Path) -> Result<PathBuf, String> {
    let octets = telecharger_octets(platform_tools_url())?;
    std::fs::create_dir_all(dossier_appli).map_err(|e| e.to_string())?;
    let mut archive = zip::ZipArchive::new(Cursor::new(octets)).map_err(|e| e.to_string())?;
    archive.extract(dossier_appli).map_err(|e| e.to_string())?;
    let adb = dossier_appli.join("platform-tools").join(EXE);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&adb, std::fs::Permissions::from_mode(0o755));
    }
    if adb.is_file() {
        Ok(adb)
    } else {
        Err("adb introuvable dans l'archive téléchargée".into())
    }
}

pub fn telecharger_octets(url: &str) -> Result<Vec<u8>, String> {
    tracer("cmd", format!("téléchargement {url}"));
    let mut reponse = ureq::get(url).call().map_err(|e| format!("{url} : {e}"))?;
    let mut octets = Vec::new();
    reponse
        .body_mut()
        .as_reader()
        .read_to_end(&mut octets)
        .map_err(|e| e.to_string())?;
    Ok(octets)
}

fn commande(adb: &Path) -> Command {
    #[cfg_attr(not(windows), allow(unused_mut))]
    let mut c = Command::new(adb);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        c.creation_flags(0x0800_0000); // CREATE_NO_WINDOW : pas de console qui clignote
    }
    c
}

/// Lance adb et renvoie la sortie standard (retours chariot Windows retirés).
pub fn executer(adb: &Path, args: &[&str]) -> Result<String, String> {
    tracer("cmd", format!("adb {}", args.join(" ")));
    let sortie = commande(adb).args(args).output().map_err(|e| {
        tracer("err", format!("impossible de lancer adb : {e}"));
        e.to_string()
    })?;
    let texte = String::from_utf8_lossy(&sortie.stdout).replace('\r', "");
    if sortie.status.success() {
        if !texte.trim().is_empty() {
            tracer("out", extrait(&texte));
        }
        Ok(texte)
    } else {
        let erreur = format!("{}{}", texte, String::from_utf8_lossy(&sortie.stderr)).trim().to_string();
        tracer("err", extrait(&erreur));
        Err(erreur)
    }
}

/// Commande shell sur la TV.
pub fn shell(adb: &Path, serie: &str, cmd: &str) -> Result<String, String> {
    executer(adb, &["-s", serie, "shell", cmd])
}

/// Sortie binaire d'une commande sur la TV (`exec-out` : octets bruts, sans pseudo-terminal).
pub fn octets(adb: &Path, serie: &str, cmd: &str) -> Result<Vec<u8>, String> {
    tracer("cmd", format!("adb -s {serie} exec-out {cmd}"));
    let sortie = commande(adb).args(["-s", serie, "exec-out", cmd]).output().map_err(|e| e.to_string())?;
    if sortie.status.success() {
        tracer("out", format!("{} octets", sortie.stdout.len()));
        Ok(sortie.stdout)
    } else {
        let erreur = String::from_utf8_lossy(&sortie.stderr).trim().to_string();
        tracer("err", extrait(&erreur));
        Err(erreur)
    }
}

/// Paquets renvoyés par `pm list packages <option>`.
pub fn paquets(adb: &Path, serie: &str, option: &str) -> Result<Vec<String>, String> {
    Ok(shell(adb, serie, &format!("pm list packages {option}"))?
        .lines()
        .filter_map(|l| l.strip_prefix("package:"))
        .map(|p| p.trim().to_string())
        .collect())
}
