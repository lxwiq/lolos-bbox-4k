//! Recherche de la TV sur le réseau local et relais ADB.
//!
//! macOS : la protection « Réseau local » fait échouer les connexions de l'appli et d'adb
//! (avec des erreurs qui varient selon l'état de l'autorisation). /usr/bin/nc, outil d'Apple,
//! n'est pas concerné : sur Mac, on sonde la TV et on relaie adb avec lui.

use serde::Serialize;
use std::io;
use std::net::{IpAddr, Ipv4Addr, SocketAddr, TcpListener, TcpStream, UdpSocket};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

const MAC: bool = cfg!(target_os = "macos");

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
}

static ERREUR_NC_SIGNALEE: AtomicBool = AtomicBool::new(false);

/// Le port répond-il ?
pub fn ouvert(ip: Ipv4Addr, port: u16) -> bool {
    ouvert_delai(ip, port, 1)
}

fn ouvert_delai(ip: Ipv4Addr, port: u16, secondes: u32) -> bool {
    if MAC {
        return match Command::new("/usr/bin/nc")
            .args(["-z", "-G", &secondes.to_string(), &ip.to_string(), &port.to_string()])
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
        };
    }
    TcpStream::connect_timeout(&SocketAddr::new(IpAddr::V4(ip), port), Duration::from_millis(400)).is_ok()
}

/// Appareils présents dans la table ARP du Mac (ceux qui ont répondu récemment sur le réseau).
fn voisins(a: u8, b: u8, c: u8) -> Vec<Ipv4Addr> {
    let sortie = Command::new("/usr/sbin/arp").arg("-an").output().map(|o| o.stdout).unwrap_or_default();
    String::from_utf8_lossy(&sortie)
        .lines()
        .filter(|l| !l.contains("incomplete"))
        .filter_map(|l| l.split(['(', ')']).nth(1)?.parse::<Ipv4Addr>().ok())
        .filter(|ip| ip.octets()[..3] == [a, b, c])
        .collect()
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

/// Sonde le /24 local : port 8009 (Cast), 6466 (télécommande Android TV) ou 5555 (ADB).
pub fn chercher() -> Recherche {
    let Some(locale) = ip_locale() else {
        return Recherche { tvs: vec![], reseau: "introuvable".into() };
    };
    let [a, b, c, _] = locale.octets();
    let (tx, rx) = mpsc::channel();
    let hotes: Vec<u8> = (1..=254).collect();
    // nc lance un processus par sonde. Une appli lancée depuis le Finder n'a droit qu'à
    // 256 fichiers ouverts : au-delà de ~32 sondes simultanées, les lancements échouent.
    for lot in hotes.chunks(if MAC { 8 } else { 16 }) {
        let tx = tx.clone();
        let lot = lot.to_vec();
        thread::spawn(move || {
            for d in lot {
                let ip = Ipv4Addr::new(a, b, c, d);
                if ip == locale {
                    continue;
                }
                let cast = ouvert(ip, 8009);
                let telecommande = !cast && ouvert(ip, 6466);
                let adb = ouvert(ip, 5555);
                if cast || telecommande || adb {
                    let _ = tx.send(TvTrouvee { ip: ip.to_string(), adb, cast, telecommande });
                }
            }
        });
    }
    drop(tx);
    crate::adb::tracer("cmd", format!("recherche sur {a}.{b}.{c}.0/24 (ports 8009, 6466, 5555{})", if MAC { ", via /usr/bin/nc" } else { "" }));
    let mut tvs: Vec<TvTrouvee> = rx.into_iter().collect();
    tvs.sort_by_key(|t| t.ip.parse::<Ipv4Addr>().map(u32::from).unwrap_or(0));
    if tvs.is_empty() && MAC {
        // Seconde passe, plus patiente, sur les appareils réellement présents.
        let liste = voisins(a, b, c);
        crate::adb::tracer("info", format!("seconde passe sur {} appareil(s) de la table ARP", liste.len()));
        for ip in liste {
            let cast = ouvert_delai(ip, 8009, 2);
            let telecommande = !cast && ouvert_delai(ip, 6466, 2);
            let adb = ouvert_delai(ip, 5555, 2);
            if cast || telecommande || adb {
                tvs.push(TvTrouvee { ip: ip.to_string(), adb, cast, telecommande });
            }
        }
    }
    for t in &tvs {
        crate::adb::tracer("out", format!("{} : cast={} télécommande={} adb={}", t.ip, t.cast, t.telecommande, t.adb));
    }
    crate::adb::tracer("info", format!("{} TV trouvée(s)", tvs.len()));
    Recherche { tvs, reseau: format!("{a}.{b}.{c}.0/24") }
}

pub fn port_adb_ouvert(ip: &str) -> Result<bool, String> {
    let ip: Ipv4Addr = ip.parse().map_err(|_| format!("« {ip} » n'est pas une adresse IP"))?;
    Ok(ouvert(ip, 5555))
}

/// Relais 127.0.0.1:<port> → TV:5555 (macOS) : une boucle nc ⇄ nc à travers une FIFO.
/// adb se connecte en local, nc fait le lien avec la TV. Arrêté avec l'appli.
pub struct Relais {
    pub port: u16,
    enfant: Child,
}

impl Drop for Relais {
    fn drop(&mut self) {
        let _ = self.enfant.kill();
    }
}

pub fn demarrer_relais(ip: &str) -> io::Result<Relais> {
    let ip: Ipv4Addr = ip
        .parse()
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "adresse IP invalide"))?;
    let port = TcpListener::bind("127.0.0.1:0")?.local_addr()?.port(); // port libre
    let script = format!(
        "d=$(mktemp -d); f=$d/fifo; mkfifo $f; trap 'rm -rf $d' EXIT; \
         while :; do /usr/bin/nc -l 127.0.0.1 {port} < $f | /usr/bin/nc {ip} 5555 > $f; sleep 0.3; done"
    );
    let enfant = Command::new("/bin/sh")
        .args(["-c", &script])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;
    thread::sleep(Duration::from_millis(400));
    crate::adb::tracer("info", format!("relais /usr/bin/nc 127.0.0.1:{port} → {ip}:5555 (pid {})", enfant.id()));
    Ok(Relais { port, enfant })
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
