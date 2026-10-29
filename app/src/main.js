const { listen } = window.__TAURI__.event;

const $ = (s) => document.querySelector(s);
const etat = { profil: null, connecte: false, sauvegarde: null, recherche: false };

// ------------------------------------------------------------------ navigation

const entrees = {
  outils: verifierAdb,
  tv: () => { if (!etat.recherche) chercher(); },
  sauvegarde: afficherTv,
  applis: chargerApplis,
  verification: verifier,
  restauration: listerSauvegardes,
};

function aller(id) {
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("visible", p.id === id));
  document.querySelectorAll("#etapes li").forEach((li) => li.classList.toggle("active", li.dataset.etape === id));
  document.querySelector("main").scrollTop = 0;
  entrees[id]?.();
}

function faite(id) {
  document.querySelector(`#etapes li[data-etape="${id}"]`)?.classList.add("faite");
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-aller]");
  if (b && !b.disabled) aller(b.dataset.aller);
});

// ------------------------------------------------------------------ journal en direct

const lignes = [];
const NIVEAUX = { cmd: "cmd", out: "rép.", err: "err.", info: "info", ui: "appli" };

function heure() {
  const d = new Date();
  return d.toTimeString().slice(0, 8) + "." + String(d.getMilliseconds()).padStart(3, "0");
}

function ajouterLigne({ heure, niveau, texte }) {
  lignes.push(`${heure} [${niveau}] ${texte}`);
  const div = document.createElement("div");
  div.className = "trace " + niveau;
  const [h, n, t] = ["h", "n", "t"].map((c) => Object.assign(document.createElement("span"), { className: c }));
  h.textContent = heure;
  n.textContent = NIVEAUX[niveau] || niveau;
  t.textContent = texte;
  div.append(h, n, t);
  const zone = $("#journal-lignes");
  const enBas = zone.scrollHeight - zone.scrollTop - zone.clientHeight < 40;
  zone.append(div);
  if (enBas) zone.scrollTop = zone.scrollHeight;
  $("#journal-compte").textContent = lignes.length;
  if (niveau === "err") $("#journal-dernier").textContent = "⚠️ " + texte.split("\n")[0];
}

/** Message de l'interface : barre du journal + journal détaillé. */
function journal(texte) {
  $("#journal-dernier").textContent = texte.split("\n")[0];
  ajouterLigne({ heure: heure(), niveau: "ui", texte });
}

listen("journal", (e) => ($("#journal-dernier").textContent = e.payload));
listen("log", (e) => ajouterLigne(e.payload));

/** Appel au backend, tracé dans le journal. */
async function invoke(commande, args) {
  const resume = args ? " " + JSON.stringify(args).slice(0, 160) : "";
  ajouterLigne({ heure: heure(), niveau: "ui", texte: "→ " + commande + resume });
  try {
    return await window.__TAURI__.core.invoke(commande, args);
  } catch (err) {
    ajouterLigne({ heure: heure(), niveau: "err", texte: commande + " : " + err });
    throw err;
  }
}

$("#journal-bascule").addEventListener("click", async () => {
  const j = $("#journal");
  const ouvert = j.classList.toggle("ouvert");
  $("#journal-bascule").setAttribute("aria-expanded", ouvert);
  if (ouvert) {
    const zone = $("#journal-lignes");
    zone.scrollTop = zone.scrollHeight;
    if (!$("#journal-fichier").textContent) {
      $("#journal-fichier").textContent = "Fichier : " + (await window.__TAURI__.core.invoke("dossier_donnees")) + "/journal.txt";
    }
  }
});
$("#journal-details").addEventListener("change", (e) => $("#journal-lignes").classList.toggle("sans-details", !e.target.checked));
$("#journal-copier").addEventListener("click", (e) =>
  navigator.clipboard?.writeText(lignes.join("\n")).then(() => (e.target.textContent = "Copié ✓"))
);
$("#journal-vider").addEventListener("click", () => {
  lignes.length = 0;
  $("#journal-lignes").textContent = "";
  $("#journal-compte").textContent = "0";
});

// ------------------------------------------------------------------ utilitaires

