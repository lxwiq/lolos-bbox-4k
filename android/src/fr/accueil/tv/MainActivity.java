package fr.accueil.tv;

import android.app.Activity;
import android.app.ActivityManager;
import android.app.ActivityOptions;
import android.app.AlertDialog;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Rect;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.os.StatFs;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.provider.Settings;
import android.text.TextUtils;
import android.util.DisplayMetrics;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.SurfaceView;
import android.view.View;
import android.widget.FrameLayout;
import android.graphics.BitmapFactory;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextClock;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import java.util.Collections;
import java.util.Iterator;
import java.util.List;

/**
 * Écran d'accueil minimal façon Apple TV : paysage animé en fond, dock d'applis favorites
 * (panneau translucide en bas), grille « Toutes les applis » sous le dock (flèche bas).
 * Appui long sur OK (ou touche Menu) : déplacer / retirer / ajouter au dock.
 *
 * Toutes les dimensions sont en unités d'écran (1/960 de la largeur : 2 px en 1080p, 4 px en
 * 4K), quelle que soit la densité annoncée par la box : la grille de 5 colonnes occupe toujours
 * la même part de l'écran, avec des marges égales à gauche et à droite.
 */
public class MainActivity extends Activity {
    /** Colonnes de la grille ; le dock en a autant, alignées dessus. */
    private static final int COLUMNS = 5;
    private static final int MARGE = 48, ECART = 24, LIBELLE = 30;
    /** Dock : débord du panneau autour des tuiles, marge au-dessus des tuiles, marge sous le panneau. */
    private static final int DOCK_BORD = 14, DOCK_HAUT = 14, DOCK_BAS = 20;
    private static final long IDLE_MS = 60_000;
    private static final long ROTATE_MS = 8 * 60_000;
    private static final String[] DEFAULT_DOCK = {
            "@tv", "com.nuvio.tv", "tv.arte.plus7.bouygues", "com.google.android.youtube.tv", "@guide" };

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final List<String> dock = new ArrayList<>();
    private final List<TileView> dockTiles = new ArrayList<>();

    private SharedPreferences prefs;
    /** Pixels par unité d'écran, et dimensions de la grille en pixels (voir mesurer()). */
    private float unite;
    private int tileW, tileH, gap, side, labelH;
    private Apps apps;
    private Wallpaper wallpaper;
    private VScroll scroll;
    private LinearLayout content, dockRow, grid;
    private TextView gridTitle, hint;
    private View dim, topScrim, clock;
    private FrameLayout racine;
    private TextView salut, meteo;
    private View voileNuit;
    private String meteoTexte;
    private long meteoHeure;
    private boolean infosAffichees;
    private Personnalisation perso;
    private boolean demarrageMontre;

    /** Lancé par l'installateur pour prévisualiser l'écran de démarrage. */
    static final String APERCU_DEMARRAGE = "fr.accueil.tv.APERCU_DEMARRAGE";
    private AlertDialog menu;

    private int lastDockIndex;
    private int moving = -1;  // index de la tuile du dock en cours de déplacement
    private boolean idle, swallowUp, resumedOnce, wallpaperPaused, rebuilding, tvWarm, auPremierPlan;

    private final Runnable goIdle = this::enterIdle;
    /** L'interface Bbox ignore la demande de direct tant qu'elle démarre : on la renvoie. */
    private final Runnable resendTv = () -> launchKey("@tv");
    private final Runnable reload = this::reloadApps;
    private final Runnable rotate = new Runnable() {
        @Override
        public void run() {
            wallpaper.next();
            // Rester sur l'accueil au passage de 21 h ou de midi : mode nuit, salutation et météo suivent
            if (perso != null) {
                voileNuit.animate().alpha(perso.nuit() ? 0.35f : 0f).setDuration(1500);
                salut.setText(Personnalisation.avecMoment(perso.salutation));
                if (perso.meteoVille != null) actualiserMeteo();
            }
            handler.postDelayed(this, ROTATE_MS);
        }
    };
    /** Après la veille, revenir devant si le service d'alimentation a relancé l'interface Bbox. */
    private final Runnable revenirDevant = () -> {
        if (!auPremierPlan) ramenerDevant();
    };
    private final BroadcastReceiver ecran = new BroadcastReceiver() {
        @Override
        public void onReceive(Context c, Intent i) {
            handler.removeCallbacks(revenirDevant);
            if (Intent.ACTION_SCREEN_OFF.equals(i.getAction())) {
                // Écran éteint : Lol'OS passe devant, pour être l'appli trouvée au réveil
                if (!auPremierPlan) ramenerDevant();
            } else {
                handler.postDelayed(revenirDevant, 2500);
            }
        }
    };
    private final BroadcastReceiver packagesChanged = new BroadcastReceiver() {
        @Override
        public void onReceive(Context c, Intent i) {
            handler.removeCallbacks(reload);
            handler.postDelayed(reload, 1500);
        }
    };

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences("accueil", MODE_PRIVATE);
        mesurer();
        loadDock();
        buildUi();
        perso = Personnalisation.charger(this);
        infosAffichees = perso.infosSysteme;
        apps = Apps.load(this, infosAffichees);
        renderAll();
        scroll.post(this::focusDock);
        if (getIntent().getBooleanExtra(APERCU_DEMARRAGE, false)) {
            demarrageMontre = true;
            scroll.post(this::montrerDemarrage);
        }

