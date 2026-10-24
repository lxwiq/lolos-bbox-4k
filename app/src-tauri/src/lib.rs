//! Installateur graphique de Lol'OS : commandes appelées par l'interface (src/).

mod adb;
mod operations;
mod profil;
mod reseau;

use operations::Tv;
use serde::Serialize;
use std::io::Write;
use std::path::PathBuf;
use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager, State};

#[derive(Default)]
struct Session {
    adb: Option<PathBuf>,
    serie: Option<String>,
    relais: Option<(String, reseau::Relais)>,
}

struct Appli {
    session: Mutex<Session>,
    profil: profil::Profil,
    dossier: PathBuf,
}

impl Appli {
    /// adb et numéro de série de la TV connectée.
    fn contexte(&self) -> Result<(PathBuf, String), String> {
        let s = self.session.lock().unwrap();
        let adb = s.adb.clone().ok_or("adb n'est pas encore prêt")?;
        let serie = s.serie.clone().ok_or("aucune TV connectée")?;
        Ok((adb, serie))
    }
}

/// Message de progression (résumé affiché dans la barre du journal), aussi tracé en détail.
fn journal(app: &AppHandle, _dossier: &PathBuf, msg: String) {
    let _ = app.emit("journal", &msg);
    adb::tracer("info", msg);
}

#[derive(Serialize, Clone)]
struct Ligne {
    heure: String,
    niveau: String,
    texte: String,
}

/// Journal en direct : chaque commande adb, sa réponse, les erreurs → interface + journal.txt.
fn installer_traceur(app: AppHandle, dossier: PathBuf) {
    let fichier = Mutex::new(
        std::fs::OpenOptions::new().create(true).append(true).open(dossier.join("journal.txt")).ok(),
    );
    adb::definir_traceur(Box::new(move |niveau, texte| {
        let heure = chrono::Local::now().format("%H:%M:%S%.3f").to_string();
        if let Some(f) = fichier.lock().unwrap().as_mut() {
            let _ = writeln!(f, "{} [{niveau}] {texte}", chrono::Local::now().format("%F %T"));
        }
        let _ = app.emit("log", Ligne { heure, niveau: niveau.to_string(), texte });
    }));
}

async fn en_fond<T: Send + 'static>(f: impl FnOnce() -> Result<T, String> + Send + 'static) -> Result<T, String> {
    tauri::async_runtime::spawn_blocking(f).await.map_err(|e| e.to_string())?
}

#[tauri::command]
fn profil(etat: State<Appli>) -> profil::Profil {
    etat.profil.clone()
}

#[derive(Serialize)]
struct EtatAdb {
    trouve: bool,
    chemin: Option<String>,
    version: Option<String>,
    systeme: &'static str,
}

#[tauri::command]
async fn etat_adb(etat: State<'_, Appli>) -> Result<EtatAdb, String> {
    let chemin = adb::trouver(&etat.dossier);
    let version = chemin
        .as_ref()
        .and_then(|c| adb::executer(c, &["version"]).ok())
        .and_then(|v| v.lines().next().map(str::to_string));
    etat.session.lock().unwrap().adb = chemin.clone();
    Ok(EtatAdb {
        trouve: chemin.is_some(),
        chemin: chemin.map(|c| c.to_string_lossy().to_string()),
        version,
        systeme: std::env::consts::OS,
    })
}

#[tauri::command]
async fn installer_adb(etat: State<'_, Appli>) -> Result<String, String> {
    let dossier = etat.dossier.clone();
    let chemin = en_fond(move || adb::telecharger(&dossier)).await?;
    etat.session.lock().unwrap().adb = Some(chemin.clone());
    Ok(chemin.to_string_lossy().to_string())
}

#[tauri::command]
async fn chercher_tv() -> Result<reseau::Recherche, String> {
    en_fond(|| Ok(reseau::chercher())).await
}

