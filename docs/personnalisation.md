# Format de personnalisation de Lol'OS

Le lanceur (`fr.accueil.tv`) relit sa configuration à chaque retour à l'accueil. L'installateur et le CLI
la déposent par adb dans `/sdcard/Android/data/fr.accueil.tv/files/`. Toutes les options sont **facultatives**
et **désactivées par défaut**.

## `personnalisation.json`

```json
{
  "salutation": "{moment} Lolo",
  "demarrage": true,
  "message_demarrage": "Bienvenue à la maison",
  "duree_demarrage_ms": 2500,
  "nettoyage_memoire": false,
  "infos_systeme": false,
  "mode_nuit": false,
  "fonds_lolos": true,
  "meteo": { "ville": "Paris", "latitude": 48.8534, "longitude": 2.3488 }
}
```

| Clé | Défaut | Effet |
|---|---|---|
| `salutation` | `""` | Message en haut à gauche de l'accueil. `{moment}` → Bonjour / Bon après-midi / Bonsoir. |
| `demarrage` | `true` | Écran de démarrage Lol'OS au premier affichage après l'allumage. |
| `message_demarrage` | `""` | Texte sous le logo de l'écran de démarrage (`{moment}` accepté). |
| `duree_demarrage_ms` | `2500` | Durée de l'écran de démarrage (1000 à 10000). |
| `nettoyage_memoire` | `false` | Au retour à l'accueil, ferme les applis restées en arrière-plan (sauf TV en direct, Chromecast, YouTube). |
| `infos_systeme` | `false` | Ajoute la tuile « Infos système » (mémoire, stockage, réseau, température, versions). |
| `mode_nuit` | `false` | De 21 h à 7 h : accueil assombri et paysages calmes. |
| `fonds_lolos` | `true` | Afficher les paysages Lol'OS. `false` : seulement vos fonds (s'il y en a). |
| `meteo` | `null` | Météo du jour sous la date (Open-Meteo, sans compte). `null` = pas de météo. |

Les clés absentes prennent leur valeur par défaut. Les outils qui modifient une option doivent **relire le
fichier existant** (`adb shell cat …/personnalisation.json`) et ne changer que les clés concernées.

## Fichiers à côté

| Fichier | Rôle |
|---|---|
| `logo-demarrage.png` | Image de l'écran de démarrage (PNG/JPEG, ≤ 1000 px conseillé). Absent = logo Lol'OS. |
| `fonds/perso-*.mp4` | Vos vidéos de fond (H.264 ou H.265, 1080p conseillé, son ignoré). |
| `fonds/perso-*.jpg`, `fonds/perso-*.png` | Vos photos de fond, animées par un lent zoom. 1920×1080 conseillé. |

Les paysages Lol'OS (`fonds/0*.mp4`) ne sont jamais supprimés par ces options.

## Réglage système : veille profonde

Au-delà d'un certain temps en veille, la Bbox s'éteint vraiment (rallumage = démarrage complet, ~1 min).

| Choix | Commandes |
|---|---|
| 1 h (réglage Bouygues) | `settings put global lowpower_control_enabled 1` · `settings put global lowpower_control_delay 3600000` |
| 24 h | `settings put global lowpower_control_enabled 1` · `settings put global lowpower_control_delay 86400000` |
| Jamais | `settings put global lowpower_control_enabled 0` |

Ces deux clés font partie des réglages sauvegardés et restaurés (`reglages_sauvegardes` du profil).

## Météo : trouver les coordonnées d'une ville

`https://geocoding-api.open-meteo.com/v1/search?name=<ville>&count=1&language=fr&format=json` →
`results[0].name`, `latitude`, `longitude`, `country`.
