package fr.accueil.tv;

import android.content.Context;

import org.json.JSONObject;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Calendar;

/**
 * Réglages choisis dans l'installateur ou le CLI, déposés par adb dans
 * /sdcard/Android/data/fr.accueil.tv/files/ — format décrit dans docs/personnalisation.md.
 * Toutes les options sont facultatives et désactivées par défaut.
 */
final class Personnalisation {
    static final String FICHIER = "personnalisation.json";
    static final String LOGO = "logo-demarrage.png";

    final String salutation;
    final boolean demarrage;
    final String messageDemarrage;
    final long dureeDemarrage;
    final File logo;  // null : logo Lol'OS intégré
    final boolean nettoyageMemoire;
    final boolean infosSysteme;
    final boolean modeNuit;
    final boolean fondsLolos;
    final String meteoVille;  // null : pas de météo
    final double latitude, longitude;

    private Personnalisation(JSONObject o, File dossier) {
        salutation = o.optString("salutation", "").trim();
        demarrage = o.optBoolean("demarrage", true);
        messageDemarrage = o.optString("message_demarrage", "").trim();
        dureeDemarrage = Math.max(1000, Math.min(10_000, o.optLong("duree_demarrage_ms", 2500)));
        File f = dossier == null ? null : new File(dossier, LOGO);
        logo = f != null && f.isFile() ? f : null;
        nettoyageMemoire = o.optBoolean("nettoyage_memoire", false);
        infosSysteme = o.optBoolean("infos_systeme", false);
        modeNuit = o.optBoolean("mode_nuit", false);
        fondsLolos = o.optBoolean("fonds_lolos", true);
        JSONObject m = o.optJSONObject("meteo");
        boolean meteo = m != null && m.has("latitude") && m.has("longitude");
        meteoVille = meteo ? m.optString("ville", "") : null;
        latitude = meteo ? m.optDouble("latitude") : 0;
        longitude = meteo ? m.optDouble("longitude") : 0;
    }

    static Personnalisation charger(Context context) {
        File dossier = context.getExternalFilesDir(null);
        JSONObject o = new JSONObject();
        try {
            File f = new File(dossier, FICHIER);
            if (f.isFile()) o = new JSONObject(new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8));
        } catch (Exception ignored) {
            // fichier absent ou illisible : valeurs par défaut
        }
        return new Personnalisation(o, dossier);
    }

    /** Mode nuit actif maintenant (21 h – 7 h) ? */
    boolean nuit() {
        int h = Calendar.getInstance().get(Calendar.HOUR_OF_DAY);
        return modeNuit && (h >= 21 || h < 7);
    }

    /** Remplace {moment} par Bonjour / Bon après-midi / Bonsoir selon l'heure. */
    static String avecMoment(String texte) {
        int h = Calendar.getInstance().get(Calendar.HOUR_OF_DAY);
        String moment = h >= 5 && h < 12 ? "Bonjour" : h >= 12 && h < 18 ? "Bon après-midi" : "Bonsoir";
        return texte.replace("{moment}", moment);
    }
}
