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

/// Où ranger les applis de la TV que le profil ne connaît pas.
#[derive(Deserialize, Serialize, Clone)]
pub struct Decouverte {
    pub categorie: String,
}

impl Default for Decouverte {
    fn default() -> Self {
        Decouverte { categorie: "autres".into() }
    }
}

/// Appli à installer à la place d'une autre (ex. SmartTube pour YouTube), publiée sur GitHub.
#[derive(Deserialize, Serialize, Clone)]
pub struct Remplacement {
    pub paquet: String,
    pub nom: String,
    pub remplace: String,
    pub description: String,
    #[serde(default)]
    pub avertissement: Option<String>,
    /// dépôt GitHub « propriétaire/projet »
    pub depot: String,
    /// début du nom de l'APK ; la fin est « _<processeur>.apk » ou « _universal.apk »
    pub fichier: String,
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
    #[serde(default)]
    pub remplacements: Vec<Remplacement>,
    #[serde(default)]
    pub decouverte: Decouverte,
    pub proteges: Proteges,
    pub reglages: Vec<Reglage>,
    pub reglages_sauvegardes: Vec<String>,
    pub accueil: Accueil,
    pub avertissements: Vec<String>,
}

pub fn charger() -> Profil {
    serde_json::from_str(JSON).expect("profil/bbox-4k.json invalide")
}
