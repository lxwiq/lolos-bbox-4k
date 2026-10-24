//! Recherche de la TV sur le réseau local et relais ADB.
//!
//! macOS : sans l'autorisation « Réseau local », les connexions de l'appli vers le réseau
//! local échouent (« No route to host »). Autorisée, l'appli se connecte elle-même. Sinon on
//! se rabat sur /usr/bin/nc, outil d'Apple qui passe dans certains contextes (terminal).
//! adb (binaire tiers) ne joint jamais la TV directement sur Mac : il passe par un relais local.

use serde::Serialize;
use std::io;
use std::net::{IpAddr, Ipv4Addr, Shutdown, SocketAddr, TcpListener, TcpStream, UdpSocket};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::thread;
use std::time::Duration;

const MAC: bool = cfg!(target_os = "macos");
const EHOSTUNREACH_MAC: i32 = 65;

#[derive(Serialize, Clone)]
pub struct TvTrouvee {
    pub ip: String,
    pub adb: bool,
    pub cast: bool,
    pub telecommande: bool,
}

#[derive(Serialize)]
pub struct Recherche {
    pub tvs: Vec<TvTrouvee>,
    pub reseau: String,
    /// macOS refuse l'accès au réseau local à l'appli (autorisation « Réseau local » absente)
    pub acces_bloque: bool,
}

/// Adresse IPv4 de l'ordinateur sur le réseau local (aucun paquet n'est envoyé).
pub fn ip_locale() -> Option<Ipv4Addr> {
    let s = UdpSocket::bind("0.0.0.0:0").ok()?;
    s.connect("8.8.8.8:80").ok()?;
    match s.local_addr().ok()?.ip() {
        IpAddr::V4(ip) => Some(ip),
        _ => None,
    }
}

/// L'appli a-t-elle le droit de joindre le réseau local ? macOS laisse toujours passer la box,
/// on sonde donc une autre adresse : un refus de macOS tombe instantanément (« No route to
/// host » sans même chercher l'appareil), alors qu'une adresse vide met du temps à échouer.
/// Sur macOS, ce premier essai déclenche aussi la demande d'autorisation.
pub fn acces_reseau_local() -> bool {
    if !MAC {
        return true;
    }
    let Some(locale) = ip_locale() else { return true };
    let [a, b, c, d] = locale.octets();
    let sonde = if d == 2 { 3 } else { 2 };
    let debut = std::time::Instant::now();
    match TcpStream::connect_timeout(&SocketAddr::new(IpAddr::V4(Ipv4Addr::new(a, b, c, sonde)), 9), Duration::from_millis(300)) {
        Err(e) if e.raw_os_error() == Some(EHOSTUNREACH_MAC) => debut.elapsed() > Duration::from_millis(150),
        _ => true,
    }
}

static ERREUR_NC_SIGNALEE: AtomicBool = AtomicBool::new(false);

/// Le port répond-il ? `via_nc` : passer par /usr/bin/nc (appli bloquée par macOS).
fn ouvert(ip: Ipv4Addr, port: u16, via_nc: bool) -> bool {
    if !via_nc {
        return TcpStream::connect_timeout(&SocketAddr::new(IpAddr::V4(ip), port), Duration::from_millis(500)).is_ok();
    }
    match Command::new("/usr/bin/nc")
        .args(["-z", "-G", "1", &ip.to_string(), &port.to_string()])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
    {
        Ok(s) => s.success(),
        Err(e) => {
            if !ERREUR_NC_SIGNALEE.swap(true, Ordering::Relaxed) {
                crate::adb::tracer("err", format!("impossible de lancer /usr/bin/nc : {e}"));
            }
            false
        }
    }
}

/// Sonde le /24 local : port 8009 (Cast), 6466 (télécommande Android TV) ou 5555 (ADB).
pub fn chercher() -> Recherche {
    let Some(locale) = ip_locale() else {
        return Recherche { tvs: vec![], reseau: "introuvable".into(), acces_bloque: false };
    };
    let [a, b, c, _] = locale.octets();
    let via_nc = !acces_reseau_local();
    crate::adb::tracer(
        "cmd",
        format!("recherche sur {a}.{b}.{c}.0/24 (ports 8009, 6466, 5555{})", if via_nc { ", via /usr/bin/nc" } else { "" }),
    );
    let (tx, rx) = mpsc::channel();
    let hotes: Vec<u8> = (1..=254).collect();
    // Avec nc, un processus par sonde : une appli lancée depuis le Finder n'a droit qu'à
    // 256 fichiers ouverts, donc pas plus de ~32 sondes simultanées.
    for lot in hotes.chunks(if via_nc { 8 } else { 4 }) {
        let tx = tx.clone();
        let lot = lot.to_vec();
        thread::spawn(move || {
            for d in lot {
                let ip = Ipv4Addr::new(a, b, c, d);
                if ip == locale {
                    continue;
                }
                let cast = ouvert(ip, 8009, via_nc);
                let telecommande = !cast && ouvert(ip, 6466, via_nc);
                let adb = ouvert(ip, 5555, via_nc);
                if cast || telecommande || adb {
                    let _ = tx.send(TvTrouvee { ip: ip.to_string(), adb, cast, telecommande });
                }
            }
        });
    }
    drop(tx);
    let mut tvs: Vec<TvTrouvee> = rx.into_iter().collect();
    tvs.sort_by_key(|t| t.ip.parse::<Ipv4Addr>().map(u32::from).unwrap_or(0));
    for t in &tvs {
        crate::adb::tracer("out", format!("{} : cast={} télécommande={} adb={}", t.ip, t.cast, t.telecommande, t.adb));
    }
    crate::adb::tracer("info", format!("{} TV trouvée(s)", tvs.len()));
    let acces_bloque = via_nc && tvs.is_empty();
    if acces_bloque {
        crate::adb::tracer("err", "macOS refuse l'accès au réseau local à Lol'OS (autorisation « Réseau local »)");
    }
    Recherche { tvs, reseau: format!("{a}.{b}.{c}.0/24"), acces_bloque }
}