function statut(el, texte, genre = "") {
  el.classList.remove("cache", "ok", "erreur", "attente");
  if (genre) el.classList.add(genre);
  el.textContent = texte;
}

async function occupe(bouton, travail) {
  bouton.disabled = true;
  try { return await travail(); } finally { bouton.disabled = false; }
}

function echapper(t) {
  return String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// ------------------------------------------------------------------ 2. adb

const INSTALL_MANUELLE = {
  macos: [["Avec Homebrew", "brew install --cask android-platform-tools"]],
  windows: [["Avec winget (Windows 10/11)", "winget install Google.PlatformTools"]],
  linux: [
    ["Debian, Ubuntu, Mint", "sudo apt install adb"],
    ["Fedora", "sudo dnf install android-tools"],
    ["Arch, Manjaro", "sudo pacman -S android-tools"],
  ],
};

async function verifierAdb() {
  const el = $("#adb-statut");
  statut(el, "Recherche d'adb…", "attente");
  const r = await invoke("etat_adb");
  if (r.trouve) {
    statut(el, `✓ adb trouvé : ${r.chemin}${r.version ? " — " + r.version : ""}`, "ok");
    $("#adb-absent").classList.add("cache");
    $("#outils-suivant").disabled = false;
    faite("outils");
    return;
  }
  statut(el, "adb n'est pas installé sur cet ordinateur.", "erreur");
  const lignes = (INSTALL_MANUELLE[r.systeme] || INSTALL_MANUELLE.linux)
    .map(([titre, cmd]) => `<p>${titre} :</p><div class="commande"><code>${cmd}</code><button data-copier="${cmd}">Copier</button></div>`)
    .join("");
  $("#adb-manuel").innerHTML = `${lignes}
    <p>Ou téléchargez les « SDK Platform-Tools » sur
    <code>developer.android.com/tools/releases/platform-tools</code>, décompressez-les et ajoutez le dossier au PATH.</p>
    <p><button id="adb-reverifier">J'ai installé adb, revérifier</button></p>`;
  $("#adb-absent").classList.remove("cache");
}

document.addEventListener("click", (e) => {
  const c = e.target.closest("[data-copier]");
  if (c) navigator.clipboard?.writeText(c.dataset.copier).then(() => (c.textContent = "Copié ✓"));
  if (e.target.id === "adb-reverifier") verifierAdb();
});

$("#adb-installer").addEventListener("click", (e) =>
  occupe(e.target, async () => {
    statut($("#adb-statut"), "Téléchargement des outils officiels Google…", "attente");
    try {
      await invoke("installer_adb");
      await verifierAdb();
    } catch (err) {
      statut($("#adb-statut"), "Échec du téléchargement : " + err + " — utilisez l'installation manuelle.", "erreur");
    }
  })
);

// ------------------------------------------------------------------ 3. TV

async function chercher() {
  if (etat.enRecherche) return;
  etat.recherche = etat.enRecherche = true;
  const el = $("#tv-statut");
  statut(el, "Recherche des TV sur le réseau (≈ 20 s)…", "attente");
  $("#tv-liste").innerHTML = "";
  let r;
  try { r = await invoke("chercher_tv"); } finally { etat.enRecherche = false; }
  const tvs = r.tvs;
  if (!tvs.length && r.acces_bloque) {
    el.classList.remove("cache", "ok", "attente");
    el.classList.add("erreur");
    el.innerHTML = `<span>macOS empêche Lol'OS d'accéder au réseau local. Ouvrez <b>Réglages Système → Confidentialité et
      sécurité → Réseau local</b> et activez <b>LolOS</b>. S'il est déjà activé (ancienne version), désactivez-le puis
      réactivez-le, quittez et relancez Lol'OS.</span>
      <button id="tv-reglages">Ouvrir les réglages</button>`;
    $("#tv-reglages").addEventListener("click", () => invoke("ouvrir_reglages_reseau_local"));
    return;
  }
  if (!tvs.length) {
    statut(el, `Aucune TV trouvée sur le réseau ${r.reseau}. Vérifiez qu'elle est allumée (pas en veille) et sur le même ` +
      "réseau que cet ordinateur (attention aux VPN), ou saisissez son adresse IP.", "erreur");
    return;
  }
  el.classList.add("cache");
  for (const tv of tvs) {
    const li = document.createElement("li");
    li.innerHTML = `<span><b>${echapper(tv.ip)}</b> <span class="petit">${tv.cast ? "Android TV / Chromecast" : "Android TV"}</span></span>
      <span class="badge ${tv.adb ? "ok" : "non"}">${tv.adb ? "débogage actif" : "débogage à activer"}</span>`;
    li.addEventListener("click", () => {
      document.querySelectorAll("#tv-liste li").forEach((x) => x.classList.remove("choisi"));
      li.classList.add("choisi");
      $("#tv-ip").value = tv.ip;
      if (!tv.adb) $("#tv-aide").open = true;
    });
    $("#tv-liste").append(li);
  }
  if (tvs.length === 1) $("#tv-liste li").click();
}

$("#tv-chercher").addEventListener("click", (e) => occupe(e.target, chercher));

$("#tv-connecter").addEventListener("click", (e) =>
  occupe(e.target, async () => {
    const ip = $("#tv-ip").value.trim();
    const el = $("#tv-statut");
    if (!ip) return statut(el, "Choisissez une TV ou saisissez son adresse IP.", "erreur");
    statut(el, "Connexion…", "attente");
    try {
      let s = await invoke("connecter", { ip });
      for (let i = 0; s === "unauthorized" && i < 30; i++) {
        statut(el, "Sur la TV : cochez « Toujours autoriser » puis OK dans « Autoriser le débogage ? »", "attente");
        await new Promise((r) => setTimeout(r, 2000));
        s = await invoke("statut_connexion");
      }
      if (s !== "device") throw new Error(s === "unauthorized" ? "autorisation non accordée sur la TV" : "TV injoignable (" + s + ")");
      localStorage.setItem("ip", ip);
      etat.connecte = true;
      statut(el, "✓ Connecté à la TV " + ip, "ok");
      $("#tv-suivant").disabled = false;
      faite("tv");
      verifierAccueilTv();
    } catch (err) {
      statut(el, "Connexion impossible : " + (err.message || err), "erreur");
      $("#tv-aide").open = true;
    }
  })
);

$("#tv-diagnostic").addEventListener("click", (e) =>
  occupe(e.target, async () => {
    const ip = $("#tv-ip").value.trim();
    if (!ip) return statut($("#tv-statut"), "Saisissez l'adresse IP de la TV pour la diagnostiquer.", "erreur");
    journal("— Diagnostic réseau " + ip + " —\n" + (await invoke("diagnostic_reseau", { ip })));
  })
);

try { $("#tv-ip").value = localStorage.getItem("ip") || ""; } catch {}

// ------------------------------------------------------------------ 4. sauvegarde

function carteInfo(titre, valeur, ok) {
  const cls = ok === undefined ? "" : ok ? "ok" : "ko";
  return `<div class="info ${cls}"><small>${titre}</small>${echapper(valeur)}</div>`;
}

async function afficherTv() {
  if (!etat.connecte) return statut($("#sauv-statut"), "Connectez d'abord la TV (étape précédente).", "erreur");
  const t = await invoke("etat_tv");
  $("#tv-infos").innerHTML =
    carteInfo("Modèle", `${t.modele} — Android ${t.android}`, t.est_bbox) +
    carteInfo("Mémoire disponible", `${t.memoire_dispo_mo} Mo`) +
    carteInfo("Applis désactivées", t.desactives) +
    carteInfo("Écran d'accueil", t.accueil.split("/")[0]);
  if (!t.est_bbox) {
    statut($("#sauv-statut"), "⚠️ Cette TV ne semble pas être une Bbox 4K : le profil est prévu pour elle. Continuez seulement si vous savez ce que vous faites.", "erreur");
  }
}

$("#sauv-faire").addEventListener("click", (e) =>
  occupe(e.target, async () => {
    const el = $("#sauv-statut");
    if (!etat.connecte) return statut(el, "Connectez d'abord la TV.", "erreur");
    statut(el, "Sauvegarde en cours…", "attente");
    try {
      etat.sauvegarde = await invoke("sauvegarder");
      statut(el, "✓ Sauvegardé dans " + etat.sauvegarde, "ok");
      journal("Sauvegarde : " + etat.sauvegarde);
      faite("sauvegarde");
      aller("applis");
    } catch (err) {
      statut(el, "Échec de la sauvegarde : " + err, "erreur");
    }
  })
);

// ------------------------------------------------------------------ 5. applis

/** « Utilisée 7 h 05 sur cette TV, la dernière fois le 27/10/2026 » */
function texteUtilisation(u) {
  if (!u) return "";
  const h = Math.floor(u.secondes / 3600), m = Math.round((u.secondes % 3600) / 60);
  const duree = h ? `${h} h${m ? " " + String(m).padStart(2, "0") : ""}` : `${Math.max(m, 1)} min`;
  const [a, mo, j] = u.derniere.split(" ")[0].split("-");
  return `Utilisée ${duree} sur cette TV` + (a && a !== "1970" ? `, la dernière fois le ${j}/${mo}/${a}` : "");
}

async function chargerApplis() {
  if (!etat.connecte) return;
  etat.profil ??= await invoke("profil");
  const p = etat.profil;
  $("#categories").innerHTML = '<p class="petit">Lecture des applis installées sur la TV…</p>';
  let paquets;
  try {
    paquets = await invoke("paquets");
  } catch (err) {
    $("#categories").innerHTML = `<p class="petit">⚠️ Lecture des applis impossible : ${echapper(err)}</p>`;
    return;
  }
  $("#avertissements").innerHTML = p.avertissements.map((a) => `<div class="avertissement">${echapper(a)}</div>`).join("");
  afficherRemplacements(p, paquets);
  $("#proteges").textContent = p.proteges.paquets.join(", ");
  $("#categories").innerHTML = "";
  for (const cat of p.categories) {
    const liste = paquets.filter((x) => x.categorie === cat.id && x.present && !x.cache);
    if (!liste.length) continue;
    const bloc = document.createElement("div");
    bloc.className = "carte categorie";
    bloc.innerHTML = `<header><h3>${echapper(cat.titre)}</h3><p>${echapper(cat.description)}</p></header><div class="paquets"></div>`;
    for (const x of liste) {
      // Une appli que vous utilisez n'est jamais cochée d'office (sauf télémétrie et pubs : demander = false).
      const coche = x.desactive || (x.defaut === "desactiver" && !(x.favori && cat.demander));
      const usage = texteUtilisation(x.utilisation);
      const label = document.createElement("label");
      label.className = "paquet" + (x.desactive ? " deja" : "");
      label.title = [x.note, usage].filter(Boolean).join("\n");
      label.innerHTML = `<input type="checkbox" value="${echapper(x.id)}" ${coche ? "checked" : ""} ${x.desactive ? "disabled" : ""}>
        <span><span>${echapper(x.nom)}${x.favori ? ` <i class="coeur" title="${echapper(usage)}">♥</i>` : ""}</span><small>${echapper(x.note || x.id)}</small></span>`;
      bloc.querySelector(".paquets").append(label);
    }
    $("#categories").append(bloc);
  }
  resume();
}

/** Proposition « Remplacer YouTube par SmartTube » (profil : remplacements), si l'appli remplacée est là. */
function afficherRemplacements(p, paquets) {
  const zone = $("#remplacements");
  zone.textContent = "";
  for (const r of p.remplacements || []) {
    const ancienne = paquets.find((x) => x.id === r.remplace && x.present);
    if (!ancienne) continue;
    // Installé mais désactivé : proposé comme à installer (l'installation le réactive).
    const installe = paquets.some((x) => x.id === r.paquet && x.present && !x.desactive);
    const bloc = document.createElement("div");
    bloc.className = "carte remplacement";
    bloc.innerHTML = installe
      ? `<h3>✓ ${echapper(r.nom)} est installé</h3>
         <p>Cochez ${echapper(ancienne.nom)} plus bas pour le désactiver. ${echapper(r.avertissement || "")}</p>`
      : `<label class="choix"><input type="checkbox" data-remplacement="${echapper(r.paquet)}" data-remplace="${echapper(r.remplace)}">
           <b>Remplacer ${echapper(ancienne.nom)} par ${echapper(r.nom)}</b></label>
         <p>${echapper(r.description)}</p>${r.avertissement ? `<p>${echapper(r.avertissement)}</p>` : ""}`;
    zone.append(bloc);
  }
}

/** Remplacer coche (ou décoche) aussi l'appli remplacée dans la liste. */
$("#remplacements").addEventListener("change", (e) => {
  const c = e.target.closest("[data-remplace]");
  const ancienne = c && document.querySelector(`#categories input[value="${c.dataset.remplace}"]`);
  if (ancienne && !ancienne.disabled) ancienne.checked = c.checked;
  resume();
});

function remplacementsChoisis() {
  return [...document.querySelectorAll("#remplacements input:checked")].map((i) => i.dataset.remplacement);
}

function choisis() {
  return [...document.querySelectorAll("#categories input:checked:not(:disabled)")].map((i) => i.value);
}

function resume() {
  const n = choisis().length, r = remplacementsChoisis().length;
  const morceaux = [];
  if (r) morceaux.push(`${r} appli${r > 1 ? "s" : ""} à installer`);
  if (n) morceaux.push(`${n} appli${n > 1 ? "s" : ""} à désactiver`);
  $("#applis-resume").textContent = morceaux.join(", ") || "Rien de nouveau à désactiver";
}
$("#categories").addEventListener("change", resume);

$("#applis-simuler").addEventListener("click", (e) =>
  occupe(e.target, async () => {
    journal("— Ce qui sera fait —");
    for (const paquet of remplacementsChoisis()) {
      const r = etat.profil.remplacements.find((x) => x.paquet === paquet);
      journal(`(simulation) installer ${r.nom} (dernière version publiée sur github.com/${r.depot})`);
    }
    await invoke("appliquer", { desactiver: choisis(), simulation: true });
  })
);

$("#applis-appliquer").addEventListener("click", (e) =>
  occupe(e.target, async () => {
    if (!etat.sauvegarde) {
      journal("⚠️ Faites d'abord la sauvegarde (étape 4).");
      return aller("sauvegarde");
    }
    try {
      // D'abord installer : si ça échoue, l'appli remplacée n'est pas désactivée.
      for (const paquet of remplacementsChoisis()) {
        await invoke("installer_remplacement", { paquet });
        // Installé : ne pas le retélécharger si la suite échoue et qu'on réessaie.
        document.querySelector(`#remplacements [data-remplacement="${paquet}"]`).checked = false;
      }
      const n = await invoke("appliquer", { desactiver: choisis(), simulation: false });
      journal(`✓ ${n} appli(s) désactivée(s)`);
      faite("applis");
      aller("accueil");
    } catch (err) {
      journal("Erreur : " + err);
    }
  })
);

// ------------------------------------------------------------------ 6. accueil

$("#acc-installer").addEventListener("click", (e) =>
  occupe(e.target, async () => {
    try {
      await invoke("installer_accueil", { fonds: $("#acc-fonds").checked });
      faite("accueil");
      aller("personnaliser");
    } catch (err) {
      journal("Erreur : " + err);
    }
  })
);

// ------------------------------------------------------------------ 7. personnaliser et 8. options

etat.logo = undefined; // undefined : inchangé · "" : logo Lol'OS · data:image/png… : image choisie
etat.perso = null;     // configuration lue sur la TV (docs/personnalisation.md)

/** Lit la configuration de la TV une fois, et remplit les deux pages. */
async function chargerPerso() {
  if (!etat.connecte || etat.perso) return;
  etat.perso = await invoke("lire_personnalisation");
  const p = etat.perso;
  $("#perso-salut").value = p.salutation ?? "";
  $("#perso-message").value = p.message_demarrage ?? "";
  $("#perso-demarrage").checked = p.demarrage ?? true;
  $("#perso-paysages").checked = p.fonds_lolos ?? true;
  $("#perso-nuit").checked = !!p.mode_nuit;
  $("#opt-nettoyage").checked = !!p.nettoyage_memoire;
  $("#opt-infos").checked = !!p.infos_systeme;
  afficherMeteo(p.meteo);
  const mode = await invoke("veille", { mode: null });
  document.querySelectorAll('input[name="veille"]').forEach((r) => (r.checked = r.value === mode));
}

entrees.personnaliser = async () => { await chargerPerso(); listerFonds(); };
entrees.options = chargerPerso;

function configCourante() {
  return {
    ...(etat.perso || {}),
    salutation: $("#perso-salut").value.trim(),
    demarrage: $("#perso-demarrage").checked,
    message_demarrage: $("#perso-message").value.trim(),
    duree_demarrage_ms: etat.perso?.duree_demarrage_ms ?? 2500,
    fonds_lolos: $("#perso-paysages").checked,
    mode_nuit: $("#perso-nuit").checked,
    meteo: etat.perso?.meteo ?? null,
    nettoyage_memoire: $("#opt-nettoyage").checked,
    infos_systeme: $("#opt-infos").checked,
  };
}

/** Image choisie → PNG d'au plus 720 px, pour ne pas surcharger la box. */
$("#perso-fichier").addEventListener("change", (e) => {
  const fichier = e.target.files[0];
  if (!fichier) return;
  const lecteur = new FileReader();
  lecteur.onload = () => {
    const img = new Image();
    img.onload = () => {
      const echelle = Math.min(1, 720 / Math.max(img.width || 720, img.height || 720));
      const toile = document.createElement("canvas");
      toile.width = Math.round((img.width || 720) * echelle);
      toile.height = Math.round((img.height || 720) * echelle);
      toile.getContext("2d").drawImage(img, 0, 0, toile.width, toile.height);
      etat.logo = toile.toDataURL("image/png");
      $("#perso-image").src = etat.logo;
      journal("Image de démarrage : " + fichier.name + " (" + toile.width + "×" + toile.height + ")");
    };
    img.onerror = () => journal("⚠️ Image illisible : " + fichier.name);
    img.src = lecteur.result;
  };
  lecteur.readAsDataURL(fichier);
});

$("#perso-defaut").addEventListener("click", () => {
  etat.logo = "";
  $("#perso-image").src = "logo-lolos.svg";
  $("#perso-fichier").value = "";
});

// --- vos fonds

async function listerFonds() {
  if (!etat.connecte) return;
  const ul = $("#fonds-liste");
  const fonds = await invoke("lister_fonds");
  ul.textContent = "";
  if (!fonds.length) {
    ul.innerHTML = '<li class="vide">Aucun fond à vous pour l\'instant.</li>';
    return;
  }
  for (const f of fonds) {
    const li = document.createElement("li");
    const nom = Object.assign(document.createElement("span"), { textContent: f.nom.replace(/^perso-/, "") });
    const taille = Object.assign(document.createElement("span"), { className: "petit", textContent: f.taille_mo + " Mo" });
    const retirer = Object.assign(document.createElement("button"), { textContent: "Retirer" });
    retirer.addEventListener("click", (e) =>
      occupe(e.target, async () => {
        await invoke("retirer_fonds", { noms: [f.nom] });
        journal("Fond retiré : " + f.nom);
        listerFonds();
      })
    );
    li.append(nom, taille, retirer);
    ul.append(li);
  }
}

$("#fonds-ajouter").addEventListener("click", (e) =>
  occupe(e.target, async () => {
    if (!etat.connecte) return journal("⚠️ Connectez d'abord la TV.");
    try {
      const n = await invoke("ajouter_fonds");
      if (n) listerFonds();
    } catch (err) {
      journal("Erreur : " + err);
    }
  })
);

// --- météo

function afficherMeteo(m) {
  $("#meteo-resultat").textContent = m ? `Météo de ${m.ville}${m.pays ? " (" + [m.region, m.pays].filter(Boolean).join(", ") + ")" : ""}.` : "Pas de météo.";
  if (m) $("#meteo-ville").value = m.ville;
}

$("#meteo-chercher").addEventListener("click", (e) =>
  occupe(e.target, async () => {
    const ville = $("#meteo-ville").value.trim();
    if (!ville) return;
    try {
      const m = await invoke("geocoder", { ville });
      etat.perso = { ...(etat.perso || {}), meteo: { ville: m.ville, latitude: m.latitude, longitude: m.longitude } };
      afficherMeteo({ ...m });
    } catch (err) {
      $("#meteo-resultat").textContent = "⚠️ " + err;
    }
  })
);

$("#meteo-retirer").addEventListener("click", () => {
  etat.perso = { ...(etat.perso || {}), meteo: null };
  $("#meteo-ville").value = "";
  afficherMeteo(null);
});

// --- enregistrement

async function envoyerPerso(apercu, suite) {
  if (!etat.connecte) {
    journal("⚠️ Connectez d'abord la TV (étape « Trouver la TV »).");
    return aller("tv");
  }
  try {
    const config = configCourante();
    await invoke("personnaliser", { config, logoBase64: etat.logo ?? null, apercu });
    etat.perso = config;
    etat.logo = undefined;
    if (suite) aller(suite);
  } catch (err) {
    journal("Erreur : " + err);
  }
}
$("#perso-apercu").addEventListener("click", (e) => occupe(e.target, () => envoyerPerso(true)));
$("#perso-enregistrer").addEventListener("click", (e) =>
  occupe(e.target, async () => { await envoyerPerso(false, "options"); faite("personnaliser"); })
);
$("#opt-enregistrer").addEventListener("click", (e) =>
  occupe(e.target, async () => {
    if (!etat.connecte) return journal("⚠️ Connectez d'abord la TV.");
    const mode = document.querySelector('input[name="veille"]:checked')?.value || "1h";
    try {
      await invoke("veille", { mode });
    } catch (err) {
      return journal("Erreur : " + err);
    }
    await envoyerPerso(false, "verification");
    faite("options");
  })
);

// ------------------------------------------------------------------ redémarrage

let confirmation = null;
$("#redemarrer").addEventListener("click", async (e) => {
  const b = e.target;
  if (!etat.connecte) return journal("⚠️ Connectez d'abord la TV pour pouvoir la redémarrer.");
  if (!confirmation) {
    b.textContent = "⏻ Cliquez encore pour confirmer";
    confirmation = setTimeout(() => { b.textContent = "⏻ Redémarrer la box"; confirmation = null; }, 4000);
    return;
  }
  clearTimeout(confirmation);
  confirmation = null;
  b.textContent = "⏻ Redémarrer la box";
  try {
    await invoke("redemarrer_box");
    etat.connecte = false;
    $("#tv-suivant").disabled = true;
    journal("La box redémarre (environ 1 minute). Reconnectez-la ensuite à l'étape « Trouver la TV ».");
  } catch (err) {
    journal("Erreur : " + err);
  }
});

// ------------------------------------------------------------------ 8. vérification

async function verifier() {
  if (!etat.connecte) return;
  const t = await invoke("etat_tv");
  const accueilOk = t.accueil.startsWith("fr.accueil.tv");
  const eligible = /(SD|HD)=true/.test(t.eligibilite);
  $("#verif-resultats").innerHTML =
    carteInfo("Télémétrie SML", t.sml_desactive ? "désactivée" : "active", t.sml_desactive) +
    carteInfo("Écran d'accueil", accueilOk ? "Accueil (nouveau)" : t.accueil.split("/")[0], accueilOk) +
    carteInfo("TV éligible", eligible ? "oui" : "non — redémarrez la box", eligible) +
    carteInfo("DNS privé", t.dns_prive === "hostname" ? "ACTIF — à retirer" : "non", t.dns_prive !== "hostname") +
    carteInfo("Mémoire disponible", `${t.memoire_dispo_mo} Mo`);
  if (accueilOk && t.sml_desactive) faite("verification");
}

$("#verif-capture").addEventListener("click", (e) =>
  occupe(e.target, async () => {
    const img = $("#verif-image");
    img.src = await invoke("capture");
    img.classList.remove("cache");
  })
);

$("#verif-direct").addEventListener("click", (e) =>
  occupe(e.target, async () => {
    journal("Test du direct (≈ 20 s)…");
    const kbits = await invoke("tester_direct");
    const ok = kbits > 2000;
    journal(ok ? `✓ La TV en direct fonctionne (${Math.round(kbits / 100) / 10} Mbit/s reçus)` : `❌ Pas de flux TV (${kbits} kbit/s)`);
    $("#verif-resultats").insertAdjacentHTML("beforeend", carteInfo("TV en direct", ok ? `${Math.round(kbits / 100) / 10} Mbit/s` : "aucun flux", ok));
  })
);

// ------------------------------------------------------------------ restauration

let choixSauvegarde = null;

async function listerSauvegardes() {
  const liste = await invoke("sauvegardes");
  const ul = $("#rest-liste");
  ul.innerHTML = liste.length ? "" : `<li>Aucune sauvegarde dans ${echapper(await invoke("dossier_donnees"))}</li>`;
  for (const chemin of liste) {
    const li = document.createElement("li");
    const nom = chemin.split(/[\\/]/).pop();
    li.innerHTML = `<span><b>${echapper(nom.replace("_", " à ").replace(/(\d\d)(\d\d)(\d\d)$/, "$1:$2:$3"))}</b></span><span class="petit">${echapper(chemin)}</span>`;
    li.addEventListener("click", () => {
      ul.querySelectorAll("li").forEach((x) => x.classList.remove("choisi"));
      li.classList.add("choisi");
      choixSauvegarde = chemin;
      $("#rest-lancer").disabled = false;
    });
    ul.append(li);
  }
}

$("#rest-lancer").addEventListener("click", (e) =>
  occupe(e.target, async () => {
    if (!etat.connecte) {
      journal("⚠️ Connectez d'abord la TV (étape « Trouver la TV »).");
      return aller("tv");
    }
    try {
      await invoke("restaurer", {
        sauvegarde: choixSauvegarde,
        usine: $("#rest-usine").checked,
        garderAccueil: $("#rest-garder").checked,
      });
      journal("✓ Restauration terminée.");
    } catch (err) {
      journal("Erreur : " + err);
    }
  })
);

// ------------------------------------------------------------------ mises à jour

/** versionCode Android → « 1.1.0 » (les toutes premières versions n'avaient pas ce format). */
function versionLisible(code) {
  return code >= 10000 ? `${Math.floor(code / 10000)}.${Math.floor(code / 100) % 100}.${code % 100}` : "ancienne version";
}

async function verifierMiseAJour() {
  try {
    const maj = await invoke("verifier_mise_a_jour");
    if (!maj) return;
    $("#maj-texte").textContent = `Lol'OS ${maj.version} est disponible.`;
    $("#maj-bouton").textContent = maj.automatique ? "Mettre à jour" : "Télécharger";
    $("#maj-bouton").onclick = (e) =>
      occupe(e.target, async () => {
        if (!maj.automatique) return invoke("ouvrir_page_versions");
        journal("Mise à jour vers Lol'OS " + maj.version + "… l'appli va redémarrer.");
        try { await invoke("installer_mise_a_jour"); } catch (err) { journal("Mise à jour impossible : " + err); }
      });
    $("#maj").classList.remove("cache");
  } catch (err) {
    journal("Vérification des mises à jour impossible (" + err + ").");
  }
}

/** Accueil de la TV plus ancien que celui livré avec cet installateur ? */
async function verifierAccueilTv() {
  try {
    const t = await invoke("etat_tv");
    const ancien = t.accueil_installe && (t.accueil_version ?? 0) < t.accueil_disponible;
    $("#maj-tv").classList.toggle("cache", !ancien);
    if (!ancien) return;
    $("#maj-tv-texte").textContent =
      `L'accueil Lol'OS de la TV peut être mis à jour (${versionLisible(t.accueil_version ?? 0)} → ${versionLisible(t.accueil_disponible)}). ` +
      "Vos réglages sont conservés.";
    $("#maj-tv-bouton").onclick = (e) =>
      occupe(e.target, async () => {
        try {
          await invoke("installer_accueil", { fonds: true });
          $("#maj-tv").classList.add("cache");
          journal("✓ Accueil de la TV mis à jour.");
        } catch (err) {
          journal("Erreur : " + err);
        }
      });
  } catch {}
}

aller("bienvenue");
verifierMiseAJour();
