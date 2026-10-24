package fr.accueil.tv;

import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Handler;
import android.os.Looper;
import android.service.dreams.DreamService;
import android.view.Gravity;
import android.view.SurfaceView;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.TextClock;

/** Écran de veille « Paysages » : les mêmes vidéos que l'accueil, avec l'heure. */
public class Screensaver extends DreamService {
    private static final long ROTATE_MS = 3 * 60_000;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private Wallpaper wallpaper;

    private final Runnable rotate = new Runnable() {
        @Override
        public void run() {
            wallpaper.next();
            handler.postDelayed(this, ROTATE_MS);
        }
    };

    @Override
    public void onAttachedToWindow() {
        super.onAttachedToWindow();
        setInteractive(false);
        setFullscreen(true);

        float d = getResources().getDisplayMetrics().density;
        FrameLayout root = new FrameLayout(this);
        SurfaceView surface = new SurfaceView(this);
        ImageView photo = new ImageView(this);
        View curtain = new View(this);
        curtain.setBackgroundColor(Color.BLACK);
        root.addView(surface, new FrameLayout.LayoutParams(-1, -1));
        root.addView(photo, new FrameLayout.LayoutParams(-1, -1));
        root.addView(curtain, new FrameLayout.LayoutParams(-1, -1));

        TextClock clock = new TextClock(this);
        clock.setFormat24Hour("HH:mm");
        clock.setFormat12Hour("HH:mm");
        clock.setTextColor(0xE6FFFFFF);
        clock.setTextSize(30);
        clock.setTypeface(Typeface.create("sans-serif-light", Typeface.NORMAL));
        clock.setShadowLayer(12, 0, 2, 0x99000000);
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.END);
        lp.setMargins(0, 0, (int) (48 * d), (int) (36 * d));
        root.addView(clock, lp);

        setContentView(root);
        wallpaper = new Wallpaper(this, surface, photo, curtain, "ecran_veille");
        Personnalisation perso = Personnalisation.charger(this);
        wallpaper.setFiltre(f -> MainActivity.fondAutorise(f, perso));
    }

    @Override
    public void onDreamingStarted() {
        super.onDreamingStarted();
        wallpaper.start(true);
        handler.postDelayed(rotate, ROTATE_MS);
    }

    @Override
    public void onDreamingStopped() {
        handler.removeCallbacks(rotate);
        wallpaper.stop();
        super.onDreamingStopped();
    }
}