pub fn port_adb_ouvert(ip: &str) -> Result<bool, String> {
    let ip: Ipv4Addr = ip.parse().map_err(|_| format!("« {ip} » n'est pas une adresse IP"))?;
    Ok(ouvert(ip, 5555, !acces_reseau_local()))
}

/// Diagnostic détaillé d'une adresse : connexion directe et via nc, avec les erreurs exactes.
pub fn diagnostic(ip: &str) -> String {
    let mut r = format!("ip locale : {:?}\naccès au réseau local : {}\n", ip_locale(), acces_reseau_local());
    let Ok(adresse) = ip.parse::<Ipv4Addr>() else { return r + "adresse invalide\n" };
    for port in [8009u16, 5555] {
        let direct = match TcpStream::connect_timeout(&SocketAddr::new(IpAddr::V4(adresse), port), Duration::from_secs(2)) {
            Ok(_) => "ouvert".to_string(),
            Err(e) => format!("erreur {:?} (os {:?}) {e}", e.kind(), e.raw_os_error()),
        };
        let nc = Command::new("/usr/bin/nc")
            .args(["-v", "-z", "-G", "2", ip, &port.to_string()])
            .output()
            .map(|o| format!("code {:?} : {}", o.status.code(), String::from_utf8_lossy(&o.stderr).trim()))
            .unwrap_or_else(|e| format!("lancement impossible : {e}"));
        r += &format!("port {port}\n  direct : {direct}\n  nc     : {nc}\n");
    }
    r
}

/// Relais 127.0.0.1:<port> → TV:5555 pour adb sur Mac. Fait par l'appli si macOS l'autorise,
/// sinon par une boucle nc ⇄ nc à travers une FIFO. Arrêté avec l'appli.
pub struct Relais {
    pub port: u16,
    arret: Arc<AtomicBool>,
    enfant: Option<Child>,
}

impl Drop for Relais {
    fn drop(&mut self) {
        self.arret.store(true, Ordering::Relaxed);
        if let Some(e) = self.enfant.as_mut() {
            let _ = e.kill();
        }
    }
}

pub fn demarrer_relais(ip: &str) -> io::Result<Relais> {
    let ip: Ipv4Addr = ip
        .parse()
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "adresse IP invalide"))?;
    let cible = SocketAddr::new(IpAddr::V4(ip), 5555);
    let ecoute = TcpListener::bind("127.0.0.1:0")?;
    let port = ecoute.local_addr()?.port();
    let arret = Arc::new(AtomicBool::new(false));

    if !acces_reseau_local() {
        drop(ecoute);
        let script = format!(
            "d=$(mktemp -d); f=$d/fifo; mkfifo $f; trap 'rm -rf $d' EXIT; \
             while :; do /usr/bin/nc -l 127.0.0.1 {port} < $f | /usr/bin/nc {ip} 5555 > $f; sleep 0.3; done"
        );
        let enfant = Command::new("/bin/sh").args(["-c", &script]).stdout(Stdio::null()).stderr(Stdio::null()).spawn()?;
        thread::sleep(Duration::from_millis(400));
        crate::adb::tracer("info", format!("relais /usr/bin/nc 127.0.0.1:{port} → {ip}:5555 (pid {})", enfant.id()));
        return Ok(Relais { port, arret, enfant: Some(enfant) });
    }

    let fin = arret.clone();
    thread::spawn(move || {
        for client in ecoute.incoming().flatten() {
            if fin.load(Ordering::Relaxed) {
                break;
            }
            thread::spawn(move || {
                let Ok(tv) = TcpStream::connect_timeout(&cible, Duration::from_secs(5)) else { return };
                let (Ok(mut c_lu), Ok(mut tv_ecrit)) = (client.try_clone(), tv.try_clone()) else { return };
                let aller = thread::spawn(move || {
                    let _ = io::copy(&mut c_lu, &mut tv_ecrit);
                    let _ = tv_ecrit.shutdown(Shutdown::Write);
                });
                let (mut tv_lu, mut c_ecrit) = (tv, client);
                let _ = io::copy(&mut tv_lu, &mut c_ecrit);
                let _ = c_ecrit.shutdown(Shutdown::Write);
                let _ = aller.join();
            });
        }
    });
    crate::adb::tracer("info", format!("relais interne 127.0.0.1:{port} → {ip}:5555"));
    Ok(Relais { port, arret, enfant: None })
}