/// Connexion ADB. Renvoie l'état adb : « device », « unauthorized » ou « offline ».
#[tauri::command]
async fn connecter(ip: String, etat: State<'_, Appli>) -> Result<String, String> {
    let ip = ip.trim().to_string();
    let adb = etat.session.lock().unwrap().adb.clone().ok_or("adb n'est pas encore prêt")?;
    let ip_sonde = ip.clone();
    if !en_fond(move || reseau::port_adb_ouvert(&ip_sonde)).await? {
        return Err(format!(
            "La TV {ip} ne répond pas sur le port ADB (5555) : vérifiez l'adresse et activez le débogage (voir les étapes)."
        ));
    }
    let serie = if cfg!(target_os = "macos") {
        // adb (binaire tiers) passe par un relais local : macOS ne le laisse pas joindre la TV.
        let mut s = etat.session.lock().unwrap();
        let port = match &s.relais {
            Some((meme_ip, relais)) if *meme_ip == ip => relais.port,
            _ => {
                let relais = reseau::demarrer_relais(&ip).map_err(|e| e.to_string())?;
                let port = relais.port;
                s.relais = Some((ip.clone(), relais));
                port
            }
        };
        format!("127.0.0.1:{port}")
    } else {
        format!("{ip}:5555")
    };
    let (a, s) = (adb.clone(), serie.clone());
    en_fond(move || adb::executer(&a, &["connect", &s])).await?;
    etat.session.lock().unwrap().serie = Some(serie.clone());
    // La TV met parfois quelques secondes à passer de « offline » à « device » / « unauthorized ».
    for _ in 0..6 {
        let s = statut(adb.clone(), serie.clone()).await?;
        if s != "offline" {
            return Ok(s);
        }
        pause_une_seconde().await;
    }
    statut(adb, serie).await
}

async fn pause_une_seconde() {
    let _ = tauri::async_runtime::spawn_blocking(|| std::thread::sleep(std::time::Duration::from_secs(1))).await;
}

async fn statut(adb: PathBuf, serie: String) -> Result<String, String> {
    en_fond(move || {
        let sortie = adb::executer(&adb, &["devices"])?;
        Ok(sortie
            .lines()
            .find(|l| l.starts_with(&serie))
            .and_then(|l| l.split_whitespace().nth(1))
            .unwrap_or("offline")
            .to_string())
    })
    .await
}

#[tauri::command]
async fn statut_connexion(etat: State<'_, Appli>) -> Result<String, String> {
    let (adb, serie) = etat.contexte()?;
    statut(adb, serie).await
}

#[tauri::command]
async fn etat_tv(etat: State<'_, Appli>) -> Result<operations::EtatTv, String> {
    let (adb, serie) = etat.contexte()?;
    let profil = etat.profil.clone();
    en_fond(move || operations::etat(&Tv { adb: &adb, serie: &serie }, &profil)).await
}

#[tauri::command]
async fn sauvegarder(etat: State<'_, Appli>) -> Result<String, String> {
    let (adb, serie) = etat.contexte()?;
    let racine = etat.dossier.join("sauvegardes");
    en_fond(move || {
        operations::sauvegarder(&Tv { adb: &adb, serie: &serie }, &racine).map(|p| p.to_string_lossy().to_string())
    })
    .await
}

#[tauri::command]
fn sauvegardes(etat: State<Appli>) -> Vec<String> {
    operations::sauvegardes(&etat.dossier.join("sauvegardes"))
}

#[tauri::command]
async fn paquets(etat: State<'_, Appli>) -> Result<Vec<operations::PaquetTv>, String> {
    let (adb, serie) = etat.contexte()?;
    let profil = etat.profil.clone();
    en_fond(move || operations::paquets(&Tv { adb: &adb, serie: &serie }, &profil)).await
}

#[tauri::command]
async fn appliquer(
    desactiver: Vec<String>,
    simulation: bool,
    app: AppHandle,
    etat: State<'_, Appli>,
) -> Result<usize, String> {
    let (adb, serie) = etat.contexte()?;
    let (profil, dossier) = (etat.profil.clone(), etat.dossier.clone());
    en_fond(move || {
        let j = |m: String| journal(&app, &dossier, m);
        operations::appliquer(&Tv { adb: &adb, serie: &serie }, &profil, &desactiver, simulation, &j)
    })
    .await
}

#[tauri::command]
async fn installer_accueil(fonds: bool, app: AppHandle, etat: State<'_, Appli>) -> Result<(), String> {
    let (adb, serie) = etat.contexte()?;
    let (profil, dossier) = (etat.profil.clone(), etat.dossier.clone());
    en_fond(move || {
        let j = |m: String| journal(&app, &dossier, m);
        operations::installer_accueil(&Tv { adb: &adb, serie: &serie }, &profil, &dossier, fonds, &j)
    })
    .await
}

#[tauri::command]
async fn capture(etat: State<'_, Appli>) -> Result<String, String> {
    let (adb, serie) = etat.contexte()?;
    en_fond(move || operations::capture_data_url(&Tv { adb: &adb, serie: &serie })).await
}

