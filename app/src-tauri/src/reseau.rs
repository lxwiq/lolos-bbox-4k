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
use std::time::{Duration, Instant};

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

/// Premier contact avec le réseau local au démarrage : sur macOS, il fait apparaître la
/// demande d'autorisation « Réseau local » avant que l'utilisateur n'arrive à la recherche.
pub fn declencher_demande_acces() {
    if let Some(locale) = ip_locale() {
        let [a, b, c, d] = locale.octets();
        let voisin = Ipv4Addr::new(a, b, c, if d == 2 { 3 } else { 2 });
        let _ = TcpStream::connect_timeout(&SocketAddr::new(IpAddr::V4(voisin), 9), Duration::from_millis(300));
    }
}

#[derive(PartialEq, Clone, Copy)]
enum Sonde {
    Ouvert,
    Ferme,
    /// « No route to host » immédiat : refus de macOS (ou adresse marquée injoignable)
    RefusImmediat,
}

fn sonder_direct(ip: Ipv4Addr, port: u16) -> Sonde {
    let debut = Instant::now();
    match TcpStream::connect_timeout(&SocketAddr::new(IpAddr::V4(ip), port), Duration::from_millis(500)) {
        Ok(_) => Sonde::Ouvert,
        Err(e) if MAC && e.raw_os_error() == Some(EHOSTUNREACH_MAC) && debut.elapsed() < Duration::from_millis(60) => {
            Sonde::RefusImmediat
        }
        Err(_) => Sonde::Ferme,
    }
}

static ERREUR_NC_SIGNALEE: AtomicBool = AtomicBool::new(false);

fn sonder_nc(ip: Ipv4Addr, port: u16) -> bool {
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

/// Un balayage du /24 : port 8009 (Cast), 6466 (télécommande Android TV) ou 5555 (ADB).
/// Renvoie les TV trouvées et la part des sondes refusées instantanément.
fn balayer(a: u8, b: u8, c: u8, locale: Ipv4Addr, via_nc: bool) -> (Vec<TvTrouvee>, f32) {
    let passerelles = [Ipv4Addr::new(a, b, c, 1), Ipv4Addr::new(a, b, c, 254)];
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
                let sonde = |port| {
                    if via_nc {
                        if sonder_nc(ip, port) { Sonde::Ouvert } else { Sonde::Ferme }
                    } else {
                        sonder_direct(ip, port)
                    }
                };
                let s_cast = sonde(8009);
                let refus = s_cast == Sonde::RefusImmediat && !passerelles.contains(&ip);
                let cast = s_cast == Sonde::Ouvert;
                let telecommande = !cast && !refus && sonde(6466) == Sonde::Ouvert;
                let adb = !refus && sonde(5555) == Sonde::Ouvert;
                let tv = (cast || telecommande || adb).then(|| TvTrouvee { ip: ip.to_string(), adb, cast, telecommande });
                let _ = tx.send((tv, refus));
            }
        });
    }
    drop(tx);
    let (mut tvs, mut refus, mut total) = (Vec::new(), 0u32, 0u32);
    for (tv, r) in rx {
        total += 1;
        refus += r as u32;
        tvs.extend(tv);
    }
    tvs.sort_by_key(|t| t.ip.parse::<Ipv4Addr>().map(u32::from).unwrap_or(0));
    (tvs, if total == 0 { 0.0 } else { refus as f32 / total as f32 })
}

/// Recherche des TV : en direct d'abord ; si macOS refuse tout (autorisation « Réseau local »
/// absente ou périmée), nouvel essai via /usr/bin/nc.
pub fn chercher() -> Recherche {
    let Some(locale) = ip_locale() else {
        return Recherche { tvs: vec![], reseau: "introuvable".into(), acces_bloque: false };
    };
    let [a, b, c, _] = locale.octets();
    let reseau = format!("{a}.{b}.{c}.0/24");
    crate::adb::tracer("cmd", format!("recherche sur {reseau} (ports 8009, 6466, 5555)"));
    let (mut tvs, part_refus) = balayer(a, b, c, locale, false);
    let bloque = tvs.is_empty() && part_refus > 0.95;
    if bloque {
        crate::adb::tracer("err", format!("{:.0} % des sondes refusées instantanément par macOS : nouvel essai via /usr/bin/nc", part_refus * 100.0));
        tvs = balayer(a, b, c, locale, true).0;
    }
    for t in &tvs {
        crate::adb::tracer("out", format!("{} : cast={} télécommande={} adb={}", t.ip, t.cast, t.telecommande, t.adb));
    }
    crate::adb::tracer("info", format!("{} TV trouvée(s)", tvs.len()));
    let acces_bloque = bloque && tvs.is_empty();
    if acces_bloque {
        crate::adb::tracer("err", "macOS refuse l'accès au réseau local à Lol'OS (autorisation « Réseau local »)");
    }
    Recherche { tvs, reseau, acces_bloque }
}

pub fn port_adb_ouvert(ip: &str) -> Result<bool, String> {
    let ip: Ipv4Addr = ip.parse().map_err(|_| format!("« {ip} » n'est pas une adresse IP"))?;
    Ok(match sonder_direct(ip, 5555) {
        Sonde::Ouvert => true,
        Sonde::RefusImmediat => sonder_nc(ip, 5555),
        Sonde::Ferme => false,
    })
}

/// Diagnostic détaillé d'une adresse : connexion directe et via nc, avec les erreurs exactes.
pub fn diagnostic(ip: &str) -> String {
    let mut r = format!("ip locale : {:?}\n", ip_locale());
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

    if sonder_direct(ip, 5555) == Sonde::RefusImmediat {
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
