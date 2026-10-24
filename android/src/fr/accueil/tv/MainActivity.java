package fr.accueil.tv;

import android.app.Activity;
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
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.provider.Settings;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.SurfaceView;
import android.view.View;
import android.widget.FrameLayout;
import android.graphics.BitmapFactory;
import android.widget.HorizontalScrollView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextClock;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Écran d'accueil minimal façon Apple TV : paysage animé en fond, dock d'applis favorites,
 * grille « Toutes les applis » sous le dock (flèche bas).
 * Appui long sur OK (ou touche Menu) : déplacer / retirer / ajouter au dock.
 */
public class MainActivity extends Activity {
    private static final int TILE_W = 160, TILE_H = 90, GAP = 26, SIDE = 40, LABEL_H = 34;
    private static final int COLUMNS = 5;
    private static final long IDLE_MS = 60_000;
    private static final long ROTATE_MS = 8 * 60_000;
    private static final String[] DEFAULT_DOCK = {
            "@tv", "com.nuvio.tv", "tv.arte.plus7.bouygues", "com.google.android.youtube.tv", "@guide" };

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final List<String> dock = new ArrayList<>();
    private final List<TileView> dockTiles = new ArrayList<>();

    private SharedPreferences prefs;
    private Apps apps;
    private Wallpaper wallpaper;
    private VScroll scroll;
    private LinearLayout content, dockRow, grid;
    private TextView gridTitle, hint;
    private View dim, scrim, topScrim, clock;
    private FrameLayout racine;
    private TextView salut;
    private Personnalisation perso;
    private boolean demarrageMontre;

    /** Lancé par l'installateur pour prévisualiser l'écran de démarrage. */
    static final String APERCU_DEMARRAGE = "fr.accueil.tv.APERCU_DEMARRAGE";
    private AlertDialog menu;

    private int lastDockIndex;
    private int moving = -1;  // index de la tuile du dock en cours de déplacement
    private boolean idle, swallowUp, resumedOnce, wallpaperPaused, rebuilding, tvWarm;

    private final Runnable goIdle = this::enterIdle;
    /** L'interface Bbox ignore la demande de direct tant qu'elle démarre : on la renvoie. */
    private final Runnable resendTv = () -> launchKey("@tv");
    private final Runnable reload = this::reloadApps;
    private final Runnable rotate = new Runnable() {
        @Override
        public void run() {
            wallpaper.next();
            handler.postDelayed(this, ROTATE_MS);
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
        loadDock();
        buildUi();
        apps = Apps.load(this);
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
    }

    @Override
    protected void onDestroy() {
        unregisterReceiver(packagesChanged);
        handler.removeCallbacksAndMessages(null);
        super.onDestroy();
    }

    @Override
    protected void onResume() {
        super.onResume();
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
        handler.removeCallbacks(rotate);
        handler.removeCallbacks(goIdle);
        wallpaper.stop();
        if (moving >= 0) endMove();
        if (menu != null) menu.dismiss();
        idle = false;
        for (View v : new View[] { scroll, clock, salut, scrim, topScrim }) {
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

    private void buildUi() {
        FrameLayout root = new FrameLayout(this);

        SurfaceView surface = new SurfaceView(this);
        View curtain = new View(this);
        curtain.setBackgroundColor(Color.BLACK);
        root.addView(surface, match());
        root.addView(curtain, match());
        wallpaper = new Wallpaper(this, surface, curtain, "accueil");

        scrim = new View(this);
        scrim.setBackground(new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
                new int[] { 0x00000000, 0x00000000, 0x26000000, 0xA6000000 }));
        root.addView(scrim, match());

        // Assombrit le haut de l'image pour que l'heure reste lisible sur un ciel clair
        topScrim = new View(this);
        topScrim.setBackground(new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
                new int[] { 0x66000000, 0x00000000 }));
        root.addView(topScrim, new FrameLayout.LayoutParams(-1, dp(150), Gravity.TOP));

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

        int screenH = getResources().getDisplayMetrics().heightPixels;
        content.addView(new View(this),
                new LinearLayout.LayoutParams(-1, screenH - dp(TILE_H + LABEL_H + 24 + 34)));

        HorizontalScrollView dockScroll = new HorizontalScrollView(this);
        dockScroll.setHorizontalScrollBarEnabled(false);
        dockScroll.setOverScrollMode(View.OVER_SCROLL_NEVER);
        dockScroll.setClipChildren(false);
        dockScroll.setClipToPadding(false);
        dockScroll.setPadding(dp(SIDE), dp(12), dp(SIDE), dp(12));
        dockRow = new LinearLayout(this);
        dockRow.setOrientation(LinearLayout.HORIZONTAL);
        dockRow.setClipChildren(false);
        dockScroll.addView(dockRow, new FrameLayout.LayoutParams(-2, -2));
        content.addView(dockScroll, new LinearLayout.LayoutParams(-1, -2));

        gridTitle = new TextView(this);
        gridTitle.setText("Toutes les applis");
        gridTitle.setTextSize(19);
        gridTitle.setTextColor(0xD9FFFFFF);
        gridTitle.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        LinearLayout.LayoutParams tl = new LinearLayout.LayoutParams(-2, -2);
        tl.setMargins(dp(SIDE), dp(56), 0, dp(14));
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
        FrameLayout.LayoutParams cl = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.END);
        cl.setMargins(0, dp(26), dp(SIDE), 0);
        root.addView(clockBox, cl);
        clock = clockBox;

        // Message d'accueil personnalisé (« {moment} Lolo » → « Bonsoir Lolo »)
        salut = new TextView(this);
        styleOverlayText(salut, 30, "sans-serif-light", 0xF2FFFFFF);
        salut.setVisibility(View.GONE);
        FrameLayout.LayoutParams sl = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.START);
        sl.setMargins(dp(SIDE), dp(26), 0, 0);
        root.addView(salut, sl);
        racine = root;