        IntentFilter filter = new IntentFilter();
        filter.addAction(Intent.ACTION_PACKAGE_ADDED);
        filter.addAction(Intent.ACTION_PACKAGE_REMOVED);
        filter.addAction(Intent.ACTION_PACKAGE_CHANGED);
        filter.addDataScheme("package");
        registerReceiver(packagesChanged, filter);
        // Applis installées sur le stockage étendu de la box : disponibles quelques secondes après
        // le démarrage, annoncées sans PACKAGE_ADDED (et sans donnée « package », d'où un second filtre).
        IntentFilter stockage = new IntentFilter(Intent.ACTION_EXTERNAL_APPLICATIONS_AVAILABLE);
        stockage.addAction(Intent.ACTION_EXTERNAL_APPLICATIONS_UNAVAILABLE);
        registerReceiver(packagesChanged, stockage);

        IntentFilter veille = new IntentFilter(Intent.ACTION_SCREEN_OFF);
        veille.addAction(Intent.ACTION_SCREEN_ON);
        registerReceiver(ecran, veille);
    }

    private void ramenerDevant() {
        try {
            startActivity(new Intent(this, MainActivity.class)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT));
        } catch (RuntimeException ignored) {
        }
    }

    @Override
    protected void onDestroy() {
        unregisterReceiver(packagesChanged);
        unregisterReceiver(ecran);
        handler.removeCallbacksAndMessages(null);
        super.onDestroy();
    }

    @Override
    protected void onResume() {
        super.onResume();
        auPremierPlan = true;
        handler.removeCallbacks(resendTv);
        wallpaperPaused = false;
        appliquerPersonnalisation();
        wallpaper.start(resumedOnce);  // un nouveau paysage à chaque retour
        resumedOnce = true;
        applyScrollFx(scroll.getScrollY());
        handler.postDelayed(rotate, ROTATE_MS);
        scheduleIdle();
    }

    @Override
    protected void onPause() {
        auPremierPlan = false;
        handler.removeCallbacks(rotate);
        handler.removeCallbacks(goIdle);
        wallpaper.stop();
        if (moving >= 0) endMove();
        if (menu != null) menu.dismiss();
        idle = false;
        for (View v : new View[] { scroll, clock, salut, topScrim }) {
            v.animate().cancel();
            v.setAlpha(1f);
        }
        applyScrollFx(scroll.getScrollY());
        super.onPause();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        // Touche Accueil alors qu'on est déjà là : retour au dock
        if (menu != null) menu.dismiss();
        if (moving >= 0) endMove();
        focusDock();
        if (intent.getBooleanExtra(APERCU_DEMARRAGE, false)) montrerDemarrage();
    }

    // ---------------------------------------------------------------- interface

    /**
     * Cale la grille sur la largeur réelle de l'écran : 5 tuiles 16:9 séparées par des écarts
     * fixes, le reste en marges égales des deux côtés (≈ zone sûre des TV, 5 % de la largeur).
     */
    private void mesurer() {
        DisplayMetrics m = getResources().getDisplayMetrics();
        int largeur = Math.max(m.widthPixels, m.heightPixels);
        unite = largeur / 960f;
        gap = u(ECART);
        tileW = (largeur - 2 * u(MARGE) - (COLUMNS - 1) * gap) / COLUMNS;
        tileH = Math.round(tileW * 9 / 16f);
        side = (largeur - COLUMNS * tileW - (COLUMNS - 1) * gap) / 2;
        labelH = u(LIBELLE);
    }

    private void buildUi() {
        FrameLayout root = new FrameLayout(this);

        SurfaceView surface = new SurfaceView(this);
        ImageView photo = new ImageView(this);
        View curtain = new View(this);
        curtain.setBackgroundColor(Color.BLACK);
        root.addView(surface, match());
        root.addView(photo, match());
        root.addView(curtain, match());
        wallpaper = new Wallpaper(this, surface, photo, curtain, "accueil");

        // Mode nuit : le paysage s'assombrit le soir
        voileNuit = new View(this);
        voileNuit.setBackgroundColor(Color.BLACK);
        voileNuit.setAlpha(0f);
        root.addView(voileNuit, match());

        // Assombrit le haut de l'image pour que l'heure reste lisible sur un ciel clair
        topScrim = new View(this);
        topScrim.setBackground(new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
                new int[] { 0x66000000, 0x00000000 }));
        root.addView(topScrim, new FrameLayout.LayoutParams(-1, u(150), Gravity.TOP));

        dim = new View(this);
        dim.setBackgroundColor(0xFF05080C);
        dim.setAlpha(0f);
        root.addView(dim, match());

        scroll = new VScroll(this);
        scroll.setVerticalScrollBarEnabled(false);
        scroll.setOverScrollMode(View.OVER_SCROLL_NEVER);
        scroll.setClipChildren(false);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setClipChildren(false);
        content.setClipToPadding(false);
        scroll.addView(content, new FrameLayout.LayoutParams(-1, -2));
        root.addView(scroll, match());

        // Dock : panneau translucide posé en bas de l'écran ; ses emplacements sont ceux des
        // colonnes de la grille, le panneau déborde un peu autour (nom de l'appli compris).
        int screenH = Math.min(getResources().getDisplayMetrics().widthPixels,
                getResources().getDisplayMetrics().heightPixels);
        int bord = u(DOCK_BORD);
        int dockH = u(DOCK_HAUT) + tileH + labelH;
        content.addView(new View(this), new LinearLayout.LayoutParams(-1, screenH - dockH - u(DOCK_BAS)));

        dockRow = new LinearLayout(this);
        dockRow.setOrientation(LinearLayout.HORIZONTAL);
        dockRow.setClipChildren(false);
        dockRow.setClipToPadding(false);
        dockRow.setPadding(bord, u(DOCK_HAUT), bord, 0);
        dockRow.setBackground(fondDock());
        LinearLayout.LayoutParams dl = new LinearLayout.LayoutParams(-1, dockH);
        dl.setMargins(side - bord, 0, side - bord, u(DOCK_BAS));
        content.addView(dockRow, dl);

        gridTitle = new TextView(this);
        gridTitle.setText("Toutes les applis");
        gridTitle.setTextSize(TypedValue.COMPLEX_UNIT_PX, u(19));
        gridTitle.setTextColor(0xD9FFFFFF);
        gridTitle.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        LinearLayout.LayoutParams tl = new LinearLayout.LayoutParams(-2, -2);
        tl.setMargins(side, u(40), 0, u(14));
        content.addView(gridTitle, tl);

        grid = new LinearLayout(this);
        grid.setOrientation(LinearLayout.VERTICAL);
        grid.setClipChildren(false);
        content.addView(grid, new LinearLayout.LayoutParams(-1, -2));
        content.addView(new View(this), new LinearLayout.LayoutParams(-1, screenH / 2));

        LinearLayout clockBox = new LinearLayout(this);
        clockBox.setOrientation(LinearLayout.VERTICAL);
        clockBox.setGravity(Gravity.END);
        TextClock time = new TextClock(this);
        time.setFormat24Hour("HH:mm");
        time.setFormat12Hour("HH:mm");
        styleOverlayText(time, 34, "sans-serif-light", 0xF2FFFFFF);
        TextClock date = new TextClock(this);
        date.setFormat24Hour("EEEE d MMMM");
        date.setFormat12Hour("EEEE d MMMM");
        styleOverlayText(date, 14, "sans-serif", 0xCCFFFFFF);
        clockBox.addView(time);
        clockBox.addView(date);
        meteo = new TextView(this);
        styleOverlayText(meteo, 14, "sans-serif", 0xCCFFFFFF);
        meteo.setVisibility(View.GONE);
        clockBox.addView(meteo);
        FrameLayout.LayoutParams cl = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.END);
        cl.setMargins(0, u(26), side, 0);
        root.addView(clockBox, cl);
        clock = clockBox;

        // Message d'accueil personnalisé (« {moment} Lolo » → « Bonsoir Lolo »)
        salut = new TextView(this);
        styleOverlayText(salut, 30, "sans-serif-light", 0xF2FFFFFF);
        salut.setVisibility(View.GONE);
        FrameLayout.LayoutParams sl = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.START);
        sl.setMargins(side, u(26), 0, 0);
        root.addView(salut, sl);
        racine = root;

        hint = new TextView(this);
        hint.setText("←  →  déplacer        OK  terminer");
        styleOverlayText(hint, 15, "sans-serif-medium", 0xFFFFFFFF);
        GradientDrawable pill = new GradientDrawable();
        pill.setCornerRadius(u(20));
        pill.setColor(0x99000000);
        hint.setBackground(pill);
        hint.setPadding(u(20), u(8), u(20), u(8));
        hint.setVisibility(View.GONE);
        FrameLayout.LayoutParams hl = new FrameLayout.LayoutParams(-2, -2,
                Gravity.TOP | Gravity.CENTER_HORIZONTAL);
        hl.topMargin = u(34);
        root.addView(hint, hl);

        scroll.setOnScrollChangeListener((v, x, y, oldX, oldY) -> applyScrollFx(y));
        root.getViewTreeObserver().addOnGlobalFocusChangeListener((oldFocus, newFocus) -> onFocusMoved(newFocus));
        setContentView(root);
    }

    private void renderAll() {
        rebuilding = true;
        renderDock();
        renderGrid();
        rebuilding = false;
    }

    /** Fond du dock : verre sombre à plat, coins arrondis, liseré clair. */
    private GradientDrawable fondDock() {
        GradientDrawable fond = new GradientDrawable();
        fond.setColor(0x8C0C0C10);
        fond.setCornerRadius(u(16));
        fond.setStroke(Math.max(1, u(0.5f)), 0x24FFFFFF);
        return fond;
    }

    private void renderDock() {
        dockRow.removeAllViews();
        dockTiles.clear();
        for (String key : dock) {
            if (dockTiles.size() == COLUMNS) break;  // le dock tient sur une rangée de la grille
            Apps.Entry e = apps.byKey.get(key);
            if (e == null) continue;  // appli désactivée ou pas encore disponible : sa place est gardée
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(tileW, -2);
            if (!dockTiles.isEmpty()) lp.leftMargin = gap;
            dockRow.addView(makeItem(e, -1, dockTiles.size()), lp);
        }
    }

    private void renderGrid() {
        grid.removeAllViews();
        LinearLayout rowView = null;
        for (int i = 0; i < apps.all.size(); i++) {
            if (i % COLUMNS == 0) {
                rowView = new LinearLayout(this);
                rowView.setOrientation(LinearLayout.HORIZONTAL);
                rowView.setClipChildren(false);
                rowView.setPadding(side, 0, 0, u(6));
                grid.addView(rowView, new LinearLayout.LayoutParams(-1, -2));
            }
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(tileW, -2);
            lp.rightMargin = gap;
            rowView.addView(makeItem(apps.all.get(i), i / COLUMNS, i), lp);
        }
    }

    private View makeItem(Apps.Entry entry, int row, int index) {
        LinearLayout item = new LinearLayout(this);
        item.setOrientation(LinearLayout.VERTICAL);
        item.setClipChildren(false);

        TextView label = new TextView(this);
        styleOverlayText(label, 14, "sans-serif-medium", 0xFFFFFFFF);
        label.setGravity(Gravity.CENTER_HORIZONTAL | Gravity.BOTTOM);
        label.setSingleLine(true);
        label.setEllipsize(TextUtils.TruncateAt.END);

        TileView tile = new TileView(this, entry, label, row, index, unite);
        tile.setOnClickListener(v -> launch(tile));
        tile.setOnLongClickListener(v -> {
            showMenu(tile);
            return true;
        });
        item.addView(tile, new LinearLayout.LayoutParams(tileW, tileH));
        item.addView(label, new LinearLayout.LayoutParams(-1, labelH));
        if (row < 0) dockTiles.add(tile);
        return item;
    }

    /** Texte sur le paysage ; taille en unités d'écran (proportionnelle à la largeur). */
    private void styleOverlayText(TextView t, float taille, String family, int color) {
        t.setTextSize(TypedValue.COMPLEX_UNIT_PX, taille * unite);
        t.setTextColor(color);
        t.setTypeface(Typeface.create(family, Typeface.NORMAL));
        t.setShadowLayer(5 * unite, 0, unite, 0x99000000);
    }

    // ---------------------------------------------------------------- personnalisation

    private void appliquerPersonnalisation() {
        perso = Personnalisation.charger(this);
        if (perso.infosSysteme != infosAffichees) {
            infosAffichees = perso.infosSysteme;
            reloadApps();
        }
        final Personnalisation p = perso;
        wallpaper.setFiltre(f -> fondAutorise(f, p));
        voileNuit.animate().alpha(perso.nuit() ? 0.35f : 0f).setDuration(600);
        if (perso.meteoVille == null) meteo.setVisibility(View.GONE);
        else actualiserMeteo();
        handler.removeCallbacks(nettoyer);
        if (perso.nettoyageMemoire) handler.postDelayed(nettoyer, 3000);
        String texte = Personnalisation.avecMoment(perso.salutation);
        salut.setText(texte);
        salut.setVisibility(texte.isEmpty() ? View.GONE : View.VISIBLE);
        // Écran de démarrage : au premier affichage après l'allumage de la box
        if (!demarrageMontre && perso.demarrage && SystemClock.elapsedRealtime() < 10 * 60_000) {
            demarrageMontre = true;
            montrerDemarrage();
        }
    }

    /** Vos fonds (perso-*) toujours ; paysages Lol'OS si autorisés ; la nuit, seulement les calmes. */
    static boolean fondAutorise(File f, Personnalisation p) {
        String n = f.getName().toLowerCase(Locale.ROOT);
        if (n.startsWith("perso-")) return true;
        if (!p.fondsLolos) return false;
        return !p.nuit() || n.contains("aurore") || n.contains("riviere") || n.contains("ocean") || n.contains("nuit");
    }

    /** Ferme les applis restées en arrière-plan (sauf TV en direct, Chromecast, YouTube et SmartTube). */
    private final Runnable nettoyer = () -> {
        ActivityManager am = getSystemService(ActivityManager.class);
        Set<String> epargnees = new HashSet<>(Arrays.asList(Apps.BBUI, "com.google.android.apps.mediashell",
                "com.google.android.youtube.tv", "org.smarttube.stable", getPackageName()));
        for (Apps.Entry e : apps.all) {
            if (e.packageName != null && !epargnees.contains(e.packageName)) am.killBackgroundProcesses(e.packageName);
        }
    };

    /** Météo du jour (Open-Meteo, sans compte), au plus une requête par demi-heure. */
    private void actualiserMeteo() {
        if (meteoTexte != null) {
            meteo.setText(meteoTexte);
            meteo.setVisibility(View.VISIBLE);
            if (System.currentTimeMillis() - meteoHeure < 30 * 60_000) return;
        }
        final double lat = perso.latitude, lon = perso.longitude;
        final String ville = perso.meteoVille;
        new Thread(() -> {
            try {
                URL url = new URL(String.format(Locale.ROOT,
                        "https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
                                + "&current=temperature_2m,weather_code,is_day&timezone=auto", lat, lon));
                HttpURLConnection c = (HttpURLConnection) url.openConnection();
                c.setConnectTimeout(8000);
                c.setReadTimeout(8000);
                JSONObject actuel = new JSONObject(lire(c.getInputStream())).getJSONObject("current");
                int code = actuel.getInt("weather_code");
                String texte = Meteo.symbole(code, actuel.optInt("is_day", 1) == 1) + "  "
                        + Math.round(actuel.getDouble("temperature_2m")) + "°  " + Meteo.libelle(code)
                        + (ville.isEmpty() ? "" : " · " + ville);
                handler.post(() -> {
                    meteoTexte = texte;
                    meteoHeure = System.currentTimeMillis();
                    if (perso.meteoVille == null) return;
                    meteo.setText(texte);
                    meteo.setVisibility(View.VISIBLE);
                });
            } catch (Exception ignored) {
                // pas de réseau : on réessaiera au prochain retour à l'accueil
            }
        }).start();
    }

    private static String lire(InputStream flux) throws java.io.IOException {
        ByteArrayOutputStream tampon = new ByteArrayOutputStream();
        byte[] morceau = new byte[4096];
        for (int n; (n = flux.read(morceau)) > 0; ) tampon.write(morceau, 0, n);
        flux.close();
        return tampon.toString(StandardCharsets.UTF_8.name());
    }

    /** Tuile « Infos système » : de quoi dépanner sans brancher d'ordinateur. */
    private void afficherInfos() {
        ActivityManager am = getSystemService(ActivityManager.class);
        ActivityManager.MemoryInfo memoire = new ActivityManager.MemoryInfo();
        am.getMemoryInfo(memoire);
        StatFs disque = new StatFs(Environment.getDataDirectory().getPath());
        String version;
        try {
            version = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (Exception e) {
            version = "?";
        }
        long minutes = SystemClock.elapsedRealtime() / 60_000;
        String texte = "Lol'OS " + version + "\n"
                + android.os.Build.MANUFACTURER + " " + android.os.Build.MODEL + " · Android " + android.os.Build.VERSION.RELEASE + "\n\n"
                + "Mémoire libre : " + memoire.availMem / 1_048_576 + " Mo sur " + memoire.totalMem / 1_048_576 + " Mo\n"
                + "Stockage libre : " + disque.getAvailableBytes() / 1_073_741_824 + " Go sur " + disque.getTotalBytes() / 1_073_741_824 + " Go\n"
                + "Réseau : " + adresseIp() + "\n"
                + "Température : " + temperature() + "\n"
                + "Allumée depuis : " + (minutes >= 60 ? minutes / 60 + " h " : "") + minutes % 60 + " min";
        menu = new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle("Infos système")
                .setMessage(texte)
                .setPositiveButton("Fermer", null)
                .create();
        menu.show();
    }

    private static String adresseIp() {
        try {
            for (NetworkInterface i : java.util.Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (i.isLoopback() || !i.isUp()) continue;
                for (InetAddress a : java.util.Collections.list(i.getInetAddresses())) {
                    if (a instanceof Inet4Address) return a.getHostAddress() + " (" + (i.getName().startsWith("wlan") ? "Wi-Fi" : i.getName()) + ")";
                }
            }
        } catch (Exception ignored) {
        }
        return "non connecté";
    }

    private static String temperature() {
        try {
            String brut = new String(Files.readAllBytes(Paths.get("/sys/class/thermal/thermal_zone0/temp"))).trim();
            return Math.round(Integer.parseInt(brut) / 1000.0) + " °C";
        } catch (Exception e) {
            return "indisponible";
        }
    }

    /** Logo + message en surimpression, puis fondu vers l'accueil. */
    private void montrerDemarrage() {
        if (perso == null) perso = Personnalisation.charger(this);
        LinearLayout ecran = new LinearLayout(this);
        ecran.setOrientation(LinearLayout.VERTICAL);
        ecran.setGravity(Gravity.CENTER);
        ecran.setBackgroundColor(0xFF09090B);
        ecran.setClickable(true);

        ImageView logo = new ImageView(this);
        logo.setScaleType(ImageView.ScaleType.FIT_CENTER);
        if (perso.logo != null) logo.setImageBitmap(BitmapFactory.decodeFile(perso.logo.getPath()));
        if (logo.getDrawable() == null) logo.setImageResource(R.drawable.marque);
        ecran.addView(logo, new LinearLayout.LayoutParams(u(180), u(180)));

        String texte = Personnalisation.avecMoment(perso.messageDemarrage);
        if (!texte.isEmpty()) {
            TextView message = new TextView(this);
            styleOverlayText(message, 26, "sans-serif-light", 0xF2FFFFFF);
            message.setGravity(Gravity.CENTER);
            message.setText(texte);
            LinearLayout.LayoutParams ml = new LinearLayout.LayoutParams(-2, -2);
            ml.topMargin = u(24);
            ecran.addView(message, ml);
        }
        racine.addView(ecran, match());
        logo.setAlpha(0f);
        logo.setScaleX(0.9f);
        logo.setScaleY(0.9f);
        logo.animate().alpha(1f).scaleX(1f).scaleY(1f).setDuration(600).start();
        ecran.postDelayed(() -> ecran.animate().alpha(0f).setDuration(700)
                .withEndAction(() -> racine.removeView(ecran)).start(), perso.dureeDemarrage);
    }

    // ---------------------------------------------------------------- navigation

    private void onFocusMoved(View v) {
        if (rebuilding || !(v instanceof TileView)) return;
        TileView t = (TileView) v;
        if (t.row < 0) {
            lastDockIndex = t.index;
            scroll.smoothScrollTo(0, 0);
        } else {
            scroll.smoothScrollTo(0, gridScrollFor(t.row));
        }
    }

    private int gridTop() {
        return gridTitle.getTop() - u(40);
    }

    private int gridScrollFor(int row) {
        int rowH = grid.getChildCount() > 0 ? grid.getChildAt(0).getHeight() : tileH + labelH;
        int y = gridTop() + Math.max(0, row - 1) * rowH;
        return Math.min(y, Math.max(0, content.getHeight() - scroll.getHeight()));
    }

    /** Plus on descend vers la grille, plus le paysage s'assombrit ; il se met en pause dessous. */
    private void applyScrollFx(int y) {
        int top = gridTop();
        float f = top <= 0 ? 0f : Math.min(1f, Math.max(0f, y / (float) top));
        dim.setAlpha(0.8f * f);
        if (!idle) {
            clock.setAlpha(1f - f);
            salut.setAlpha(1f - f);
        }
        if (f > 0.95f && !wallpaperPaused) {
            wallpaperPaused = true;
            wallpaper.setPaused(true);
        } else if (f < 0.5f && wallpaperPaused) {
            wallpaperPaused = false;
            wallpaper.setPaused(false);
        }
    }

    private void focusDock() {
        if (dockTiles.isEmpty()) {
            scroll.smoothScrollTo(0, 0);
            return;
        }
        dockTiles.get(Math.min(lastDockIndex, dockTiles.size() - 1)).requestFocus();
        scroll.smoothScrollTo(0, 0);
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        int code = e.getKeyCode();
        boolean down = e.getAction() == KeyEvent.ACTION_DOWN;
        if (code == KeyEvent.KEYCODE_VOLUME_UP || code == KeyEvent.KEYCODE_VOLUME_DOWN
                || code == KeyEvent.KEYCODE_VOLUME_MUTE || code == KeyEvent.KEYCODE_MUTE) {
            return super.dispatchKeyEvent(e);
        }
        scheduleIdle();
        if (idle) {
            if (down) {
                wake();
                swallowUp = true;
            }
            return true;
        }
        if (swallowUp && !down) {
            swallowUp = false;
            return true;
        }
        if (moving >= 0) {
            if (down) handleMoveKey(code);
            return true;
        }
        if (down && e.getRepeatCount() == 0) {
            switch (code) {
                case KeyEvent.KEYCODE_TV:
                    launchKey("@tv");
                    warmUpTv();
                    return true;
                case KeyEvent.KEYCODE_GUIDE:
                    launchKey("@guide");
                    return true;
                case KeyEvent.KEYCODE_MENU:
                    View f = getCurrentFocus();
                    if (f instanceof TileView) showMenu((TileView) f);
                    return true;
            }
        }
        if (code == KeyEvent.KEYCODE_BACK) {
            if (!down && scroll.getScrollY() > 0) focusDock();
            return true;  // l'accueil ne se ferme jamais
        }
        return super.dispatchKeyEvent(e);
    }

    // ---------------------------------------------------------------- veille de l'interface

    private void scheduleIdle() {
        handler.removeCallbacks(goIdle);
        handler.postDelayed(goIdle, IDLE_MS);
    }

    /** Sans action pendant une minute, l'interface s'efface et laisse le paysage. */
    private void enterIdle() {
        if (idle || moving >= 0 || scroll.getScrollY() > 0 || (menu != null && menu.isShowing())) {
            if (!idle) scheduleIdle();
            return;
        }
        idle = true;
        for (View v : new View[] { scroll, clock, salut, topScrim }) v.animate().alpha(0f).setDuration(1500).start();
    }

    private void wake() {
        idle = false;
        for (View v : new View[] { scroll, clock, salut, topScrim }) v.animate().alpha(1f).setDuration(250).start();
    }

    // ---------------------------------------------------------------- actions

    private void launch(TileView t) {
        if ("@reboot".equals(t.entry.key)) {
            confirmerRedemarrage(t.entry);
            return;
        }
        if ("@infos".equals(t.entry.key)) {
            afficherInfos();
            return;
        }
        if ("@veille".equals(t.entry.key)) {
            sendBroadcast(t.entry.intent);
            return;
        }
        try {
            ActivityOptions anim = ActivityOptions.makeScaleUpAnimation(t, 0, 0, t.getWidth(), t.getHeight());
            startActivity(t.entry.intent, anim.toBundle());
            if ("@tv".equals(t.entry.key)) warmUpTv();
        } catch (RuntimeException ex) {
            Toast.makeText(this, "Impossible d'ouvrir " + t.entry.label, Toast.LENGTH_SHORT).show();
        }
    }

    /**
     * Premier passage sur la TV depuis le démarrage : l'interface Bbox se charge et affiche
     * son accueil au lieu du direct. On renvoie la demande une fois qu'elle est prête
     * (sans effet si le direct est déjà affiché ; annulé si on revient à l'accueil).
     */
    private void warmUpTv() {
        if (tvWarm) return;
        tvWarm = true;
        handler.postDelayed(resendTv, 7_000);
        handler.postDelayed(resendTv, 12_000);
        handler.postDelayed(resendTv, 18_000);
    }

    /**
     * Redémarrage par le service d'alimentation de la Bbox (redémarrage « quiescent » : la box
     * revient en veille). Si le message n'est plus accepté, on ouvre « À propos » des Réglages,
     * qui contient « Redémarrer ».
     */
    private void confirmerRedemarrage(Apps.Entry entry) {
        menu = new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle("Redémarrer la box ?")
                .setMessage("La box redémarre puis reste en veille : rallumez-la avec la télécommande.")
                .setPositiveButton("Redémarrer", (d, w) -> {
                    try {
                        sendBroadcast(entry.intent);
                        handler.postDelayed(() -> startActivity(new Intent(Settings.ACTION_DEVICE_INFO_SETTINGS)
                                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)), 4000);
                    } catch (RuntimeException ex) {
                        startActivity(new Intent(Settings.ACTION_DEVICE_INFO_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                    }
                })
                .setNegativeButton("Annuler", null)
                .create();
        menu.show();
    }

    private void launchKey(String key) {
        Apps.Entry e = apps.byKey.get(key);
        if (e == null) return;
        try {
            startActivity(e.intent);
        } catch (RuntimeException ignored) {
        }
    }

    private void showMenu(TileView t) {
        List<String> labels = new ArrayList<>();
        List<Runnable> actions = new ArrayList<>();
        boolean docked = visibleDock().contains(t.entry.key);
        if (t.row < 0) {
            labels.add("Déplacer");
            actions.add(() -> startMove(t.index));
        }
        if (docked) {
            labels.add("Retirer du dock");
            actions.add(() -> removeFromDock(t));
        } else {
            labels.add("Ajouter au dock");
            actions.add(() -> addToDock(t.entry.key));
        }
        if (!t.entry.isSpecial()) {
            labels.add("Infos sur l'appli");
            actions.add(() -> openAppInfo(t.entry.packageName));
        }
        menu = new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle(t.entry.label)
                .setItems(labels.toArray(new CharSequence[0]), (dialog, which) -> actions.get(which).run())
                .create();
        menu.show();
    }

    private List<String> visibleDock() {
        List<String> keys = new ArrayList<>();
        for (TileView t : dockTiles) keys.add(t.entry.key);
        return keys;
    }

    /**
     * Nouveau contenu du dock affiché. Les applis du dock absentes de l'écran (désactivées, ou sur
     * un stockage pas encore monté) gardent leur place et reviendront quand elles seront visibles.
     */
    private void setDock(List<String> keys) {
        List<String> affichees = visibleDock();
        List<String> tout = new ArrayList<>();
        Iterator<String> nouvelles = keys.iterator();
        for (String k : dock) {
            if (!affichees.contains(k)) tout.add(k);
            else if (nouvelles.hasNext()) tout.add(nouvelles.next());
        }
        while (nouvelles.hasNext()) tout.add(nouvelles.next());
        dock.clear();
        dock.addAll(tout);
        prefs.edit().putString("dock", TextUtils.join("\n", dock)).apply();
        rebuilding = true;
        renderDock();
        rebuilding = false;
    }

    private void loadDock() {
        String saved = prefs.getString("dock", null);
        dock.clear();
        if (saved == null) {
            Collections.addAll(dock, DEFAULT_DOCK);
        } else {
            for (String k : saved.split("\n")) if (!k.isEmpty()) dock.add(k);
        }
    }

    private void addToDock(String key) {
        List<String> keys = visibleDock();
        if (keys.size() >= COLUMNS) {
            Toast.makeText(this, "Le dock est plein (" + COLUMNS + " applis) : retirez-en une d'abord",
                    Toast.LENGTH_LONG).show();
            return;
        }
        keys.add(key);
        setDock(keys);
        lastDockIndex = keys.size() - 1;
        focusDock();
    }

    private void removeFromDock(TileView t) {
        List<String> keys = visibleDock();
        keys.remove(t.entry.key);
        boolean wasFocused = t.hasFocus();
        setDock(keys);
        if (wasFocused) {
            lastDockIndex = Math.max(0, t.index - 1);
            focusDock();
        }
    }

    private void startMove(int index) {
        if (index >= dockTiles.size()) return;
        moving = index;
        TileView t = dockTiles.get(index);
        t.requestFocus();
        t.setWiggling(true);
        hint.setVisibility(View.VISIBLE);
    }

    private void handleMoveKey(int code) {
        switch (code) {
            case KeyEvent.KEYCODE_DPAD_LEFT:
                moveBy(-1);
                break;
            case KeyEvent.KEYCODE_DPAD_RIGHT:
                moveBy(1);
                break;
            case KeyEvent.KEYCODE_DPAD_CENTER:
            case KeyEvent.KEYCODE_ENTER:
            case KeyEvent.KEYCODE_BACK:
            case KeyEvent.KEYCODE_DPAD_UP:
            case KeyEvent.KEYCODE_DPAD_DOWN:
                endMove();
                swallowUp = true;
                break;
        }
    }

    private void moveBy(int delta) {
        List<String> keys = visibleDock();
        int to = moving + delta;
        if (to < 0 || to >= keys.size()) return;
        Collections.swap(keys, moving, to);
        setDock(keys);
        moving = to;
        lastDockIndex = to;
        TileView t = dockTiles.get(to);
        t.requestFocus();
        t.setWiggling(true);
    }

    private void endMove() {
        if (moving >= 0 && moving < dockTiles.size()) dockTiles.get(moving).setWiggling(false);
        moving = -1;
        hint.setVisibility(View.GONE);
    }

    private void openAppInfo(String pkg) {
        try {
            startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.fromParts("package", pkg, null)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        } catch (RuntimeException ignored) {
        }
    }

    /** Une appli a été installée, activée ou désactivée : on recharge. */
    private void reloadApps() {
        View f = getCurrentFocus();
        boolean inDock = !(f instanceof TileView) || ((TileView) f).row < 0;
        apps = Apps.load(this, infosAffichees);
        renderAll();
        if (inDock) {
            focusDock();
        } else if (grid.getChildCount() > 0) {
            LinearLayout firstRow = (LinearLayout) grid.getChildAt(0);
            ((LinearLayout) firstRow.getChildAt(0)).getChildAt(0).requestFocus();
        }
    }

    // ---------------------------------------------------------------- utilitaires

    /** Unités d'écran → pixels (1 unité = 1/960 de la largeur de l'écran). */
    private int u(float v) {
        return Math.round(v * unite);
    }

    private static FrameLayout.LayoutParams match() {
        return new FrameLayout.LayoutParams(-1, -1);
    }

    /** ScrollView vertical dont on pilote le défilement soi-même (focus → position). */
    static final class VScroll extends ScrollView {
        VScroll(Context context) {
            super(context);
        }

        @Override
        protected int computeScrollDeltaToGetChildRectOnScreen(Rect rect) {
            return 0;
        }

        @Override
        public boolean executeKeyEvent(KeyEvent event) {
            return false;
        }
    }
}