#[tauri::command]
async fn tester_direct(etat: State<'_, Appli>) -> Result<u64, String> {
    let (adb, serie) = etat.contexte()?;
    en_fond(move || operations::tester_direct(&Tv { adb: &adb, serie: &serie })).await
}

#[tauri::command]
async fn restaurer(
    sauvegarde: String,
    usine: bool,
    garder_accueil: bool,
    app: AppHandle,
    etat: State<'_, Appli>,
) -> Result<(), String> {
    let (adb, serie) = etat.contexte()?;
    let (profil, dossier) = (etat.profil.clone(), etat.dossier.clone());
    en_fond(move || {
        let j = |m: String| journal(&app, &dossier, m);
        operations::restaurer(
            &Tv { adb: &adb, serie: &serie },
            &profil,
            &PathBuf::from(sauvegarde),
            usine,
            garder_accueil,
            &j,
        )
    })
    .await
}

#[tauri::command]
async fn diagnostic_reseau(ip: String) -> Result<String, String> {
    en_fond(move || Ok(reseau::diagnostic(&ip))).await
}

#[tauri::command]
fn dossier_donnees(etat: State<Appli>) -> String {
    etat.dossier.to_string_lossy().to_string()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let dossier = app.path().app_data_dir()?;
            std::fs::create_dir_all(&dossier)?;
            installer_traceur(app.handle().clone(), dossier.clone());
            adb::tracer("info", format!("Lol'OS {} démarré — données dans {}", env!("CARGO_PKG_VERSION"), dossier.display()));
            app.manage(Appli { session: Mutex::new(Session::default()), profil: profil::charger(), dossier });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            profil,
            etat_adb,
            installer_adb,
            chercher_tv,
            connecter,
            statut_connexion,
            etat_tv,
            sauvegarder,
            sauvegardes,
            paquets,
            appliquer,
            installer_accueil,
            capture,
            tester_direct,
            restaurer,
            dossier_donnees,
            diagnostic_reseau
        ])
        .run(tauri::generate_context!())
        .expect("erreur au lancement de l'application");
}

/// Essai sur une vraie TV, sans rien modifier :
/// `BBOX_SERIE=192.168.1.x:5555 cargo test -- --ignored --nocapture`
#[cfg(test)]
mod essais {
    use super::*;

    #[test]
    #[ignore]
    fn lecture_seule_sur_la_tv() {
        let serie = std::env::var("BBOX_SERIE").expect("BBOX_SERIE non défini");
        let adb = adb::trouver(&std::env::temp_dir()).expect("adb introuvable");
        let tv = Tv { adb: &adb, serie: &serie };
        let profil = profil::charger();

        let e = operations::etat(&tv, &profil).unwrap();
        println!("{} Android {} — {} Mo libres — {} désactivées — accueil {} — sml désactivé {} — dns {}",
            e.modele, e.android, e.memoire_dispo_mo, e.desactives, e.accueil, e.sml_desactive, e.dns_prive);

        let paquets = operations::paquets(&tv, &profil).unwrap();
        let presents = paquets.iter().filter(|p| p.present).count();
        let off = paquets.iter().filter(|p| p.desactive).count();
        println!("profil : {} paquets, {presents} présents, {off} déjà désactivés", paquets.len());

        let defauts: Vec<String> = paquets.iter().filter(|p| p.defaut == "desactiver").map(|p| p.id.clone()).collect();
        let n = operations::appliquer(&tv, &profil, &defauts, true, &|m| println!("  {m}")).unwrap();
        println!("simulation : {n} paquet(s) à désactiver");

        let dossier = std::env::temp_dir().join("lolos-essai-sauvegardes");
        let s = operations::sauvegarder(&tv, &dossier).unwrap();
        println!("sauvegarde : {} ({} fichiers)", s.display(), std::fs::read_dir(&s).unwrap().count());
        assert!(operations::capture_png(&tv).unwrap().starts_with(&[0x89, b'P', b'N', b'G']));
        println!("capture : PNG valide");
    }
}

#[cfg(test)]
mod essais_reseau {
    /// `cargo test recherche -- --ignored --nocapture` : sonde le réseau local (aucun accès ADB).
    #[test]
    #[ignore]
    fn recherche() {
        let r = super::reseau::chercher();
        println!("réseau {}", r.reseau);
        for tv in &r.tvs {
            println!("  {} adb={} cast={} télécommande={}", tv.ip, tv.adb, tv.cast, tv.telecommande);
        }
        assert!(!r.tvs.is_empty(), "aucune TV trouvée");
    }
}
