//! Profil de la box (profil/bbox-4k.json, embarqué dans l'appli).

use serde::{Deserialize, Serialize};

pub const JSON: &str = include_str!("../../../profil/bbox-4k.json");

#[derive(Deserialize, Serialize, Clone)]
pub struct Categorie {
    pub id: String,
    pub titre: String,
    pub description: String,
    pub demander: bool,
}

#[derive(Deserialize, Serialize, Clone)]
pub struct Paquet {
    pub id: String,
    pub nom: String,
    pub categorie: String,
    pub defaut: String,
    #[serde(default)]
    pub priorite: Option<u8>,
    #[serde(default)]
    pub note: Option<String>,
    #[serde(default)]
    pub lies: Vec<String>,
    #[serde(default)]
    pub cache: bool,
}

#[derive(Deserialize, Serialize, Clone)]
pub struct Proteges {
    pub paquets: Vec<String>,
}

#[derive(Deserialize, Serialize, Clone)]
pub struct Reglage {
    pub ns: String,
    pub cle: String,
    pub valeur: String,
}

#[derive(Deserialize, Serialize, Clone)]
pub struct Accueil {
    pub paquet: String,
    pub activite: String,
    pub ecran_veille: String,
    pub appops: Vec<String>,
    pub dossier_fonds: String,
    pub accueil_origine: String,
}

#[derive(Deserialize, Serialize, Clone)]
pub struct Profil {
    pub nom: String,
    pub categories: Vec<Categorie>,
    pub paquets: Vec<Paquet>,
    pub proteges: Proteges,
    pub reglages: Vec<Reglage>,
    pub reglages_sauvegardes: Vec<String>,
    pub accueil: Accueil,
    pub avertissements: Vec<String>,
}

pub fn charger() -> Profil {
    serde_json::from_str(JSON).expect("profil/bbox-4k.json invalide")
}
