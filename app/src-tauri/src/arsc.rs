//! Lecture du nom d'une appli dans le `resources.arsc` de son APK (même logique que cli/lolos.py).
//! Android ne donne par adb que l'identifiant de ressource du nom (labelRes=0x7f…) : on le résout ici.

const TABLE: u16 = 0x0002;
const CHAINES: u16 = 0x0001;
const PAQUET: u16 = 0x0200;
const TYPE: u16 = 0x0201;
const REFERENCE: u8 = 0x01;
const CHAINE: u8 = 0x03;

fn u16_a(d: &[u8], o: usize) -> Option<u16> {
    Some(u16::from_le_bytes(d.get(o..o + 2)?.try_into().ok()?))
}

fn u32_a(d: &[u8], o: usize) -> Option<u32> {
    Some(u32::from_le_bytes(d.get(o..o + 4)?.try_into().ok()?))
}

/// Texte de la ressource chaîne `id` (0xPPTTEEEE), en français si l'APK le contient, sinon par défaut.
pub fn libelle(d: &[u8], id: u32) -> Option<String> {
    libelle_niveau(d, id, 0)
}

fn libelle_niveau(d: &[u8], id: u32, profondeur: u8) -> Option<String> {
    if u16_a(d, 0)? != TABLE || profondeur > 3 {
        return None;
    }
    let (pid, tid, eid) = (id >> 24, ((id >> 16) & 0xFF) as u8, (id & 0xFFFF) as usize);
    let mut pool = None;
    // (langue de la configuration, type de valeur, valeur) pour chaque configuration qui définit l'entrée
    let mut valeurs: Vec<([u8; 2], u8, u32)> = Vec::new();
    let fin = d.len().min(u32_a(d, 4)? as usize);
    let mut pos = u16_a(d, 2)? as usize;
    while pos + 8 <= fin {
        let (genre, entete, taille) = (u16_a(d, pos)?, u16_a(d, pos + 2)? as usize, u32_a(d, pos + 4)? as usize);
        if taille < 8 || pos + taille > fin {
            break;
        }
        if genre == CHAINES && pool.is_none() {
            pool = Some(pos);
        } else if genre == PAQUET && u32_a(d, pos + 8)? == pid {
            let mut sous = pos + entete;
            while sous + 8 <= pos + taille {
                let (g, h, t) = (u16_a(d, sous)?, u16_a(d, sous + 2)? as usize, u32_a(d, sous + 4)? as usize);
                if t < 8 {
                    break;
                }
                if g == TYPE && d.get(sous + 8) == Some(&tid) {
                    if let Some((genre_valeur, valeur)) = entree(d, sous, h, eid) {
                        let langue = d.get(sous + 28..sous + 30).and_then(|l| l.try_into().ok()).unwrap_or([0, 0]);
                        valeurs.push((langue, genre_valeur, valeur));
                    }
                }
                sous += t;
            }
        }
        pos += taille;
    }
    let rang = |l: &[u8; 2]| match l {
        b"fr" => 0,
        [0, 0] => 1,
        b"en" => 2,
        _ => 3,
    };
    let (_, genre_valeur, valeur) = valeurs.into_iter().min_by_key(|(l, _, _)| rang(l))?;
    if genre_valeur == REFERENCE {
        return libelle_niveau(d, valeur, profondeur + 1);
    }
    chaine(d, pool?, valeur as usize)
}

/// Valeur (type, donnée) de l'entrée `eid` d'un bloc ResTable_type, si elle existe et est simple.
fn entree(d: &[u8], bloc: usize, entete: usize, eid: usize) -> Option<(u8, u32)> {
    let (drapeaux, nombre, debut) = (*d.get(bloc + 9)?, u32_a(d, bloc + 12)? as usize, u32_a(d, bloc + 16)? as usize);
    let decalage = if drapeaux & 0x01 != 0 {
        // creux : paires (index, décalage / 4) triées
        (0..nombre).find_map(|i| {
            let p = bloc + entete + 4 * i;
            if u16_a(d, p)? as usize == eid {
                u16_a(d, p + 2).map(|o| o as usize * 4)
            } else {
                None
            }
        })?
    } else if eid >= nombre {
        return None;
    } else if drapeaux & 0x02 != 0 {
        let o = u16_a(d, bloc + entete + 2 * eid)?;
        (o != 0xFFFF).then_some(o as usize * 4)?
    } else {
        let o = u32_a(d, bloc + entete + 4 * eid)?;
        (o != u32::MAX).then_some(o as usize)?
    };
    let e = bloc + debut + decalage;
    let drapeaux_entree = u16_a(d, e + 2)?;
    let (genre, valeur) = if drapeaux_entree & 0x08 != 0 {
        ((drapeaux_entree >> 8) as u8, u32_a(d, e + 4)?) // entrée compacte
    } else if drapeaux_entree & 0x01 != 0 {
        return None; // entrée complexe (style, tableau…)
    } else {
        let v = e + u16_a(d, e)? as usize;
        (*d.get(v + 3)?, u32_a(d, v + 4)?)
    };
    matches!(genre, REFERENCE | CHAINE).then_some((genre, valeur))
}

/// Chaîne n° `index` du réservoir de chaînes commençant en `pool` (UTF-8 ou UTF-16).
fn chaine(d: &[u8], pool: usize, index: usize) -> Option<String> {
    if index >= u32_a(d, pool + 8)? as usize {
        return None;
    }
    let mut o = pool + u32_a(d, pool + 20)? as usize + u32_a(d, pool + u16_a(d, pool + 2)? as usize + 4 * index)? as usize;
    if u32_a(d, pool + 16)? & 0x100 != 0 {
        o += if d.get(o)? & 0x80 != 0 { 2 } else { 1 }; // longueur en UTF-16, inutile ici
        let mut n = *d.get(o)? as usize;
        o += 1;
        if n & 0x80 != 0 {
            n = ((n & 0x7F) << 8) | *d.get(o)? as usize;
            o += 1;
        }
        Some(String::from_utf8_lossy(d.get(o..o + n)?).into_owned())
    } else {
        let mut n = u16_a(d, o)? as usize;
        o += 2;
        if n & 0x8000 != 0 {
            n = ((n & 0x7FFF) << 16) | u16_a(d, o)? as usize;
            o += 2;
        }
        let unites: Vec<u16> = d.get(o..o + 2 * n)?.chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]])).collect();
        Some(String::from_utf16_lossy(&unites))
    }
}
