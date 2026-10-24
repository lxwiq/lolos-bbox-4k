---
name: lolos
description: Désencombrer et accélérer une TV Bbox 4K de Bouygues Telecom (ou une autre Android TV) par ADB avec cli/lolos.py — couper pubs, télémétrie et applis inutilisées, installer l'écran d'accueil minimaliste « Accueil », vérifier que la TV en direct marche encore, ou tout restaurer. À utiliser quand l'utilisateur veut alléger, nettoyer, accélérer sa Bbox / son décodeur / son Android TV, retirer les pubs ou recommandations, remplacer l'accueil Bbox, ou revenir à l'état d'origine.
---

# Désencombrer une Bbox 4K avec lolos

Tout passe par `python3 cli/lolos.py` (Python ≥ 3.8, sans dépendance), lancé **depuis la racine du
dépôt** : les sauvegardes vont dans `./sauvegardes/` et le journal dans `./journal.txt`.
Ajoutez `--json` pour lire les résultats (`etat`, `lister`, `verifier`, `chercher`, `appliquer --simulation`…).
Votre outil Bash n'a pas de terminal interactif : les commandes qui modifient la TV (`appliquer`,
`restaurer`) exigent `--oui`, à ne passer **qu'après l'accord explicite de l'utilisateur**.
N'utilisez jamais `--interactif` : posez les questions vous-même (étape 5).

Parlez à l'utilisateur en français, simplement : c'est sa TV de salon, pas un appareil de test.

## Règles absolues

- **Toujours sauvegarder avant de modifier** (`sauvegarder`). L'outil refuse d'appliquer sans sauvegarde ;
  n'utilisez pas `--sans-sauvegarde`.
- **Jamais de désinstallation** : on désactive seulement (`pm disable-user`), c'est réversible. Seul notre
  lanceur `fr.accueil.tv` est désinstallé, et uniquement par `restaurer`.
- **Jamais de DNS privé** (AdGuard, NextDNS, dnsforge…) : l'interface Bbox s'authentifie via
  `mabbox.bytel.fr`, que seul le DNS de la box résout. Avec un DNS privé : « Authentification requise »
  et plus de chaînes. Si `verifier` le signale : `adb shell settings delete global private_dns_mode`,
  `adb shell settings delete global private_dns_specifier`, puis redémarrer la TV.
- **SML d'abord** : `fr.bouyguestelecom.tv.sml` contient « SpyObject », qui réactive les applis TV
  désactivées. `appliquer` le désactive toujours en premier ; son processus continue de tourner
  jusqu'au prochain redémarrage, c'est normal.
- Ne touchez jamais aux paquets protégés (`proteges` du profil : interface Bbox `bbui`, chaînes, DRM,
  télécommande, mises à jour, services Google…), pas de `pm clear`, pas de `am force-stop` sur
  `fr.bouyguestelecom.tv.bbui`, pas de modification du dock du lanceur.
- **Ne demandez ni ne tapez jamais les identifiants Bouygues** (compte, mot de passe, code parental) de
  l'utilisateur. Si la TV en demande, c'est à lui de les saisir avec la télécommande.
- Prévenez avant toute action visible sur l'écran (`accueil`, `verifier --direct`, `restaurer`) : quelqu'un
  regarde peut-être la TV.
- Ne publiez rien de personnel : les sauvegardes contiennent réseau Wi-Fi, comptes et identifiants ;
  elles restent sur l'ordinateur de l'utilisateur.

## Déroulé

### 1. Trouver la TV
```
python3 cli/lolos.py chercher
```
Si adb manque, l'outil affiche les commandes d'installation ; proposez `python3 cli/lolos.py installer-adb`
(téléchargement officiel Google, ~7 Mo, sans droits administrateur). Sur macOS, Python et adb sont souvent
bloqués par la protection « Réseau local » : `chercher` passe alors par Bonjour (`dns-sd`) et `connecter`
par un relais `/usr/bin/nc` sur 127.0.0.1:15555, automatiquement. Si aucune TV n'est trouvée, demandez
l'IP lue sur la TV (Paramètres → Réseau et Internet).