        hint = new TextView(this);
        hint.setText("←  →  déplacer        OK  terminer");
        styleOverlayText(hint, 15, "sans-serif-medium", 0xFFFFFFFF);
        GradientDrawable pill = new GradientDrawable();
        pill.setCornerRadius(dp(20));
        pill.setColor(0x99000000);
        hint.setBackground(pill);
        hint.setPadding(dp(20), dp(8), dp(20), dp(8));
        hint.setVisibility(View.GONE);
        FrameLayout.LayoutParams hl = new FrameLayout.LayoutParams(-2, -2,
                Gravity.TOP | Gravity.CENTER_HORIZONTAL);
        hl.topMargin = dp(34);
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

    private void renderDock() {
        dockRow.removeAllViews();
        dockTiles.clear();
        for (String key : dock) {
            Apps.Entry e = apps.byKey.get(key);
            if (e == null) continue;  // appli désactivée : sa place est gardée
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(TILE_W), -2);
            lp.rightMargin = dp(GAP);
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
                rowView.setPadding(dp(SIDE), 0, 0, dp(6));
                grid.addView(rowView, new LinearLayout.LayoutParams(-1, -2));
            }
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(TILE_W), -2);
            lp.rightMargin = dp(GAP);
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

        TileView tile = new TileView(this, entry, label, row, index);
        tile.setOnClickListener(v -> launch(tile));
        tile.setOnLongClickListener(v -> {
            showMenu(tile);
            return true;
        });
        item.addView(tile, new LinearLayout.LayoutParams(dp(TILE_W), dp(TILE_H)));
        item.addView(label, new LinearLayout.LayoutParams(-1, dp(LABEL_H)));
        if (row < 0) dockTiles.add(tile);
        return item;
    }

    private void styleOverlayText(TextView t, float sp, String family, int color) {
        t.setTextSize(sp);
        t.setTextColor(color);
        t.setTypeface(Typeface.create(family, Typeface.NORMAL));
        t.setShadowLayer(10, 0, 2, 0x99000000);
    }

    // ---------------------------------------------------------------- personnalisation

    private void appliquerPersonnalisation() {
        perso = Personnalisation.charger(this);
        String texte = Personnalisation.avecMoment(perso.salutation);
        salut.setText(texte);
        salut.setVisibility(texte.isEmpty() ? View.GONE : View.VISIBLE);
        // Écran de démarrage : au premier affichage après l'allumage de la box
        if (!demarrageMontre && perso.demarrage && SystemClock.elapsedRealtime() < 10 * 60_000) {
            demarrageMontre = true;
            montrerDemarrage();
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
        ecran.addView(logo, new LinearLayout.LayoutParams(dp(180), dp(180)));

        String texte = Personnalisation.avecMoment(perso.messageDemarrage);
        if (!texte.isEmpty()) {
            TextView message = new TextView(this);
            styleOverlayText(message, 26, "sans-serif-light", 0xF2FFFFFF);
            message.setGravity(Gravity.CENTER);
            message.setText(texte);
            LinearLayout.LayoutParams ml = new LinearLayout.LayoutParams(-2, -2);
            ml.topMargin = dp(24);
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
        return gridTitle.getTop() - dp(40);
    }

    private int gridScrollFor(int row) {
        int rowH = grid.getChildCount() > 0 ? grid.getChildAt(0).getHeight() : dp(TILE_H + LABEL_H);
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
        for (View v : new View[] { scroll, clock, salut, scrim, topScrim }) v.animate().alpha(0f).setDuration(1500).start();
    }

    private void wake() {
        idle = false;
        for (View v : new View[] { scroll, clock, salut, scrim, topScrim }) v.animate().alpha(1f).setDuration(250).start();
    }

    // ---------------------------------------------------------------- actions

    private void launch(TileView t) {
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

    private void setDock(List<String> keys) {
        dock.clear();
        dock.addAll(keys);
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
        apps = Apps.load(this);
        renderAll();
        if (inDock) {
            focusDock();
        } else if (grid.getChildCount() > 0) {
            LinearLayout firstRow = (LinearLayout) grid.getChildAt(0);
            ((LinearLayout) firstRow.getChildAt(0)).getChildAt(0).requestFocus();
        }
    }

    // ---------------------------------------------------------------- utilitaires

    private int dp(float v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
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
