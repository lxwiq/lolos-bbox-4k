# Lol'OS — guide pour Claude

Désencombre une TV « Bbox 4K » de Bouygues Telecom (Android TV 11, Arcadyan, modèle `Bouygtel4K`) par
ADB et installe un écran d'accueil minimaliste. Tout ce qui est affiché à l'utilisateur est **en français**.

## Organisation

| Dossier | Contenu |
|---|---|
| `profil/bbox-4k.json` | **Source unique de vérité** : catégories (`demander`), paquets (`defaut`, `lies`, `cache`, `priorite`), `proteges`, `reglages`, `reglages_sauvegardes`, `accueil`, `avertissements`. Les outils ne codent aucune liste en dur. |
| `cli/lolos.py` | CLI Python ≥ 3.8, bibliothèque standard seule, Windows/macOS/Linux. `--help` pour les commandes. |
| `android/` | Lanceur Lol'OS (`fr.accueil.tv`) : sources, `Accueil.apk`. |
| `app/` | Interface graphique Tauri. |
| `fonds/` | Fonds vidéo animés (`*.mp4`) et crédits. |
| `.claude/skills/lolos/` | Skill qui décrit le parcours complet avec l'utilisateur. |

## Faits établis sur la vraie TV (ne pas « corriger »)

1. Désactiver = `am force-stop` puis `pm disable-user --user 0 <pkg>` ; réactiver = `pm enable`. **Jamais de
   désinstallation**, sauf `fr.accueil.tv` lors d'une restauration.
2. `fr.bouyguestelecom.tv.sml` (« SpyObject ») réactive les applis TV désactivées : toujours en premier
   (`priorite: 0`). Son processus reste en mémoire jusqu'au redémarrage.
3. **Jamais de DNS privé** : l'interface Bbox s'authentifie via `mabbox.bytel.fr`, résolu seulement par le
   DNS de la box → sinon « Authentification requise » et plus de direct. `verifier` le signale.
4. macOS « Réseau local » bloque adb (Homebrew) et python3 vers les IP du réseau local (« No route to
   host », errno 65). Contournements : relais `/usr/bin/nc` sur 127.0.0.1:15555 (pid dans
   `~/.lolos/relais.pid`) et Bonjour via `dns-sd`.
5. `adb exec-out screencap` est corrompu (message de debug du pilote) : passer par `/sdcard/_cap.png` +
   `adb pull`. Le direct est noir sur les captures (plan vidéo matériel) : vérifier le débit wlan0
   (`/proc/net/dev`, ≈13 Mbit/s quand une chaîne joue).
6. La touche TV de la télécommande ouvre toujours le direct ; les bandeaux de l'accueil Bbox viennent des
   serveurs Bouygues.
7. Au réveil, `fr.bouyguestelecom.tv.power` (StandByService → restartEntryPoint) relance l'interface Bbox sauf si
   le libellé d'application au premier plan est dans sa liste blanche (« BBOX TV »…) : le lanceur porte donc
   `android:label="BBOX TV"` au niveau application (activité : « Lol'OS ») et se met devant sur SCREEN_OFF.
8. Redémarrer sans root : broadcast explicite `fr.bouyguestelecom.tv.power/.PowerReceiver`, action
   `fr.bouyguestelecom.tv.power.REBOOT_QUIESCENT` (la box revient en veille) ; `GO_TO_SLEEP` = veille. Ne jamais
   utiliser `MonitorUpdateService` (OTA), `DreamApplyService` (extinction) ni `FactoryResetActivity`.
9. Logo de démarrage Android et message « Bonjour… » de la Bbox : non modifiables sans root (partition système,
   contenu éditorial serveur). L'écran de démarrage Lol'OS (`personnalisation.json` + `logo-demarrage.png`) les remplace.
10. Les applis absentes du profil sont trouvées sur la TV (`cmd package query-activities` LEANBACK_LAUNCHER et
   LAUNCHER + `pm list packages -3`) et rangées dans `decouverte.categorie`, actives par défaut. adb ne donne que
   l'identifiant de ressource du nom (`labelRes=0x7f…`) : le vrai nom est lu dans `resources.arsc`, extrait sur la
   TV (`exec-out unzip -p <apk> resources.arsc`), par `arsc.rs` / `libelle_arsc` (mêmes règles des deux côtés).
11. `remplacements` du profil (SmartTube `org.smarttube.stable` pour YouTube) : dernière version GitHub, APK choisi
   selon `ro.product.cpu.abi` (`armeabi-v7a` sur la Bbox, `universal` sinon), installé AVANT de désactiver l'appli
   remplacée (si l'installation échoue, rien n'est désactivé). YouTube n'est plus protégé.
12. Les favoris de l'accueil Bbox sont dans ses données privées (illisibles sans root). Le ♥ vient de
   `dumpsys usagestats` (≥ 60 s d'utilisation, applis de la liste d'applis seulement) : une appli ♥ n'est jamais
   désactivée d'office (sauf télémétrie et pubs, `demander: false`).

## Règles

- Toute modification de la TV passe par une sauvegarde préalable (`sauvegarder`) ; tout est réversible
  (`restaurer`).
- Ne jamais toucher aux paquets `proteges`, ni `pm clear`, ni forcer l'arrêt de `fr.bouyguestelecom.tv.bbui`.
- Aucune donnée personnelle dans le dépôt : pas de sauvegarde (`sauvegardes/` est ignoré), pas de
  `journal.txt`, pas de SSID, numéro de série, identifiant ou IP réelle (exemples : `192.168.1.x`).
- Ne jamais demander ni saisir les identifiants Bouygues de l'utilisateur.
- Ajouter une appli = l'ajouter au profil JSON, pas dans le code.

## Tester sans risque

```
python3 -m py_compile cli/lolos.py
python3 cli/lolos.py --help                     # et <commande> --help
python3 cli/lolos.py appliquer --simulation …   # plan sans rien modifier
python3 cli/lolos.py restaurer --simulation
```
Commandes en lecture seule sur la TV : `etat`, `lister`, `capture`, `verifier` (sans `--direct`),
`sauvegarder`. Visibles à l'écran : `accueil`, `verifier --direct`, `restaurer`, `appliquer` (applis qui se
ferment) — à ne lancer qu'avec l'accord de l'utilisateur, qui regarde peut-être la TV.

Variables utiles pour les tests : `LOLOS_DOSSIER` (remplace `~/.lolos` : config, relais, adb téléchargé),
`LOLOS_PORT_RELAIS` (port local du relais macOS, 15555 par défaut), `ANDROID_SERIAL` (appareil adb déjà
connecté à utiliser), `--adb CHEMIN` (permet de brancher un faux adb pour tester sans TV).