### 2. Activer le débogage (si « ADB : fermé »)
Guidez l'utilisateur pas à pas, une étape à la fois, et attendez qu'il confirme :
Paramètres → Préférences de l'appareil → À propos → appuyer 7 fois sur « Build » → retour →
Options pour les développeurs → activer « Débogage USB » (et « Débogage réseau » s'il existe).

### 3. Se connecter
```
python3 cli/lolos.py connecter 192.168.1.x
```
Prévenez d'abord : « Une fenêtre “Autoriser le débogage ?” va s'afficher sur la TV : cochez “Toujours
autoriser” puis OK. » L'outil attend jusqu'à 60 s.

### 4. Sauvegarder (TOUJOURS) puis faire l'inventaire
```
python3 cli/lolos.py sauvegarder
python3 cli/lolos.py etat --json
python3 cli/lolos.py lister --json
```
Signalez tout de suite un DNS privé, une TV non éligible ou une RAM libre très basse.

### 5. Demander ce que l'utilisateur utilise
Les catégories `demander: false` (télémétrie, pubs) sont toujours coupées : annoncez-le simplement.
Pour chaque catégorie `demander: true` qui a au moins une appli présente (`lister --json`, applis non
`cache`), utilisez l'outil **AskUserQuestion** :
- une question par catégorie, `multiSelect: true`, « Lesquelles de ces applis utilisez-vous ? » ;
- **4 options au maximum par question** (au-delà, découpez : « Streaming (1/2) », « Streaming (2/2) ») et
  4 questions au maximum par appel (faites plusieurs appels si besoin) ;
- libellé = nom de l'appli, description = sa note ou son état (« actuellement désactivée ») ;
- rien de coché = aucune appli utilisée dans la catégorie.

Mettez ces mises en garde dans la description des options concernées :
- **Replay** : l'onglet « Replay » de la Bbox ouvre ces applis ; désactiver TF1+ supprime le replay de TF1.
  Les chaînes en direct restent disponibles dans tous les cas.
- **Assistant Google / recherche vocale** (`com.google.android.katniss`) : c'est le bouton micro de la
  télécommande.
- **Chromecast intégré** (`com.google.android.apps.mediashell`) : nécessaire pour caster depuis un téléphone.
- **Paiement Cashlog** : nécessaire pour louer des films (VOD).
- **Assistance à distance Bouygues** : permet à un technicien de prendre la main sur la TV.

Applis cochées → `--garder`, non cochées → `--desactiver`. Les applis liées (module Netflix, webapp Canal+,
Pleio jeux…) suivent automatiquement leur appli principale : ne les listez pas.

### 6. Simuler, montrer le plan, obtenir l'accord
```
python3 cli/lolos.py appliquer --simulation --desactiver ID,ID,… --garder ID,ID,…
```
Les identifiants peuvent être des paquets ou des catégories entières (`adulte`, `jeux`). Montrez le plan en
clair (ce qui sera désactivé, réactivé, les réglages d'animation) et demandez la confirmation.

### 7. Appliquer
```
python3 cli/lolos.py appliquer --desactiver … --garder … --oui
```
Si l'outil signale des applis « réactivées toutes seules », SML tournait encore : redémarrez la TV
(`adb reboot`, avec l'accord de l'utilisateur) puis relancez la même commande.

### 8. Écran d'accueil minimaliste (proposé, pas imposé)
Expliquez : accueil épuré façon Apple TV (tuiles des applis gardées, fonds vidéo animés, écran de veille) ;
la touche TV de la télécommande ouvre toujours le direct ; les bandeaux de recommandations de l'accueil
Bbox viennent des serveurs Bouygues et ne peuvent pas être retirés autrement.
```
python3 cli/lolos.py accueil
```

### 9. Vérifier
```
python3 cli/lolos.py capture /tmp/bbox-apres.png
```
puis **lisez le PNG avec l'outil Read** pour voir l'écran et décrivez-le. Le direct TV apparaît **noir**
sur les captures (la vidéo est sur un plan matériel) : c'est normal, pas une panne.
```
python3 cli/lolos.py verifier
python3 cli/lolos.py verifier --direct
```
`--direct` lance une chaîne, mesure le débit reçu (≈13 Mbit/s quand une chaîne joue) puis revient à
l'accueil : prévenez l'utilisateur, et ne le lancez pas s'il regarde la TV. Code de sortie 2 = un contrôle
a échoué (le JSON indique lequel et le conseil à suivre).

### 10. Expliquer comment revenir en arrière
```
python3 cli/lolos.py restaurer --simulation            # voir ce qui serait fait
python3 cli/lolos.py restaurer --oui                   # état exact de la dernière sauvegarde
python3 cli/lolos.py restaurer --usine --oui           # réactiver absolument toutes les applis
python3 cli/lolos.py restaurer --garder-accueil --oui  # garder le lanceur installé, accueil Bbox par défaut
```
La restauration réactive les applis, remet les réglages sauvegardés, désinstalle le lanceur et remet
l'accueil Bbox. Un redémarrage est conseillé ensuite.

## Dépannage

| Symptôme | Cause et solution |
|---|---|
| `No route to host` sur macOS | Protection « Réseau local » : relais automatique ; sinon Réglages Système → Confidentialité et sécurité → Réseau local → autoriser le terminal. |
| `unauthorized` | Accepter « Autoriser le débogage ? » sur la TV (cocher « Toujours autoriser »). |
| Applis réactivées toutes seules | SML pas désactivé en premier ou encore en mémoire : redémarrer la TV, relancer `appliquer`. |
| « Authentification requise », plus de chaînes | DNS privé : le supprimer (voir Règles) et redémarrer. |
| Capture noire pendant le direct | Normal (plan vidéo matériel). Vérifier avec `verifier --direct`. |
| `error: closed` | Relais ou Wi-Fi coupé : relancer `connecter`. |
