package fr.accueil.tv;

import android.content.Context;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Rect;
import android.graphics.drawable.Drawable;
import android.net.Uri;
import android.provider.Settings;

import java.text.Collator;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/** Applis lançables + tuiles spéciales vers l'interface Bbox (TV, programme, replay…). */
final class Apps {
    static final String BBUI = "fr.bouyguestelecom.tv.bbui";

    /** Entrées techniques ou en double avec une tuile spéciale (« Réglages »). */
    private static final Set<String> HIDDEN = new HashSet<>(Arrays.asList(
            "com.android.tv.settings",
            "fr.bouyguestelecom.tv.partners.channelapps"));

    static final class Entry {
        final String key;          // nom de paquet, ou « @… » pour une tuile spéciale
        final String label;
        final Intent intent;
        final String packageName;  // null pour une tuile spéciale
        final Drawable banner;     // bannière TV 16:9 fournie par l'appli
        final Drawable icon;       // sinon, icône sur fond coloré
        final int[] colors;

        Entry(String key, String label, Intent intent, String packageName,
              Drawable banner, Drawable icon, int[] colors) {
            this.key = key;
            this.label = label;
            this.intent = intent;
            this.packageName = packageName;
            this.banner = banner;
            this.icon = icon;
            this.colors = colors;
        }

        boolean isSpecial() {
            return packageName == null;
        }
    }

    final List<Entry> all = new ArrayList<>();
    final Map<String, Entry> byKey = new HashMap<>();

    private void add(Entry e) {
        all.add(e);
        byKey.put(e.key, e);
    }

    static Apps load(Context context) {
        PackageManager pm = context.getPackageManager();
        Apps apps = new Apps();

        // Le direct plein écran : lien « chaîne en cours » compris par LiveActivity elle-même.
        // (bbox://live ou LiveActivity seule retombent sur l'accueil Bbox et ses recommandations.)
        Intent live = new Intent(Intent.ACTION_VIEW, Uri.parse("https://media.bbox.fr/channel/live"))
                .setClassName(BBUI, BBUI + ".ui.live.LiveActivity")
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        if (pm.resolveActivity(live, 0) != null) {
            apps.add(special("@tv", "TV", live, 0xFF2457E0, 0xFF0B1E5B));
            apps.add(special("@guide", "Programme TV", bbox("epg"), 0xFF7A3FD1, 0xFF2C1060));
            apps.add(special("@records", "Enregistrements",
                    new Intent(Intent.ACTION_MAIN)
                            .setClassName(BBUI, BBUI + ".ui.pvr.PvrActivity")
                            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP),
                    0xFFD8432F, 0xFF5A120A));
        }
        apps.add(special("@settings", "Réglages",
                new Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                0xFF5E6670, 0xFF24282E));

        // Une entrée par paquet, en préférant l'activité TV (leanback)
        Map<String, ResolveInfo> found = new LinkedHashMap<>();
        Map<String, String> categories = new HashMap<>();
        for (String category : new String[] {
                Intent.CATEGORY_LEANBACK_LAUNCHER, Intent.CATEGORY_LAUNCHER }) {
            Intent query = new Intent(Intent.ACTION_MAIN).addCategory(category);
            for (ResolveInfo ri : pm.queryIntentActivities(query, 0)) {
                String pkg = ri.activityInfo.packageName;
                if (pkg.equals(context.getPackageName()) || HIDDEN.contains(pkg) || found.containsKey(pkg)) continue;
                found.put(pkg, ri);
                categories.put(pkg, category);
            }
        }

        List<Entry> regular = new ArrayList<>();
        for (Map.Entry<String, ResolveInfo> it : found.entrySet()) {
            String pkg = it.getKey();
            ActivityInfo ai = it.getValue().activityInfo;
            Intent launch = new Intent(Intent.ACTION_MAIN)
                    .addCategory(categories.get(pkg))
                    .setClassName(pkg, ai.name)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
            String label = pkg.equals(BBUI) ? "Accueil Bbox" : String.valueOf(it.getValue().loadLabel(pm));
            Drawable banner = ai.loadBanner(pm);
            Drawable icon = banner == null ? ai.loadIcon(pm) : null;
            regular.add(new Entry(pkg, label, launch, pkg, banner, icon,
                    icon == null ? null : tint(icon)));
        }
        Collator collator = Collator.getInstance(Locale.FRANCE);
        regular.sort((a, b) -> collator.compare(a.label, b.label));
        for (Entry e : regular) apps.add(e);
        return apps;
    }

    private static Intent bbox(String path) {
        return new Intent(Intent.ACTION_VIEW, Uri.parse("bbox://" + path))
                .setPackage(BBUI)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
    }

    private static Entry special(String key, String label, Intent intent, int from, int to) {
        return new Entry(key, label, intent, null, null, null, new int[] { from, to });
    }

    /** Couleur moyenne de l'icône, assombrie, pour le fond de la tuile. */
    private static int[] tint(Drawable icon) {
        Bitmap bitmap = Bitmap.createBitmap(12, 12, Bitmap.Config.ARGB_8888);
        Rect old = icon.copyBounds();
        icon.setBounds(0, 0, 12, 12);
        icon.draw(new Canvas(bitmap));
        icon.setBounds(old);
        long r = 0, g = 0, b = 0;
        int n = 0;
        for (int y = 0; y < 12; y++) {
            for (int x = 0; x < 12; x++) {
                int px = bitmap.getPixel(x, y);
                if (Color.alpha(px) < 200) continue;
                r += Color.red(px);
                g += Color.green(px);
                b += Color.blue(px);
                n++;
            }
        }
        bitmap.recycle();
        int base = n == 0 ? 0xFF3A3F47 : Color.rgb((int) (r / n), (int) (g / n), (int) (b / n));
        return new int[] { shade(base, 0.8f), shade(base, 0.4f) };
    }

    private static int shade(int c, float f) {
        return Color.rgb((int) (Color.red(c) * f), (int) (Color.green(c) * f), (int) (Color.blue(c) * f));
    }
}
